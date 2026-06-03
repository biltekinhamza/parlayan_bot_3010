from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import storage
from .config import config_store

router = APIRouter()


# ─── Scanner Durum ───────────────────────────────────────────────────────────

@router.get("/api/status")
def get_status():
    from .main import scanner_status
    return scanner_status()


@router.get("/api/session/current")
def get_current_session():
    return storage.get_current_session()


@router.get("/api/session/list")
def get_sessions(limit: int = 50):
    return storage.get_sessions(limit=limit)


@router.post("/api/scanner/start")
def start_scanner():
    from .main import start_scanner as _start
    return _start()


@router.post("/api/scanner/stop")
def stop_scanner():
    from .main import stop_scanner as _stop
    return _stop()


@router.post("/api/scanner/scan-now")
async def scan_now():
    from .main import scanner_service
    if scanner_service is None:
        raise HTTPException(status_code=503, detail="Scanner hazır değil")
    result = await scanner_service.run_once()
    return result


# ─── Parlayan Adaylar ─────────────────────────────────────────────────────────

@router.get("/api/parlayan/candidates")
def get_candidates(all_time: bool = False):
    """Aktif izlemedeki parlayan adaylar."""
    return storage.get_active_parlayan_candidates(limit=50, all_time=all_time)


@router.get("/api/parlayan/today")
def get_today(all_time: bool = False):
    """Bugün tespit edilen en iyi parlayan coinler."""
    return storage.get_top_parlayan_today(all_time=all_time)


# ─── İşlemler ─────────────────────────────────────────────────────────────────

@router.get("/api/trades/open")
def get_open_trades(all_time: bool = False):
    return storage.get_open_paper_trades(all_time=all_time)


@router.get("/api/trades/recent")
def get_recent_trades(all_time: bool = False):
    return storage.get_recent_trades(limit=30, all_time=all_time)


@router.get("/api/trades/summary")
def get_trade_summary(all_time: bool = False):
    return storage.get_trade_summary(all_time=all_time)


# ─── Piyasa Snapshots ─────────────────────────────────────────────────────────

@router.get("/api/market/top")
def get_top_movers():
    """Son taramadaki en yüksek parlayan skorlu coinler."""
    from .db import db
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (symbol)
            symbol, price, rsi, price_change_24h_pct,
            price_change_5m_pct, price_change_15m_pct,
            volume_ratio, momentum_score, liquidity_score,
            fake_pump_risk, parlayan_score, bot_state, ts
        FROM market_snapshots
        WHERE ts > now() - interval '10 minutes'
        ORDER BY symbol, ts DESC
        """
    )
    rows.sort(key=lambda r: float(r.get("parlayan_score") or 0), reverse=True)
    return rows[:50]


@router.get("/api/market/gainers")
def get_top_gainers():
    """24h en çok yükselenler."""
    from .db import db
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (symbol)
            symbol, price, price_change_24h_pct, parlayan_score,
            volume_ratio, rsi, ts
        FROM market_snapshots
        WHERE ts > now() - interval '10 minutes'
          AND price_change_24h_pct > 5
        ORDER BY symbol, ts DESC
        """
    )
    rows.sort(key=lambda r: float(r.get("price_change_24h_pct") or 0), reverse=True)
    return rows[:30]


# ─── Olaylar ─────────────────────────────────────────────────────────────────

@router.get("/api/events")
def get_events(limit: int = 50):
    from .db import db
    return db.fetch_all(
        "SELECT * FROM bot_events ORDER BY ts DESC LIMIT %s",
        (limit,),
    )




# ─── Profesyonel Araştırma Dashboard ─────────────────────────────────────────

@router.get("/api/research/summary")
def get_research_summary(limit: int = 30):
    return storage.get_research_summary(limit=limit)


@router.get("/api/research/pre-pump")
def get_pre_pump(limit: int = 30):
    return storage.get_top_pre_pump(limit=limit)


@router.get("/api/research/timeline/{symbol}")
def get_symbol_timeline(symbol: str, hours: int = 24, limit: int = 1000):
    return storage.get_symbol_timeline(symbol.upper(), hours=hours, limit=limit)


@router.get("/api/research/events")
def get_signal_events(limit: int = 100, symbol: str | None = None):
    return storage.get_signal_events(limit=limit, symbol=symbol.upper() if symbol else None)


