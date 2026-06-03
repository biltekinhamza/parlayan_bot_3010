from __future__ import annotations

from typing import Any


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def directional_volume_proxy(extra: dict[str, Any] | None) -> dict[str, Any]:
    """
    Directional Volume Proxy.

    Binance public kline verisinde gerçek aggressor-side tape yoktur; bu yüzden
    yeşil/kırmızı bar oranı, close location ve up/down volume proxy değerleriyle
    alıcı/satıcı baskısı tahmini çıkarılır.
    """
    extra = extra or {}
    up_volume_ratio = _f(extra.get("up_volume_ratio"), 0.5)
    down_volume_ratio = _f(extra.get("down_volume_ratio"), 0.5)
    delta = _f(extra.get("directional_volume_delta"), up_volume_ratio - down_volume_ratio)
    close_location = _f(extra.get("close_location_score"), 0.5)
    green_ratio = _f(extra.get("recent_green_bar_ratio"), 0.5)
    raw_score = 50.0
    raw_score += (up_volume_ratio - 0.5) * 70.0
    raw_score += delta * 45.0
    raw_score += (close_location - 0.5) * 35.0
    raw_score += (green_ratio - 0.5) * 25.0
    score = round(max(0.0, min(100.0, raw_score)), 2)
    side = "BUYER_PRESSURE" if score >= 58 else "SELLER_PRESSURE" if score <= 42 else "MIXED"
    return {
        "directional_volume_score": score,
        "directional_volume_side": side,
        "up_volume_ratio": up_volume_ratio,
        "down_volume_ratio": down_volume_ratio,
        "directional_volume_delta": delta,
        "close_location_score": close_location,
        "recent_green_bar_ratio": green_ratio,
    }
