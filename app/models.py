from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    is_spot_trading_allowed: bool
    filters: dict[str, Any]


@dataclass(slots=True)
class MarketFeature:
    """Piyasa verisi + hesaplanmış skorlar."""
    symbol: str
    ts: datetime
    price: float
    rsi: float | None
    price_change_24h_pct: float
    price_change_15m_pct: float
    price_change_5m_pct: float
    price_change_30m_pct: float
    quote_volume_24h: float
    trade_count_24h: int
    spread_pct: float | None
    volume_ratio: float
    momentum_score: float      # Genel momentum gücü (0-100)
    liquidity_score: float     # Likidite kalitesi (0-100)
    fake_pump_risk: float      # Sahte pump riski (0-100, düşük = iyi)
    parlayan_score: float      # Ana parlayan skoru (0-100)
    wick_body_ratio: float
    bot_state: str = "WATCH"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParlayanCandidate:
    """Günlük parlayan olarak tespit edilen aday."""
    symbol: str
    detected_at: datetime
    price_at_detection: float
    price_change_24h_pct: float   # Tespit anındaki 24h değişim
    parlayan_score: float
    volume_ratio: float
    rsi: float
    status: str = "WATCHING"      # WATCHING | ENTERED | CLOSED | EXPIRED
    entry_price: float | None = None
    peak_gain_pct: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Trade:
    """Paper trade kaydı."""
    symbol: str
    entry_price: float
    quote_size: float
    stop_loss_pct: float
    trailing_start_pct: float
    trailing_gap_pct: float
    take_profit_pct: float
    context: dict[str, Any] = field(default_factory=dict)
