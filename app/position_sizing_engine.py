from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


@dataclass(slots=True)
class PositionSizeDecision:
    quote_size: float
    multiplier: float
    confidence: float
    reason: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_size": self.quote_size,
            "multiplier": self.multiplier,
            "confidence": self.confidence,
            "reason": self.reason,
            "details": self.details,
        }


class PositionSizingEngine:
    """
    V4.6 confidence-based paper position sizing.

    Discovery sinyalleri kazananları erken test eder ama küçük boyutla girer.
    Klasik teyitli profiller daha yüksek çarpan alır.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def cfg(self) -> dict[str, Any]:
        return self.config.get("position_sizing", {})

    def size(self, base_quote_size: float, context: dict[str, Any]) -> PositionSizeDecision:
        if not self.cfg.get("enabled", True):
            return PositionSizeDecision(base_quote_size, 1.0, 1.0, "position_sizing_disabled", {})

        profile = str(context.get("entry_profile") or "")
        confidence = _f(context.get("position_confidence"), _f((context.get("discovery") or {}).get("confidence"), 0.60))
        pre_pump = _f(context.get("pre_pump_score"))
        volume_ratio = _f(context.get("volume_ratio"), 1.0)
        metrics = context.get("professional_metrics") or {}
        directional = _f(metrics.get("directional_volume_score"), _f(context.get("directional_volume_score"), 50.0))
        velocity = _f(metrics.get("velocity_score"), 0.0)

        multipliers = self.cfg.get("profile_multipliers", {})
        multiplier = _f(multipliers.get(profile), _f(self.cfg.get("default_multiplier"), 0.50))

        if profile.startswith("DNA_"):
            multiplier = min(multiplier, _f(self.cfg.get("max_discovery_multiplier"), 0.35))
        if pre_pump >= _f(self.cfg.get("boost_pre_pump_score"), 82.0) and directional >= _f(self.cfg.get("boost_directional_score"), 75.0):
            multiplier += _f(self.cfg.get("high_quality_boost"), 0.10)
        if velocity >= _f(self.cfg.get("velocity_boost_score"), 55.0) and volume_ratio >= _f(self.cfg.get("volume_boost_ratio"), 2.0):
            multiplier += _f(self.cfg.get("velocity_volume_boost"), 0.10)

        multiplier *= max(_f(self.cfg.get("min_confidence_multiplier"), 0.60), min(1.0, confidence))
        multiplier = max(_f(self.cfg.get("min_multiplier"), 0.20), min(_f(self.cfg.get("max_multiplier"), 1.0), multiplier))

        min_quote = _f(self.cfg.get("min_quote_size_usdt"), 15.0)
        max_quote = _f(self.cfg.get("max_quote_size_usdt"), self.config.get("risk", {}).get("max_position_usdt", base_quote_size))
        quote_size = round(max(min_quote, min(max_quote, base_quote_size * multiplier)), 4)

        return PositionSizeDecision(
            quote_size=quote_size,
            multiplier=round(multiplier, 4),
            confidence=round(confidence, 4),
            reason="profile_confidence_size",
            details={
                "entry_profile": profile,
                "base_quote_size": base_quote_size,
                "pre_pump_score": pre_pump,
                "volume_ratio": volume_ratio,
                "directional_volume_score": directional,
                "velocity_score": velocity,
            },
        )
