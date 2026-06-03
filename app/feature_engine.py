from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .binance_client import BinancePublicClient
from .indicators import clamp, kline_features, safe_float, spread_pct_from_book
from .models import MarketFeature, SymbolInfo
from . import storage
from .stable_asset_filter import evaluate_symbol
from .professional_metrics import compute_pre_pump_metrics
from .velocity_engine import compute_velocity_metrics


def _filter_map(filters: list[dict[str, Any]]) -> dict[str, Any]:
    return {item.get("filterType", "UNKNOWN"): item for item in filters}


class FeatureEngine:
    """
    Binance piyasa verilerini çekip her coin için özellik vektörü üretir.
    Parlayan skoru burada hesaplanır.
    """

    def __init__(self, client: BinancePublicClient, config: dict[str, Any]):
        self.client = client
        self.config = config
        self.symbols: dict[str, SymbolInfo] = {}

    async def load_symbols(self) -> list[SymbolInfo]:
        info = await self.client.exchange_info()
        quote = self.config["binance"]["quote_asset"]
        exclude_bases = set(self.config["binance"].get("exclude_bases", []))
        exclude_contains = self.config["binance"].get("exclude_symbols_contains", [])
        symbols: list[SymbolInfo] = []
        for item in info.get("symbols", []):
            symbol = item.get("symbol", "")
            base = item.get("baseAsset", "")
            if item.get("quoteAsset") != quote:
                continue
            stable_decision = evaluate_symbol(symbol, self.config, base_asset=base, quote_asset=item.get("quoteAsset", ""))
            if stable_decision.blocked:
                continue
            if base in exclude_bases:
                continue
            if any(token in symbol for token in exclude_contains):
                continue
            symbols.append(SymbolInfo(
                symbol=symbol,
                base_asset=base,
                quote_asset=item.get("quoteAsset", ""),
                status=item.get("status", ""),
                is_spot_trading_allowed=bool(item.get("isSpotTradingAllowed", False)),
                filters=_filter_map(item.get("filters", [])),
            ))
        self.symbols = {s.symbol: s for s in symbols if s.status == "TRADING" and s.is_spot_trading_allowed}
        return list(self.symbols.values())

    def _select_symbols(self, tickers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Parlayan strateji için sembol seçimi:
        - 24h değişime göre ilk N (asıl hedef kitle)
        - Volume'a göre ilk N (likidite güvencesi)
        - Trade sayısına göre ilk N (aktivite)
        """
        cfg = self.config["scanner"]
        min_vol = float(cfg["min_quote_volume_24h_usdt"])
        valid = [
            t for t in tickers
            if t.get("symbol") in self.symbols and safe_float(t.get("quoteVolume")) >= min_vol
        ]
        # Parlayan hedef: 24h değişim pozitif olanları öne al
        positive_movers = [t for t in valid if safe_float(t.get("priceChangePercent")) > 0]
        by_change = sorted(positive_movers, key=lambda x: safe_float(x.get("priceChangePercent")), reverse=True)
        by_volume = sorted(valid, key=lambda x: safe_float(x.get("quoteVolume")), reverse=True)[:cfg["top_by_quote_volume"]]
        by_count = sorted(valid, key=lambda x: int(safe_float(x.get("count"))), reverse=True)[:cfg["top_by_trade_count"]]

        merged: dict[str, dict[str, Any]] = {t["symbol"]: t for t in by_volume + by_count + by_change[:cfg["top_by_price_change"]]}
        selected = list(merged.values())
        # Sırala: 24h değişim önce, sonra volume
        selected.sort(key=lambda x: (safe_float(x.get("priceChangePercent")), safe_float(x.get("quoteVolume"))), reverse=True)
        return selected[:cfg["max_symbols_per_scan"]]

    async def build_features(self) -> list[MarketFeature]:
        if not self.symbols:
            await self.load_symbols()
        tickers = await self.client.ticker_24hr()
        book_tickers = {item["symbol"]: item for item in await self.client.book_ticker() if item.get("symbol")}
        selected = self._select_symbols(tickers)
        now = datetime.now(timezone.utc)
        features: list[MarketFeature] = []
        for ticker in selected:
            feature = await self._build_feature(ticker, book_tickers.get(ticker["symbol"], {}), now)
            if feature is not None:
                features.append(feature)
        # Parlayan skora göre sırala
        features.sort(key=lambda f: f.parlayan_score, reverse=True)
        return features

    async def build_features_for_symbols(self, symbols: list[str]) -> dict[str, MarketFeature]:
        """Belirli semboller için özellik güncelle (pozisyon izleme için)."""
        if not symbols:
            return {}
        if not self.symbols:
            await self.load_symbols()
        wanted = {s for s in symbols if s in self.symbols}
        if not wanted:
            return {}
        tickers = {item.get("symbol"): item for item in await self.client.ticker_24hr() if item.get("symbol") in wanted}
        books = {item.get("symbol"): item for item in await self.client.book_ticker() if item.get("symbol") in wanted}
        now = datetime.now(timezone.utc)
        out: dict[str, MarketFeature] = {}
        for symbol in wanted:
            ticker = tickers.get(symbol)
            if not ticker:
                continue
            feature = await self._build_feature(ticker, books.get(symbol, {}), now)
            if feature is not None:
                out[symbol] = feature
        return out

    async def _build_feature(self, ticker: dict[str, Any], book: dict[str, Any], now: datetime) -> MarketFeature | None:
        symbol = ticker["symbol"]
        interval = self.config["scanner"]["kline_interval"]
        limit = self.config["scanner"]["kline_limit"]
        try:
            klines = await self.client.klines(symbol, interval=interval, limit=limit)
        except Exception:
            return None
        kf = kline_features(klines)
        if not kf:
            return None

        price = safe_float(ticker.get("lastPrice"), kf.get("last_close", 0.0))
        bid = safe_float(book.get("bidPrice"))
        ask = safe_float(book.get("askPrice"))
        spread_pct = spread_pct_from_book(bid, ask)
        price_change_24h = safe_float(ticker.get("priceChangePercent"))
        quote_volume = safe_float(ticker.get("quoteVolume"))
        trade_count = int(safe_float(ticker.get("count")))

        change_5m = float(kf.get("price_change_5m_pct", 0.0))
        change_15m = float(kf.get("price_change_15m_pct", 0.0))
        change_30m = float(kf.get("price_change_30m_pct", 0.0))
        volume_ratio = float(kf.get("volume_ratio", 1.0))
        rsi = kf.get("rsi")
        wick_body_ratio = float(kf.get("wick_body_ratio", 0.0) or 0.0)

        liquidity_score = self._liquidity_score(quote_volume, trade_count, spread_pct)
        fake_risk = self._fake_pump_risk(wick_body_ratio, spread_pct, liquidity_score, change_15m)
        momentum_score = self._momentum_score(change_5m, change_15m, change_24h=price_change_24h, volume_ratio=volume_ratio)
        parlayan_score = self._parlayan_score(
            change_24h=price_change_24h,
            change_5m=change_5m,
            change_15m=change_15m,
            change_30m=change_30m,
            volume_ratio=volume_ratio,
            rsi=rsi,
            liquidity_score=liquidity_score,
            fake_risk=fake_risk,
            spread_pct=spread_pct,
        )
        bot_state = self._state_from_rsi(rsi)

        preview_feature = type("PreviewFeature", (), {})()
        preview_feature.symbol = symbol
        preview_feature.ts = now
        preview_feature.price = price
        preview_feature.rsi = rsi
        preview_feature.price_change_24h_pct = price_change_24h
        preview_feature.price_change_15m_pct = change_15m
        preview_feature.price_change_5m_pct = change_5m
        preview_feature.price_change_30m_pct = change_30m
        preview_feature.quote_volume_24h = quote_volume
        preview_feature.trade_count_24h = trade_count
        preview_feature.spread_pct = spread_pct
        preview_feature.volume_ratio = volume_ratio
        preview_feature.momentum_score = momentum_score
        preview_feature.liquidity_score = liquidity_score
        preview_feature.fake_pump_risk = fake_risk
        preview_feature.parlayan_score = parlayan_score
        preview_feature.wick_body_ratio = wick_body_ratio
        preview_feature.extra = {
            "up_volume_ratio": kf.get("up_volume_ratio"),
            "down_volume_ratio": kf.get("down_volume_ratio"),
            "directional_volume_delta": kf.get("directional_volume_delta"),
            "recent_green_bar_ratio": kf.get("recent_green_bar_ratio"),
            "close_location_score": kf.get("close_location_score"),
            "directional_volume_score": kf.get("directional_volume_score"),
        }

        previous_snapshot = storage.get_latest_market_snapshot(symbol)
        velocity_metrics = compute_velocity_metrics(preview_feature, previous_snapshot)
        professional_metrics = compute_pre_pump_metrics(preview_feature, previous_snapshot)

        return MarketFeature(
            symbol=symbol,
            ts=now,
            price=price,
            rsi=rsi,
            price_change_24h_pct=price_change_24h,
            price_change_15m_pct=change_15m,
            price_change_5m_pct=change_5m,
            price_change_30m_pct=change_30m,
            quote_volume_24h=quote_volume,
            trade_count_24h=trade_count,
            spread_pct=spread_pct,
            volume_ratio=volume_ratio,
            momentum_score=momentum_score,
            liquidity_score=liquidity_score,
            fake_pump_risk=fake_risk,
            parlayan_score=parlayan_score,
            wick_body_ratio=wick_body_ratio,
            bot_state=bot_state,
            extra={
                "price_change_1h_pct": kf.get("price_change_1h_pct"),
                "price_change_4h_pct": kf.get("price_change_4h_pct"),
                "up_volume_ratio": kf.get("up_volume_ratio"),
                "down_volume_ratio": kf.get("down_volume_ratio"),
                "directional_volume_delta": kf.get("directional_volume_delta"),
                "recent_green_bar_ratio": kf.get("recent_green_bar_ratio"),
                "close_location_score": kf.get("close_location_score"),
                "directional_volume_score": kf.get("directional_volume_score"),
                "interval": interval,
                **velocity_metrics,
                **professional_metrics,
            },
        )

    def _state_from_rsi(self, rsi: float | None) -> str:
        if rsi is None:
            return "DATA_INCOMPLETE"
        if rsi < 35:
            return "OVERSOLD"
        if rsi < 45:
            return "RECOVERY"
        if rsi >= 75:
            return "FOMO_RISK"
        return "ACTIVE_MOMENTUM"

    @staticmethod
    def _liquidity_score(quote_volume: float, trade_count: int, spread_pct: float | None) -> float:
        volume_score = clamp(math.log10(max(quote_volume, 1.0)) * 11.5 - 45)
        count_score = clamp(math.log10(max(trade_count, 1)) * 14 - 30)
        spread_score = 70.0 if spread_pct is None else clamp(100 - spread_pct * 240)
        return round(clamp(volume_score * 0.45 + count_score * 0.25 + spread_score * 0.30), 2)

    @staticmethod
    def _fake_pump_risk(wick_body_ratio: float, spread_pct: float | None, liquidity_score: float, change_15m: float) -> float:
        spread_component = 0.0 if spread_pct is None else clamp(spread_pct * 160, 0, 35)
        wick_component = clamp(wick_body_ratio * 14, 0, 30)
        low_liquidity_component = clamp(65 - liquidity_score, 0, 30)
        jump_component = clamp(max(change_15m - 10, 0) * 2, 0, 20)
        return round(clamp(spread_component + wick_component + low_liquidity_component + jump_component), 2)

    @staticmethod
    def _momentum_score(change_5m: float, change_15m: float, change_24h: float, volume_ratio: float) -> float:
        score = 35.0
        score += clamp(change_5m * 6, -20, 25)
        score += clamp(change_15m * 3.0, -20, 25)
        score += clamp(math.log(max(volume_ratio, 0.1), 2) * 12, -10, 25)
        score += clamp(change_24h * 0.22, -15, 18)
        return round(clamp(score), 2)

    @staticmethod
    def _parlayan_score(
        change_24h: float,
        change_5m: float,
        change_15m: float,
        change_30m: float,
        volume_ratio: float,
        rsi: float | None,
        liquidity_score: float,
        fake_risk: float,
        spread_pct: float | None,
    ) -> float:
        """
        Günlük parlayan coin skoru.
        Hedef: 24h'de %7-%50 hareket etmiş, momentumu devam eden coinleri bul.

        Bileşenler:
        - 24h değişim (ana sinyal)        : max 35 puan
        - Kısa vadeli momentum (5m+15m)   : max 25 puan
        - Volume onayı                     : max 20 puan
        - RSI zonu (55-72 ideal)          : max 10 puan
        - Kalite bonusu (liq - fake risk) : max 10 puan

        Negatif çarpanlar:
        - Düşük likidite, yüksek spread, yüksek fake risk
        """
        score = 0.0

        # 1. 24h değişim skoru — asıl parlayan sinyali
        if change_24h >= 7:
            # 7% başlangıç noktası, 50%'de tepe
            score += clamp((change_24h - 7.0) * 2.3, 0, 35)
        elif change_24h > 0:
            # 0-7% arası çok zayıf, minimum katkı
            score += change_24h * 0.3

        # 2. Momentum devam (5m + 15m): hareket hala devam ediyor mu?
        score += clamp(change_5m * 10, 0, 12)
        score += clamp(change_15m * 4, 0, 13)

        # 3. Volume onayı: gerçek alıcı var mı?
        if volume_ratio > 0:
            score += clamp(math.log(max(volume_ratio, 0.1), 2) * 9, 0, 20)

        # 4. RSI zonu: 55-72 arası ideal (ne aşırı alım ne ölü)
        if rsi is not None:
            rsi_f = float(rsi)
            if 55 <= rsi_f <= 72:
                score += 10
            elif 50 <= rsi_f < 55 or 72 < rsi_f <= 78:
                score += 5
            elif rsi_f > 78:
                score -= 10  # Aşırı alım cezası

        # 5. Kalite: likidite - fake risk dengesi
        quality = (liquidity_score - fake_risk) / 10
        score += clamp(quality, -5, 10)

        # Spread cezası
        if spread_pct is not None and spread_pct > 0.25:
            score -= clamp((spread_pct - 0.25) * 40, 0, 15)

        return round(clamp(score), 2)
