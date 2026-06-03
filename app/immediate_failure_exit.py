from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class ImmediateFailureExit:
    """
    V4.5 Immediate Failure Exit.

    Follow-through kontrolünden daha erken, bariz başarısız breakoutları öldürür.
    Amaç stop-loss beklemek değil, çalışan hareket ile çalışmayan hareketi hızlı ayırmaktır.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def evaluate(
        self,
        context: dict[str, Any],
        live_extra: dict[str, Any],
        age_minutes: float,
        current_net_gain_pct: float,
        max_runup_pct: float,
    ) -> dict[str, Any]:
        cfg = self.config.get("immediate_failure_exit", {})
        if not cfg.get("enabled", True):
            return {"exit": False, "exit_reason": None, "reason": "disabled", "score": 50.0}

        check_after = _f(cfg.get("check_after_minutes"), 3.0)
        hard_loss = _f(cfg.get("hard_loss_pct"), -1.15)
        min_runup = _f(cfg.get("min_runup_pct"), 0.12)
        min_velocity = _f(cfg.get("min_velocity_score"), 12.0)
        min_directional = _f(cfg.get("min_directional_score"), 40.0)
        min_up_volume = _f(cfg.get("min_up_volume_ratio"), 0.46)

        metrics = context.get("professional_metrics") or {}
        velocity = _f(live_extra.get("velocity_score"), _f(metrics.get("velocity_score"), 0.0))
        directional = _f(live_extra.get("directional_volume_score"), _f(metrics.get("directional_volume_score"), 50.0))
        up_volume = _f(live_extra.get("up_volume_ratio"), _f(metrics.get("up_volume_ratio"), 0.5))
        momentum_accel = _f(live_extra.get("momentum_acceleration"), _f(metrics.get("momentum_acceleration"), 0.0))

        score = 50.0
        score += min(max_runup_pct * 18.0, 18.0)
        score += min(current_net_gain_pct * 10.0, 12.0)
        score += min((velocity - min_velocity) * 0.4, 12.0)
        score += min((directional - min_directional) * 0.35, 10.0)
        score += min((up_volume - min_up_volume) * 60.0, 10.0)
        score += min(momentum_accel * 20.0, 8.0)
        score = round(max(0.0, min(100.0, score)), 2)

        details = {
            "age_minutes": round(age_minutes, 2),
            "current_net_gain_pct": round(current_net_gain_pct, 4),
            "max_runup_pct": round(max_runup_pct, 4),
            "velocity_score": velocity,
            "directional_volume_score": directional,
            "up_volume_ratio": up_volume,
            "momentum_acceleration": momentum_accel,
            "score": score,
        }

        if age_minutes < check_after:
            return {"exit": False, "exit_reason": None, "reason": "warming_up", **details}

        no_runup = max_runup_pct < min_runup
        broken_price = current_net_gain_pct <= hard_loss
        broken_flow = velocity < min_velocity and directional < min_directional and up_volume < min_up_volume

        if broken_price and (no_runup or broken_flow):
            return {
                "exit": True,
                "exit_reason": "IMMEDIATE_FAILURE_EXIT",
                "reason": "early breakout failure: loss/no-runup/weak-flow",
                "no_runup": no_runup,
                "broken_flow": broken_flow,
                **details,
            }

        return {"exit": False, "exit_reason": None, "reason": "not_failed", **details}
