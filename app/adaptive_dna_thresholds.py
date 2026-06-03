from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import storage


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _clamp(value: float, floor: float, ceiling: float) -> float:
    return max(floor, min(ceiling, value))


def _blend(base_value: float, hint_value: float, influence: float) -> float:
    return (base_value * (1.0 - influence)) + (hint_value * influence)


@dataclass(slots=True)
class AdaptiveThresholds:
    enabled: bool
    profile_key: str | None
    sample_count: int
    win_rate: float
    min_volume_ratio: float
    min_velocity_score: float
    min_directional_volume_score: float
    min_parlayan_score: float
    min_pre_pump_score: float
    source: str
    feature_stats: dict[str, Any]
    recommendations: dict[str, Any]
    confidence_factor: float = 0.0
    influence: float = 0.0
    full_weight_samples: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "profile_key": self.profile_key,
            "sample_count": self.sample_count,
            "win_rate": self.win_rate,
            "min_volume_ratio": self.min_volume_ratio,
            "min_velocity_score": self.min_velocity_score,
            "min_directional_volume_score": self.min_directional_volume_score,
            "min_parlayan_score": self.min_parlayan_score,
            "min_pre_pump_score": self.min_pre_pump_score,
            "source": self.source,
            "feature_stats": self.feature_stats,
            "recommendations": self.recommendations,
            "confidence_factor": self.confidence_factor,
            "influence": self.influence,
            "full_weight_samples": self.full_weight_samples,
        }


class AdaptiveDNAThresholds:
    """
    V4.6.1 Adaptive DNA Thresholds.

    V4.6'da Market DNA eşikleri çok az örnekle fazla gevşeyebiliyordu.
    V4.6.1 bunu düzeltir:
    - Profili araştırma için 8 sample'dan itibaren kullanabilir.
    - Ama trade eşiklerini tam gevşetmek için daha yüksek full_weight_samples ister.
    - Sample sayısı düşükken DNA etkisi kontrollü bir yüzdeyle harmanlanır.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def cfg(self) -> dict[str, Any]:
        return self.config.get("adaptive_dna_thresholds", {})

    def resolve(self, base: dict[str, float], market_regime: str | None = None) -> AdaptiveThresholds:
        if not self.cfg.get("enabled", True):
            return self._disabled(base, "disabled")

        min_samples = int(self.cfg.get("min_samples", self.config.get("market_dna", {}).get("min_samples", 8)))
        full_weight_samples = int(self.cfg.get("full_weight_samples", 50))
        max_low_sample_influence = float(self.cfg.get("max_low_sample_influence", 0.25))
        max_influence = float(self.cfg.get("max_influence", 0.85))
        target_threshold = float(self.cfg.get("target_threshold_pct", 10))
        target_horizon = int(self.cfg.get("target_horizon_minutes", 240))
        min_win_rate = float(self.cfg.get("min_win_rate", 55))

        profile = storage.get_best_market_dna_profile(
            threshold_pct=target_threshold,
            horizon_minutes=target_horizon,
            market_regime=market_regime,
            min_samples=min_samples,
        )
        if not profile:
            return self._disabled(base, "no_profile")

        sample_count = int(_f(profile.get("sample_count")))
        win_rate = _f(profile.get("win_rate"))
        if sample_count < min_samples or win_rate < min_win_rate:
            return self._disabled(base, "profile_not_trusted")

        rec = profile.get("recommendations") or {}
        stats = profile.get("feature_stats") or {}

        volume_hint = _f(rec.get("min_volume_ratio_hint"), base.get("min_volume_ratio", 0.75))
        velocity_hint = _f(rec.get("min_velocity_score_hint"), base.get("min_velocity_score", 18))
        directional_hint = _f(rec.get("min_directional_volume_score_hint"), base.get("min_directional_volume_score", 52))
        parlayan_hint = _f(stats.get("parlayan_score"), base.get("min_parlayan_score", 42)) * float(self.cfg.get("parlayan_score_multiplier", 1.10))
        pre_pump_hint = _f(stats.get("pre_pump_score"), base.get("min_pre_pump_score", 62)) * float(self.cfg.get("pre_pump_multiplier", 0.95))

        if full_weight_samples <= min_samples:
            confidence_factor = 1.0
        else:
            confidence_factor = _clamp((sample_count - min_samples) / (full_weight_samples - min_samples), 0.0, 1.0)

        if sample_count < full_weight_samples:
            influence = min(max_low_sample_influence, max_influence) * max(0.35, confidence_factor)
        else:
            influence = max_influence

        base_volume = float(base.get("min_volume_ratio", 0.75))
        base_velocity = float(base.get("min_velocity_score", 58.0))
        base_directional = float(base.get("min_directional_volume_score", 52.0))
        base_parlayan = float(base.get("min_parlayan_score", 48.0))
        base_pre = float(base.get("min_pre_pump_score", 64.0))

        resolved_volume = _blend(base_volume, volume_hint, influence)
        resolved_velocity = _blend(base_velocity, velocity_hint, influence)
        resolved_directional = _blend(base_directional, directional_hint, influence)
        resolved_parlayan = _blend(base_parlayan, parlayan_hint, influence)
        resolved_pre = _blend(base_pre, pre_pump_hint, influence)

        return AdaptiveThresholds(
            enabled=True,
            profile_key=str(profile.get("profile_key") or ""),
            sample_count=sample_count,
            win_rate=round(win_rate, 4),
            min_volume_ratio=round(_clamp(resolved_volume, float(self.cfg.get("volume_floor", 0.58)), float(self.cfg.get("volume_ceiling", base_volume))), 4),
            min_velocity_score=round(_clamp(resolved_velocity, float(self.cfg.get("velocity_floor", 18.0)), float(self.cfg.get("velocity_ceiling", base_velocity))), 4),
            min_directional_volume_score=round(_clamp(resolved_directional, float(self.cfg.get("directional_floor", 38.0)), float(self.cfg.get("directional_ceiling", base_directional))), 4),
            min_parlayan_score=round(_clamp(resolved_parlayan, float(self.cfg.get("parlayan_floor", 28.0)), float(self.cfg.get("parlayan_ceiling", base_parlayan))), 4),
            min_pre_pump_score=round(_clamp(resolved_pre, float(self.cfg.get("pre_pump_floor", 50.0)), float(self.cfg.get("pre_pump_ceiling", base_pre))), 4),
            source="market_dna_profile_weighted_v461",
            feature_stats=dict(stats),
            recommendations=dict(rec),
            confidence_factor=round(confidence_factor, 4),
            influence=round(influence, 4),
            full_weight_samples=full_weight_samples,
        )

    def _disabled(self, base: dict[str, float], source: str) -> AdaptiveThresholds:
        return AdaptiveThresholds(
            enabled=False,
            profile_key=None,
            sample_count=0,
            win_rate=0.0,
            min_volume_ratio=float(base.get("min_volume_ratio", 0.75)),
            min_velocity_score=float(base.get("min_velocity_score", 18)),
            min_directional_volume_score=float(base.get("min_directional_volume_score", 52)),
            min_parlayan_score=float(base.get("min_parlayan_score", 42)),
            min_pre_pump_score=float(base.get("min_pre_pump_score", 62)),
            source=source,
            feature_stats={},
            recommendations={},
            confidence_factor=0.0,
            influence=0.0,
            full_weight_samples=int(self.cfg.get("full_weight_samples", 50)),
        )
