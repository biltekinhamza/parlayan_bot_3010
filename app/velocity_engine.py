from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .indicators import clamp


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _dt_minutes(current: Any, previous: Any) -> float:
    if not isinstance(current, datetime) or not isinstance(previous, datetime):
        return 1.0
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return max((current - previous).total_seconds() / 60.0, 0.25)


def compute_velocity_metrics(feature: Any, previous_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """
    V4.2 velocity / fast-alarm katmanı.

    GitHub pump detector tarzı projelerden aldığımız ana fikir:
    sadece periyodik skor değil, hareketin hızlanmasını ölç.
    Bu fonksiyon yeni tablo gerektirmez; sonuçları market_snapshots.extra içine yazar.
    """
    previous_snapshot = previous_snapshot or {}
    prev_extra = previous_snapshot.get("extra") or {}

    prev_price = _f(previous_snapshot.get("price"), _f(getattr(feature, "price", None)))
    price = _f(getattr(feature, "price", None))
    minutes = _dt_minutes(getattr(feature, "ts", None), previous_snapshot.get("ts"))

    price_delta_pct = 0.0
    if prev_price > 0 and price > 0:
        price_delta_pct = ((price - prev_price) / prev_price) * 100.0

    price_velocity_1m = price_delta_pct / minutes

    change_5m = _f(getattr(feature, "price_change_5m_pct", None))
    change_15m = _f(getattr(feature, "price_change_15m_pct", None))
    change_30m = _f(getattr(feature, "price_change_30m_pct", None))

    # Mum pencerelerinden kaba hız ve ivme yaklaşımı.
    price_velocity_5m = change_5m / 5.0
    price_velocity_15m = change_15m / 15.0
    momentum_acceleration = price_velocity_5m - price_velocity_15m

    volume_ratio = _f(getattr(feature, "volume_ratio", None), 1.0)
    prev_volume_ratio = _f(previous_snapshot.get("volume_ratio"), volume_ratio)
    volume_velocity = (volume_ratio - prev_volume_ratio) / minutes

    trade_count = _f(getattr(feature, "trade_count_24h", None), 0.0)
    prev_trade_count = _f(previous_snapshot.get("trade_count_24h"), trade_count)
    trade_count_velocity = max((trade_count - prev_trade_count) / minutes, 0.0)

    prev_velocity_score = _f(prev_extra.get("velocity_score"), 0.0)
    velocity_score = 0.0
    velocity_score += clamp(price_velocity_1m * 22.0, -12, 25)
    velocity_score += clamp(price_velocity_5m * 18.0, -10, 22)
    velocity_score += clamp(momentum_acceleration * 30.0, -12, 24)
    velocity_score += clamp(max(volume_ratio - 1.0, 0.0) * 9.0, 0, 24)
    velocity_score += clamp(max(volume_velocity, 0.0) * 16.0, 0, 16)
    velocity_score += clamp(trade_count_velocity / 45.0, 0, 12)
    velocity_score = round(clamp(velocity_score, 0, 100), 2)

    velocity_delta = round(velocity_score - prev_velocity_score, 2)

    fast_alarm_score = 0.0
    fast_alarm_score += clamp(max(price_velocity_1m, 0.0) * 28.0, 0, 28)
    fast_alarm_score += clamp(max(change_5m, 0.0) * 5.0, 0, 24)
    fast_alarm_score += clamp(max(volume_ratio - 1.2, 0.0) * 10.0, 0, 24)
    fast_alarm_score += clamp(max(volume_velocity, 0.0) * 20.0, 0, 14)
    fast_alarm_score += clamp(max(momentum_acceleration, 0.0) * 32.0, 0, 10)
    fast_alarm_score = round(clamp(fast_alarm_score, 0, 100), 2)

    fast_alarm = (
        fast_alarm_score >= 65
        and change_5m >= 0.35
        and volume_ratio >= 1.25
        and price_velocity_1m >= 0.08
    )

    alarm_reason: list[str] = []
    if price_velocity_1m >= 0.08:
        alarm_reason.append("price_velocity_1m_positive")
    if change_5m >= 0.35:
        alarm_reason.append("price_change_5m_positive")
    if volume_ratio >= 1.25:
        alarm_reason.append("volume_ratio_expanding")
    if volume_velocity > 0:
        alarm_reason.append("volume_velocity_positive")
    if momentum_acceleration > 0:
        alarm_reason.append("momentum_acceleration_positive")

    return {
        "price_velocity_1m_pct": round(price_velocity_1m, 5),
        "price_velocity_5m_pct": round(price_velocity_5m, 5),
        "price_velocity_15m_pct": round(price_velocity_15m, 5),
        "momentum_acceleration": round(momentum_acceleration, 5),
        "volume_velocity": round(volume_velocity, 5),
        "trade_count_velocity": round(trade_count_velocity, 2),
        "velocity_score": velocity_score,
        "velocity_delta": velocity_delta,
        "fast_alarm_score": fast_alarm_score,
        "fast_alarm": fast_alarm,
        "fast_alarm_reasons": alarm_reason,
    }
