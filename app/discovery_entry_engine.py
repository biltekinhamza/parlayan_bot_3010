from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import MarketFeature
from .stable_asset_filter import evaluate_symbol


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


@dataclass(slots=True)
class DiscoveryDecision:
    allowed: bool
    entry_profile: str | None
    confidence: float
    reasons: list[str]
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "entry_profile": self.entry_profile,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "details": self.details,
        }


class DiscoveryEntryEngine:
    """
    V4.6 Discovery Entry Engine.

    Amaç: Pattern Memory / Market DNA'nın gösterdiği erken fırsatları
    tam pozisyonla kovalamadan, küçük ve kontrollü paper pozisyonlarla test etmek.

    Bu motor risk kapılarını kaldırmaz:
    - FOMO/DANGER/DISTRIBUTION yine engellenir.
    - Slippage/spread/fake-pump sınırları korunur.
    - Pozisyon boyutu PositionSizingEngine tarafından küçültülür.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def cfg(self) -> dict[str, Any]:
        return self.config.get("discovery_entry", {})

    def evaluate(self, feature: MarketFeature, adaptive: dict[str, Any], has_open_trade: bool = False) -> DiscoveryDecision:
        if not self.cfg.get("enabled", True):
            return self._reject(["discovery disabled"], {"adaptive": adaptive})

        stable_decision = evaluate_symbol(feature.symbol, self.config)
        if stable_decision.blocked:
            return self._reject([stable_decision.reason], {"adaptive": adaptive, "stable_asset_filter": stable_decision.as_dict()})

        extra = feature.extra or {}
        phase = str(extra.get("market_phase") or "WATCH")
        profile = str(extra.get("v4_profile") or "WATCH")
        allowed_phases = set(self.cfg.get("allowed_phases", [
            "RECOVERY_COMPRESSION",
            "VOLUME_WAKEUP",
            "EARLY_MOMENTUM",
            "ACCUMULATION_BREAKOUT",
            "MOMENTUM_EXPANSION",
        ]))
        blocked_phases = set(self.cfg.get("blocked_phases", ["FOMO", "LATE_FOMO", "DANGER", "DISTRIBUTION"]))

        if has_open_trade:
            return self._reject(["bu sembolde zaten açık işlem var"], {"phase": phase, "profile": profile, "adaptive": adaptive})
        if phase in blocked_phases:
            return self._reject([f"discovery fazı yasak: {phase}"], {"phase": phase, "profile": profile, "adaptive": adaptive})
        if phase not in allowed_phases:
            return self._reject([f"discovery fazı uygun değil: {phase}"], {"phase": phase, "profile": profile, "adaptive": adaptive})
        if feature.rsi is None:
            return self._reject(["RSI yok"], {"phase": phase, "profile": profile, "adaptive": adaptive})

        rsi = float(feature.rsi)
        change_5m = _f(feature.price_change_5m_pct)
        change_15m = _f(feature.price_change_15m_pct)
        change_30m = _f(feature.price_change_30m_pct)
        change_24h = _f(feature.price_change_24h_pct)
        volume_ratio = _f(feature.volume_ratio, 1.0)
        pre_pump_score = _f(extra.get("pre_pump_score"))
        velocity_score = _f(extra.get("velocity_score"))
        fast_alarm_score = _f(extra.get("fast_alarm_score"))
        directional_score = _f(extra.get("directional_volume_score"), 50.0)
        up_volume = _f(extra.get("up_volume_ratio"), 0.5)
        score_delta = _f(extra.get("score_delta"))
        momentum_acc = _f(extra.get("momentum_acceleration"))
        close_location = _f(extra.get("close_location_score"), 0.5)

        max_24h = float(self.cfg.get("max_24h_change_pct", 28.0))
        max_fake = float(self.cfg.get("max_fake_pump_risk", 78.0))
        max_spread = float(self.cfg.get("max_spread_pct", self.config.get("strategy", {}).get("parlayan", {}).get("max_spread_pct", 0.45)))
        rsi_min = float(self.cfg.get("rsi_min", 42.0))
        rsi_max = float(self.cfg.get("rsi_max", 74.0))
        min_volume = _f(adaptive.get("min_volume_ratio"), float(self.cfg.get("min_volume_ratio", 0.45)))
        min_directional = _f(adaptive.get("min_directional_volume_score"), float(self.cfg.get("min_directional_volume_score", 50.0)))
        min_parlayan = _f(adaptive.get("min_parlayan_score"), float(self.cfg.get("min_parlayan_score", 12.0)))
        min_pre = _f(adaptive.get("min_pre_pump_score"), float(self.cfg.get("min_pre_pump_score", 45.0)))

        hard_rejects: list[str] = []
        guard_cfg = dict(self.config.get("discovery_guard", {}) or {})
        if guard_cfg.get("enabled", True):
            min_abs_24h = float(guard_cfg.get("min_abs_24h_change_pct", 0.75))
            min_abs_5m = float(guard_cfg.get("min_abs_5m_change_pct", 0.03))
            min_directional = float(guard_cfg.get("min_directional_volume_score", 42.0))
            min_up_volume = float(guard_cfg.get("min_up_volume_ratio", 0.25))
            if abs(change_24h) < min_abs_24h and abs(change_5m) < min_abs_5m:
                hard_rejects.append(
                    f"discovery guard: fiyat neredeyse sabit, 24h={change_24h:.2f}%, 5m={change_5m:.2f}%"
                )
            if directional_score < min_directional and up_volume < min_up_volume:
                hard_rejects.append(
                    f"discovery guard: alıcı yönü zayıf, dir={directional_score:.1f} < {min_directional:.1f}, up={up_volume:.2f} < {min_up_volume:.2f}"
                )
        if change_24h > max_24h:
            hard_rejects.append(f"24h discovery için geç: {change_24h:.2f}% > {max_24h:.1f}%")
        if feature.fake_pump_risk > max_fake:
            hard_rejects.append(f"fake pump riski yüksek: {feature.fake_pump_risk:.1f} > {max_fake:.1f}")
        if feature.spread_pct is not None and feature.spread_pct > max_spread:
            hard_rejects.append(f"spread yüksek: {feature.spread_pct:.3f}% > {max_spread:.2f}%")
        if not (rsi_min <= rsi <= rsi_max):
            hard_rejects.append(f"RSI discovery bandı dışında: {rsi:.1f}")
        if hard_rejects:
            return self._reject(hard_rejects, {"phase": phase, "profile": profile, "adaptive": adaptive})

        points = 0
        reasons: list[str] = []

        checks = [
            (volume_ratio >= min_volume, f"DNA volume geçti: {volume_ratio:.2f} >= {min_volume:.2f}"),
            (feature.parlayan_score >= min_parlayan, f"DNA parlayan geçti: {feature.parlayan_score:.1f} >= {min_parlayan:.1f}"),
            (pre_pump_score >= min_pre, f"DNA pre-pump geçti: {pre_pump_score:.1f} >= {min_pre:.1f}"),
            (directional_score >= max(min_directional, float(self.cfg.get("directional_soft_floor", 48.0))) or up_volume >= float(self.cfg.get("up_volume_soft_floor", 0.58)), f"alıcı baskısı yeterli: dir={directional_score:.1f}, up={up_volume:.2f}"),
            (velocity_score >= float(self.cfg.get("velocity_soft_floor", 12.0)) or fast_alarm_score >= float(self.cfg.get("fast_alarm_soft_floor", 18.0)), f"velocity/fast_alarm canlı: velocity={velocity_score:.1f}, fast={fast_alarm_score:.1f}"),
            (change_5m >= float(self.cfg.get("min_5m_change_pct", -0.15)) and change_15m >= float(self.cfg.get("min_15m_change_pct", -0.20)), f"kısa yapı bozulmamış: 5m={change_5m:.2f}, 15m={change_15m:.2f}"),
            (score_delta >= float(self.cfg.get("min_score_delta", 1.0)) or momentum_acc >= float(self.cfg.get("min_momentum_acceleration", 0.02)), f"ivme/score_delta var: delta={score_delta:.2f}, mom_acc={momentum_acc:.4f}"),
            (close_location >= float(self.cfg.get("min_close_location_score", 0.38)), f"kapanış konumu kabul: {close_location:.2f}"),
        ]
        for ok, reason in checks:
            if ok:
                points += 1
                reasons.append(reason)

        strong_trigger = (
            pre_pump_score >= float(self.cfg.get("strong_pre_pump_score", 70.0))
            or directional_score >= float(self.cfg.get("strong_directional_score", 72.0))
            or velocity_score >= float(self.cfg.get("strong_velocity_score", 35.0))
            or volume_ratio >= float(self.cfg.get("strong_volume_ratio", 3.0))
            or bool(extra.get("fast_alarm"))
        )
        min_points = int(self.cfg.get("min_points", 4))
        if points < min_points or not strong_trigger:
            return self._reject(
                [f"discovery puanı yetersiz: {points}/{min_points}, strong_trigger={strong_trigger}"],
                {
                    "points": points,
                    "strong_trigger": strong_trigger,
                    "phase": phase,
                    "profile": profile,
                    "adaptive": adaptive,
                    "passed_reasons": reasons,
                },
            )

        confidence = min(1.0, max(0.10, (points / len(checks)) * 0.70 + (0.20 if strong_trigger else 0.0)))
        entry_profile = "DNA_DISCOVERY_ENTRY"
        if phase == "RECOVERY_COMPRESSION":
            entry_profile = "DNA_RECOVERY_SCOUT"
        elif phase in {"VOLUME_WAKEUP", "ACCUMULATION_BREAKOUT"}:
            entry_profile = "DNA_WAKEUP_SCOUT"
        elif phase in {"EARLY_MOMENTUM", "MOMENTUM_EXPANSION"}:
            entry_profile = "DNA_MOMENTUM_SCOUT"

        return DiscoveryDecision(
            allowed=True,
            entry_profile=entry_profile,
            confidence=round(confidence, 4),
            reasons=[
                f"discovery_profile={entry_profile}",
                f"points={points}/{len(checks)}",
                f"confidence={confidence:.2f}",
                *reasons,
            ],
            details={
                "points": points,
                "strong_trigger": strong_trigger,
                "phase": phase,
                "profile": profile,
                "adaptive": adaptive,
                "metrics": {
                    "rsi": rsi,
                    "change_5m": change_5m,
                    "change_15m": change_15m,
                    "change_30m": change_30m,
                    "change_24h": change_24h,
                    "volume_ratio": volume_ratio,
                    "parlayan_score": feature.parlayan_score,
                    "pre_pump_score": pre_pump_score,
                    "velocity_score": velocity_score,
                    "fast_alarm_score": fast_alarm_score,
                    "directional_volume_score": directional_score,
                    "up_volume_ratio": up_volume,
                    "score_delta": score_delta,
                    "momentum_acceleration": momentum_acc,
                },
            },
        )

    @staticmethod
    def _reject(reasons: list[str], details: dict[str, Any]) -> DiscoveryDecision:
        return DiscoveryDecision(False, None, 0.0, reasons, details)
