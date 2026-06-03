from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from . import storage
from .binance_client import BinancePublicClient
from .decision_engine import DecisionEngine
from .feature_engine import FeatureEngine
from .models import MarketFeature
from .trade_engine import TradeEngine


class ScannerService:
    """
    Ana tarama döngüsü.

    Profesyonel paper-trading araştırma döngüsü v4.4:
    1. Piyasayı tara → her coin için dakika dakika snapshot yaz
    2. Pre-pump / momentum fazını hesapla
    3. Aday listesini güncelle
    4. Risk filtresinden geçenlere sanal giriş yap
    5. Açık paper pozisyonları trailing/koruma mantığıyla yönet
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.client = BinancePublicClient(
            base_url=config["binance"]["rest_base_url"],
            timeout_seconds=int(config["binance"]["request_timeout_seconds"]),
            max_retries=int(config["binance"]["max_retries"]),
        )
        self.feature_engine = FeatureEngine(self.client, config)
        self.decision_engine = DecisionEngine(config)
        self.trade_engine = TradeEngine(config)
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def close(self) -> None:
        await self.client.close()

    def reload_config(self, config: dict[str, Any]) -> None:
        self.config = config
        self.feature_engine.config = config
        self.decision_engine = DecisionEngine(config)
        self.trade_engine = TradeEngine(config)
        storage.log_event("INFO", "config", "Config yeniden yüklendi", {})

    async def run_once(self) -> dict[str, Any]:
        """Tek tarama döngüsü."""
        try:
            # 1. Sembolleri yükle
            symbols = await self.feature_engine.load_symbols()
            storage.upsert_symbols(symbols)

            # 2. Özellikleri hesapla (parlayan skora göre sıralı)
            features = await self.feature_engine.build_features()
            storage.insert_market_snapshots(features)
            market_regime = storage.compute_and_store_market_regime(features)
            for feature in features:
                extra = feature.extra or {}
                if extra.get("phase_changed"):
                    storage.insert_signal_event(
                        feature.symbol,
                        "PHASE_CHANGE",
                        "INFO",
                        float(extra.get("pre_pump_score") or 0),
                        feature.price,
                        {
                            "from": extra.get("previous_phase"),
                            "to": extra.get("market_phase"),
                            "volume_ratio": feature.volume_ratio,
                            "price_change_5m_pct": feature.price_change_5m_pct,
                            "price_change_15m_pct": feature.price_change_15m_pct,
                            "price_change_24h_pct": feature.price_change_24h_pct,
                        },
                    )
                if float(extra.get("pre_pump_score") or 0) >= 70 and extra.get("market_phase") in {"EARLY_MOMENTUM", "ACCUMULATION_BREAKOUT"}:
                    storage.insert_signal_event(
                        feature.symbol,
                        "PRE_PUMP_ALERT",
                        "INFO",
                        float(extra.get("pre_pump_score") or 0),
                        feature.price,
                        {
                            "market_phase": extra.get("market_phase"),
                            "parlayan_score": feature.parlayan_score,
                            "volume_ratio": feature.volume_ratio,
                            "rsi": feature.rsi,
                            "spread_pct": feature.spread_pct,
                            "velocity_score": extra.get("velocity_score"),
                            "fast_alarm_score": extra.get("fast_alarm_score"),
                        },
                    )
                if extra.get("fast_alarm"):
                    storage.insert_signal_event(
                        feature.symbol,
                        "FAST_PUMP_ALERT",
                        "WARNING" if float(extra.get("fast_alarm_score") or 0) >= 80 else "INFO",
                        float(extra.get("fast_alarm_score") or 0),
                        feature.price,
                        {
                            "fast_alarm_score": extra.get("fast_alarm_score"),
                            "fast_alarm_reasons": extra.get("fast_alarm_reasons", []),
                            "velocity_score": extra.get("velocity_score"),
                            "velocity_delta": extra.get("velocity_delta"),
                            "price_velocity_1m_pct": extra.get("price_velocity_1m_pct"),
                            "price_velocity_5m_pct": extra.get("price_velocity_5m_pct"),
                            "volume_velocity": extra.get("volume_velocity"),
                            "trade_count_velocity": extra.get("trade_count_velocity"),
                            "market_phase": extra.get("market_phase"),
                            "parlayan_score": feature.parlayan_score,
                            "pre_pump_score": extra.get("pre_pump_score"),
                            "volume_ratio": feature.volume_ratio,
                            "rsi": feature.rsi,
                        },
                    )

            # 3. Kararlar ve işlemler
            cfg = self.config["strategy"]["parlayan"]
            decision_limit = int(self.config["scanner"].get("decision_limit_symbols", 100))
            max_watch = int(cfg.get("max_watch_candidates", 30))

            watched = 0
            entered = 0
            rejected = 0

            for feature in features[:decision_limit]:
                # Cooldown ve açık işlem durumu
                # V4.1: restart sonrası eski session açık pozisyonları da dikkate alınır.
                cooldown = storage.get_active_cooldown(feature.symbol)
                has_open = bool(storage.get_open_trades_for_symbol(feature.symbol, all_time=True))

                result = self.decision_engine.decide(
                    feature,
                    has_open_trade=has_open,
                    in_cooldown=bool(cooldown),
                )
                action = result["action"]

                if action == "PARLAYAN_ENTRY":
                    # Aday kaydı (ya yeni ya güncelleme)
                    candidate_id = storage.upsert_parlayan_candidate(feature.symbol, {
                        "price": feature.price,
                        "price_change_24h_pct": feature.price_change_24h_pct,
                        "parlayan_score": feature.parlayan_score,
                        "volume_ratio": float(feature.volume_ratio or 1.0),
                        "rsi": float(feature.rsi or 0),
                        "context": {
                            "decision": result,
                            "price_change_5m_pct": feature.price_change_5m_pct,
                            "price_change_15m_pct": feature.price_change_15m_pct,
                            "price_change_30m_pct": feature.price_change_30m_pct,
                            "momentum_score": feature.momentum_score,
                            "liquidity_score": feature.liquidity_score,
                            "fake_pump_risk": feature.fake_pump_risk,
                            "professional_metrics": feature.extra,
                        },
                    })
                    if candidate_id:
                        trade_id = self.trade_engine.maybe_open(
                            candidate_id=candidate_id,
                            symbol=feature.symbol,
                            price=feature.price,
                            context={
                                "parlayan_score": feature.parlayan_score,
                                "price_change_24h_pct": feature.price_change_24h_pct,
                                "rsi": feature.rsi,
                                "volume_ratio": feature.volume_ratio,
                                "entry_mode": "professional_paper",
                                "pre_pump_score": (feature.extra or {}).get("pre_pump_score"),
                                "market_phase": (feature.extra or {}).get("market_phase"),
                                "v4_profile": (feature.extra or {}).get("v4_profile"),
                                "entry_profile": result.get("entry_profile"),
                                "professional_metrics": feature.extra,
                            },
                        )
                        if trade_id:
                            entered += 1
                            storage.insert_signal_event(
                                feature.symbol,
                                "PAPER_ENTRY",
                                "INFO",
                                float((feature.extra or {}).get("pre_pump_score") or feature.parlayan_score),
                                feature.price,
                                {"trade_id": trade_id, "decision": result, "metrics": feature.extra, "entry_profile": result.get("entry_profile")},
                            )

                elif action == "PARLAYAN_WATCH":
                    if watched < max_watch:
                        storage.upsert_parlayan_candidate(feature.symbol, {
                            "price": feature.price,
                            "price_change_24h_pct": feature.price_change_24h_pct,
                            "parlayan_score": feature.parlayan_score,
                            "volume_ratio": float(feature.volume_ratio or 1.0),
                            "rsi": float(feature.rsi or 0),
                            "context": {"watch_reasons": result.get("reasons", []), "professional_metrics": feature.extra},
                        })
                        watched += 1
                    # V4.1: Watch da aslında entry reject sebebidir; kayda girer.
                    if self.config.get("research", {}).get("log_decision_rejections", True):
                        storage.insert_decision_event(feature.symbol, result, feature, severity="INFO")
                else:
                    rejected += 1
                    # V4.1: Her reddin nedenini kaydet. Karanlıkta kalmayacağız.
                    if self.config.get("research", {}).get("log_decision_rejections", True):
                        storage.insert_decision_event(feature.symbol, result, feature, severity="INFO")

            # 4. Pozisyon güncellemesi (fiyatlar)
            latest_prices = {f.symbol: f.price for f in features}
            self.trade_engine.update_open_trades(latest_prices, {f.symbol: f for f in features})

            # 5. Eski adayları temizle (24 saat)
            storage.expire_old_parlayan_candidates(max_hours=24)

            outcome_refresh = {}
            if self.config.get("research", {}).get("decision_quality_auto_refresh", True):
                outcome_refresh = storage.refresh_decision_outcomes(
                    hours=int(self.config.get("research", {}).get("decision_quality_hours", 36)),
                    horizons=(60, 240),
                    limit_per_horizon=int(self.config.get("research", {}).get("decision_quality_refresh_limit", 300)),
                )

            summary = {
                "ok": True,
                "scanned": len(features),
                "entered": entered,
                "watched": watched,
                "rejected": rejected,
                "market_regime": market_regime,
                "decision_outcomes_refresh": outcome_refresh,
                "top_pre_pump": [
                    {
                        "symbol": f.symbol,
                        "pre_pump_score": (f.extra or {}).get("pre_pump_score"),
                        "market_phase": (f.extra or {}).get("market_phase"),
                        "price_change_5m_pct": f.price_change_5m_pct,
                        "price_change_15m_pct": f.price_change_15m_pct,
                        "volume_ratio": f.volume_ratio,
                    }
                    for f in features[:10]
                ],
            }
            storage.log_event("INFO", "scanner", "Tarama tamamlandı", summary)
            return summary

        except Exception as exc:
            storage.log_event("ERROR", "scanner", "Tarama hatası", {"error": repr(exc)})
            return {"ok": False, "error": repr(exc)}

    async def update_positions_once(self) -> dict[str, Any]:
        """Pozisyon güncelleme (her 10-30 sn)."""
        try:
            prices = await self.client.ticker_prices()
            self.trade_engine.update_open_trades(prices)
            return {"ok": True, "prices": len(prices)}
        except Exception as exc:
            storage.log_event("ERROR", "positions", "Pozisyon güncelleme hatası", {"error": repr(exc)})
            return {"ok": False, "error": repr(exc)}

    async def run_position_monitor_loop(self) -> None:
        """Arka planda sürekli pozisyon izleme."""
        while True:
            cfg = self.config.get("position_monitor", {})
            if cfg.get("enabled", True):
                await self.update_positions_once()
            interval = int(cfg.get("interval_seconds", 15))
            await asyncio.sleep(max(5, interval))

    async def run_loop(self) -> None:
        """Ana tarama döngüsü."""
        self._running = True
        storage.set_metadata("scanner_state", {"running": True})
        while self._running:
            started = time.monotonic()
            await self.run_once()
            scan_interval = int(self.config["scanner"].get("scan_interval_seconds", 120))
            elapsed = time.monotonic() - started
            remaining = max(0, scan_interval - elapsed)
            if remaining > 0:
                await asyncio.sleep(remaining)

    def stop(self) -> None:
        self._running = False
        storage.set_metadata("scanner_state", {"running": False})