@router.get("/api/risk/daily")
def get_daily_risk(all_time: bool = False):
    return {
        "session": storage.get_current_session(),
        "daily": storage.get_daily_paper_stats(all_time=all_time),
        "portfolio": storage.get_paper_portfolio_state(all_time=all_time),
    }




@router.get("/api/research/pump-detective")
def get_pump_detective(threshold_pct: float = 30.0, minutes_before: int = 60, limit: int = 50):
    return storage.get_pump_detective_report(threshold_pct=threshold_pct, minutes_before=minutes_before, limit=limit)


@router.get("/api/research/winning-patterns")
def get_winning_patterns(all_time: bool = False):
    return storage.get_winning_pattern_report(all_time=all_time)


# ─── Config ───────────────────────────────────────────────────────────────────

@router.get("/api/config")
def get_config():
    return config_store.get()


class ConfigPatch(BaseModel):
    patch: dict[str, Any]
    reason: str | None = None


@router.post("/api/config/update")
def update_config(body: ConfigPatch):
    new_cfg = config_store.update(body.patch)
    from .main import apply_runtime_config
    result = apply_runtime_config(new_cfg, reason=body.reason or "dashboard_update")
    return {"ok": True, "config": new_cfg, **result}


@router.post("/api/config/reset")
def reset_config():
    """Diski yeniden oku (elle düzenlediysen)."""
    from .config import _load_from_disk
    cfg = _load_from_disk()
    config_store.replace(cfg)
    from .main import apply_runtime_config
    apply_runtime_config(cfg, reason="manual_reset")
    return {"ok": True}



# ─── V4.1 Integrity / Risk / Pump Detective ──────────────────────────────────

@router.get("/api/research/reject-reasons")
def get_reject_reasons(hours: int = 24, limit: int = 100):
    return storage.get_reject_reason_report(hours=hours, limit=limit)


@router.get("/api/research/pump-detective-v2")
def get_pump_detective_v2(threshold_pct: float = 30.0, limit: int = 100):
    return storage.get_pump_detective_v2_report(threshold_pct=threshold_pct, limit=limit)


@router.get("/api/risk/portfolio-global")
def get_global_portfolio():
    starting = float(config_store.get().get("paper_trading", {}).get("starting_equity_usdt", 1000))
    return {
        "session": storage.get_current_session(),
        "portfolio_global": storage.get_paper_portfolio_state(starting_equity_usdt=starting, all_time=True),
        "daily_global": storage.get_daily_paper_stats(all_time=True),
        "open_trades_global": storage.get_open_paper_trades(all_time=True),
    }


# ─── V4.2 Research / Velocity / Daily Integrity ──────────────────────────────

@router.get("/api/research/fast-alerts")
def get_fast_alerts(hours: int = 24, limit: int = 100):
    return storage.get_fast_alerts(hours=hours, limit=limit)


@router.get("/api/research/velocity")
def get_velocity_report(hours: int = 24, limit: int = 100):
    return storage.get_velocity_research_report(hours=hours, limit=limit)


@router.get("/api/research/daily-report")
def get_daily_report(day: str | None = None, all_time: bool = False):
    return storage.get_daily_signal_report(day=day, all_time=all_time)



# ─── V4.3 Decision Quality / Near Miss / Market Regime ──────────────────────

@router.post("/api/research/decision-outcomes/refresh")
def refresh_decision_outcomes(hours: int = 36):
    return storage.refresh_decision_outcomes(hours=hours)


@router.get("/api/research/decision-quality")
def get_decision_quality(hours: int = 36, horizon_minutes: int = 240, auto_refresh: bool = True):
    return storage.get_decision_quality_report(hours=hours, horizon_minutes=horizon_minutes, auto_refresh=auto_refresh)


@router.get("/api/research/danger-quality")
def get_danger_quality(hours: int = 36, horizon_minutes: int = 240):
    return storage.get_danger_filter_quality(hours=hours, horizon_minutes=horizon_minutes)


@router.get("/api/research/near-misses")
def get_near_misses(hours: int = 36, horizon_minutes: int = 240, min_upside_pct: float = 5.0, limit: int = 100):
    return storage.get_near_miss_report(
        hours=hours,
        horizon_minutes=horizon_minutes,
        min_upside_pct=min_upside_pct,
        limit=limit,
    )


