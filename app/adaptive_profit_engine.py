from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class AdaptiveProfitEngine:
    """
    V4.5 Adaptive Profit Engine.

    Sabit take-profit iyi trendleri erken öldürmesin diye trend persistence ölçer.
    Trend güçlü ise hedef uzatılır; aynı anda kâr koruma stopu yukarı taşınır.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def trend_persistence_score(self, context: dict[str, Any], live_extra: dict[str, Any]) -> float:
        metrics = context.get("professional_metrics") or {}
        pre = _f(context.get("pre_pump_score"), _f(metrics.get("pre_pump_score"), 0.0))
        velocity = _f(live_extra.get("velocity_score"), _f(metrics.get("velocity_score"), 0.0))
        directional = _f(live_extra.get("directional_volume_score"), _f(metrics.get("directional_volume_score"), 50.0))
        up_volume = _f(live_extra.get("up_volume_ratio"), _f(metrics.get("up_volume_ratio"), 0.5))
        momentum_accel = _f(live_extra.get("momentum_acceleration"), _f(metrics.get("momentum_acceleration"), 0.0))
        regime_boost = 0.0
        regime = str((self.config.get("research", {}) or {}).get("latest_regime", "") or "")
        if regime in {"ALT_RISK_ON", "RISK_ON", "ALT_ROTATION"}:
            regime_boost = 5.0

        score = 0.0
        score += min(pre, 100.0) * 0.28
        score += min(velocity, 100.0) * 0.25
        score += min(max(directional, 0.0), 100.0) * 0.22
        score += max(0.0, min((up_volume - 0.45) * 120.0, 18.0))
        score += max(0.0, min(momentum_accel * 24.0, 12.0))
        score += regime_boost
        return round(max(0.0, min(100.0, score)), 2)

    def evaluate(
        self,
        trade: dict[str, Any],
        context: dict[str, Any],
        live_extra: dict[str, Any],
        current_gain_pct: float,
        max_gain_pct: float,
        fee_pct: float,
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        ap_cfg = self.config.get("adaptive_profit", {})
        if not ap_cfg.get("enabled", True):
            return {
                "extend": False,
                "reason": "disabled",
                "lock_pct": 0.0,
                "target_pct": _f(trade.get("take_profit_pct"), _f(cfg.get("take_profit_pct"), 15.0)),
            }

        persistence = self.trend_persistence_score(context, live_extra)
        min_persistence = _f(ap_cfg.get("min_trend_persistence_score"), 66.0)
        target_pct = _f(ap_cfg.get("extended_take_profit_pct"), 24.0)
        lock_pct = _f(ap_cfg.get("lock_profit_pct"), 10.0)
        giveback_pct = _f(ap_cfg.get("max_giveback_from_peak_pct"), 4.2)

        if persistence >= min_persistence and max_gain_pct - current_gain_pct <= giveback_pct:
            return {
                "extend": True,
                "reason": f"trend persists: score={persistence:.1f}",
                "lock_pct": lock_pct,
                "target_pct": target_pct,
                "trend_persistence_score": persistence,
            }

        return {
            "extend": False,
            "reason": f"trend weak: score={persistence:.1f}",
            "lock_pct": 0.0,
            "target_pct": _f(trade.get("take_profit_pct"), _f(cfg.get("take_profit_pct"), 15.0)),
            "trend_persistence_score": persistence,
        }
