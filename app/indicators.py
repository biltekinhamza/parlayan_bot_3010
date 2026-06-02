from __future__ import annotations

from statistics import mean


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def rsi_from_closes(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, period + 1):
        change = closes[idx] - closes[idx - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for idx in range(period + 1, len(closes)):
        change = closes[idx] - closes[idx - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100.0


def kline_features(klines: list[list]) -> dict:
    closes = [safe_float(row[4]) for row in klines]
    highs = [safe_float(row[2]) for row in klines]
    lows = [safe_float(row[3]) for row in klines]
    volumes = [safe_float(row[5]) for row in klines]
    quote_volumes = [safe_float(row[7]) for row in klines]

    if not closes:
        return {}

    last_close = closes[-1]
    change_5m = pct_change(closes[-2], last_close) if len(closes) >= 2 else 0.0
    change_15m = pct_change(closes[-4], last_close) if len(closes) >= 4 else 0.0
    change_30m = pct_change(closes[-7], last_close) if len(closes) >= 7 else 0.0
    change_1h = pct_change(closes[-13], last_close) if len(closes) >= 13 else 0.0
    change_4h = pct_change(closes[-49], last_close) if len(closes) >= 49 else 0.0

    # Volume ratio: son 3 bar / önceki 30 bar ortalaması
    recent_volume = sum(quote_volumes[-3:]) if len(quote_volumes) >= 3 else sum(quote_volumes)
    baseline_slice = quote_volumes[-30:-3] if len(quote_volumes) >= 33 else quote_volumes[:-3]
    baseline = mean(baseline_slice) * 3 if baseline_slice else max(recent_volume, 1.0)
    volume_ratio = recent_volume / baseline if baseline > 0 else 1.0

    # Wick/body ratio (mum gövde analizi)
    last_open = safe_float(klines[-1][1])
    upper_wick = max(0.0, highs[-1] - max(last_open, last_close))
    body = max(abs(last_close - last_open), 1e-12)
    wick_body_ratio = upper_wick / body

    return {
        "rsi": rsi_from_closes(closes),
        "price_change_5m_pct": change_5m,
        "price_change_15m_pct": change_15m,
        "price_change_30m_pct": change_30m,
        "price_change_1h_pct": change_1h,
        "price_change_4h_pct": change_4h,
        "volume_ratio": volume_ratio,
        "wick_body_ratio": wick_body_ratio,
        "last_close": last_close,
    }


def spread_pct_from_book(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 100.0