@router.get("/api/research/pre-pump-alert-quality")
def get_pre_pump_alert_quality(hours: int = 36, horizon_minutes: int = 240, limit: int = 100):
    return storage.get_pre_pump_alert_quality(hours=hours, horizon_minutes=horizon_minutes, limit=limit)


@router.get("/api/research/market-regime")
def get_market_regime(hours: int = 24):
    return storage.get_market_regime_report(hours=hours)


@router.get("/api/reports/v44-quality")
def get_v44_quality_report(hours: int = 24, all_time: bool = True):
    return storage.get_v44_trade_quality_report(hours=hours, all_time=all_time)



# ─── V4.6 Pattern Memory / Market DNA ────────────────────────────────────────

@router.get("/api/research/pattern-memory")
def get_pattern_memory(hours: int = 72, threshold_pct: float | None = None, limit: int = 100):
    return storage.get_pattern_memory_report(hours=hours, threshold_pct=threshold_pct, limit=limit)


@router.get("/api/research/market-dna")
def get_market_dna(limit: int = 100, refresh: bool = False):
    return storage.get_market_dna_report(limit=limit, refresh=refresh)


@router.post("/api/research/market-dna/refresh")
def refresh_market_dna():
    return storage.refresh_market_dna_profiles()


# ─── Documentation compatible aliases ────────────────────────────────────────

@router.get("/api/reports/daily")
def reports_daily(day: str | None = None, all_time: bool = False):
    return storage.get_daily_signal_report(day=day, all_time=all_time)


@router.get("/api/reports/velocity")
def reports_velocity(hours: int = 24, limit: int = 100):
    return storage.get_velocity_research_report(hours=hours, limit=limit)


@router.get("/api/reports/pump-alarms")
def reports_pump_alarms(hours: int = 24, limit: int = 100):
    return storage.get_fast_alerts(hours=hours, limit=limit)


@router.get("/api/reports/decision-quality")
def reports_decision_quality(hours: int = 36, horizon_minutes: int = 240, auto_refresh: bool = True):
    return storage.get_decision_quality_report(hours=hours, horizon_minutes=horizon_minutes, auto_refresh=auto_refresh)


@router.get("/api/reports/reject-outcomes")
def reports_reject_outcomes(hours: int = 36, horizon_minutes: int = 240, min_upside_pct: float = 5.0, limit: int = 100):
    return storage.get_near_miss_report(hours=hours, horizon_minutes=horizon_minutes, min_upside_pct=min_upside_pct, limit=limit)


@router.get("/api/reports/pattern-memory")
def reports_pattern_memory(hours: int = 72, threshold_pct: float | None = None, limit: int = 100):
    return storage.get_pattern_memory_report(hours=hours, threshold_pct=threshold_pct, limit=limit)


@router.get("/api/reports/market-dna")
def reports_market_dna(limit: int = 100, refresh: bool = False):
    return storage.get_market_dna_report(limit=limit, refresh=refresh)


@router.get("/api/reports/adaptive-dna")
def reports_adaptive_dna():
    from .adaptive_dna_thresholds import AdaptiveDNAThresholds
    cfg = config_store.get()
    parlayan_cfg = cfg.get("strategy", {}).get("parlayan", {})
    base = {
        "min_volume_ratio": float(parlayan_cfg.get("min_volume_ratio_for_entry", 0.75)),
        "min_velocity_score": float(parlayan_cfg.get("min_velocity_score_for_entry", 58.0)),
        "min_directional_volume_score": float(parlayan_cfg.get("min_directional_volume_score_for_entry", 52.0)),
        "min_parlayan_score": float(parlayan_cfg.get("min_parlayan_score_for_entry", 48.0)),
        "min_pre_pump_score": float(parlayan_cfg.get("min_pre_pump_score_for_entry", 64.0)),
    }
    return {
        "version": "adaptive_dna_thresholds_v46",
        "base": base,
        "resolved": AdaptiveDNAThresholds(cfg).resolve(base, "NEUTRAL").as_dict(),
    }


@router.get("/api/reports/stable-filter")
def reports_stable_filter():
    from .stable_asset_filter import get_stable_base_assets, get_blocked_symbols
    cfg = config_store.get()
    return {
        "version": "stable_asset_filter_v461",
        "enabled": bool(cfg.get("stable_asset_filter", {}).get("enabled", True)),
        "stable_base_assets": sorted(get_stable_base_assets(cfg)),
        "blocked_symbols": sorted(get_blocked_symbols(cfg)),
    }
