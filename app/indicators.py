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
    opens = [safe_float(row[1]) for row in klines]
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

    # Directional volume proxy:
    # Binance public klines do not tell us buyer-initiated volume directly.
    # This proxy estimates whether recent volume is happening on green/upper-closing candles.
    recent_n = 6 if len(closes) >= 6 else len(closes)
    recent_indices = list(range(len(closes) - recent_n, len(closes))) if recent_n > 0 else []
    recent_qv = sum(quote_volumes[i] for i in recent_indices) or 1.0
    up_qv = 0.0
    down_qv = 0.0
    green_bars = 0
    close_location_sum = 0.0
    for i in recent_indices:
        qv = quote_volumes[i]
        o = opens[i]
        c = closes[i]
        h = highs[i]
        l = lows[i]
        if c >= o:
            up_qv += qv
            green_bars += 1
        else:
            down_qv += qv
        rng = max(h - l, 1e-12)
        close_location_sum += (c - l) / rng

    up_volume_ratio = up_qv / recent_qv
    down_volume_ratio = down_qv / recent_qv
    directional_volume_delta = up_volume_ratio - down_volume_ratio
    recent_green_bar_ratio = green_bars / recent_n if recent_n > 0 else 0.0
    close_location_score = close_location_sum / recent_n if recent_n > 0 else 0.5
    directional_volume_score = clamp(
        up_volume_ratio * 55.0
        + recent_green_bar_ratio * 25.0
        + close_location_score * 20.0,
        0.0,
        100.0,
    )

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
        "up_volume_ratio": up_volume_ratio,
        "down_volume_ratio": down_volume_ratio,
        "directional_volume_delta": directional_volume_delta,
        "recent_green_bar_ratio": recent_green_bar_ratio,
        "close_location_score": close_location_score,
        "directional_volume_score": directional_volume_score,
    }


def spread_pct_from_book(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 100.0
