from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class FollowThroughEngine:
    """
    V4.6 Follow Through Engine.

    Girişten sonra coin hareketi gerçekten devam ediyor mu ölçer.
    Profesyonel trader mantığı: iyi breakout, ilk dakikalarda en azından küçük
    bir runup, hacim yönü ve velocity onayı üretmelidir.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(
        self,
        trade: dict[str, Any],
        context: dict[str, Any],
        live_extra: dict[str, Any],
        age_minutes: float,
        current_net_gain_pct: float,
        max_runup_pct: float,
        max_drawdown_pct: float,
    ) -> dict[str, Any]:
        ft_cfg = self.config.get("follow_through", {})
        if not ft_cfg.get("enabled", True):
            return {"exit": False, "exit_reason": None, "score": 50.0, "reason": "disabled"}

        check_min = _f(ft_cfg.get("check_after_minutes"), 10.0)
        min_runup = _f(ft_cfg.get("min_runup_pct"), 0.35)
        max_loss = _f(ft_cfg.get("max_loss_pct"), -0.85)
        velocity_floor = _f(ft_cfg.get("velocity_score_floor"), 18.0)
        directional_floor = _f(ft_cfg.get("directional_score_floor"), 45.0)

        metrics = context.get("professional_metrics") or {}
        velocity_score = _f(live_extra.get("velocity_score"), _f(metrics.get("velocity_score"), 0.0))
        directional_score = _f(live_extra.get("directional_volume_score"), _f(metrics.get("directional_volume_score"), 50.0))
        up_volume = _f(live_extra.get("up_volume_ratio"), _f(metrics.get("up_volume_ratio"), 0.5))
        momentum_accel = _f(live_extra.get("momentum_acceleration"), _f(metrics.get("momentum_acceleration"), 0.0))
        price_velocity = _f(live_extra.get("price_velocity_1m_pct"), _f(metrics.get("price_velocity_1m_pct"), 0.0))

        score = 50.0
        score += min(max_runup_pct * 14.0, 24.0)
        score += min(max(current_net_gain_pct, -3.0) * 8.0, 18.0)
        score += min(max(velocity_score - 30.0, -30.0) * 0.35, 16.0)
        score += min(max(directional_score - 50.0, -30.0) * 0.35, 14.0)
        score += min(max(up_volume - 0.52, -0.25) * 55.0, 12.0)
        score += min(max(momentum_accel, -1.0) * 18.0, 10.0)
        score += min(max(price_velocity, -0.5) * 16.0, 6.0)
        score = round(max(0.0, min(100.0, score)), 2)

        details = {
            "age_minutes": round(age_minutes, 2),
            "max_runup_pct": round(max_runup_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "current_net_gain_pct": round(current_net_gain_pct, 4),
            "velocity_score": velocity_score,
            "directional_volume_score": directional_score,
            "up_volume_ratio": up_volume,
            "momentum_acceleration": momentum_accel,
            "price_velocity_1m_pct": price_velocity,
            "score": score,
        }

        if age_minutes < check_min:
            return {"exit": False, "exit_reason": None, "score": score, "reason": "warming_up", **details}

        failed_runup = max_runup_pct < min_runup
        underwater = current_net_gain_pct <= max_loss
        weak_flow = velocity_score < velocity_floor or directional_score < directional_floor or up_volume < 0.48

        if failed_runup and underwater:
            return {
                "exit": True,
                "exit_reason": "FAILED_BREAKOUT",
                "reason": f"max_runup={max_runup_pct:.2f}% < {min_runup:.2f}% ve pnl={current_net_gain_pct:.2f}% <= {max_loss:.2f}%",
                "weak_flow": weak_flow,
                **details,
            }

        stale_min = _f(ft_cfg.get("stale_after_minutes"), 24.0)
        stale_runup = _f(ft_cfg.get("stale_min_runup_pct"), 0.75)
        if age_minutes >= stale_min and max_runup_pct < stale_runup and current_net_gain_pct < 0.05 and weak_flow:
            return {
                "exit": True,
                "exit_reason": "STALE_MOMENTUM_EXIT",
                "reason": f"{age_minutes:.1f} dk sonra takip yok: runup={max_runup_pct:.2f}%, flow weak",
                "weak_flow": weak_flow,
                **details,
            }

        return {"exit": False, "exit_reason": None, "score": score, "reason": "healthy_or_wait", **details}
