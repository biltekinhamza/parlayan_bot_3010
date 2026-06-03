from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import db, jsonb

STRATEGY_VERSION = "professional_paper_v45"
MODE = "paper"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Events ──────────────────────────────────────────────────────────────────

def log_event(level: str, category: str, message: str, details: dict[str, Any] | None = None) -> None:
    db.execute(
        "INSERT INTO bot_events(level, category, message, details) VALUES (%s, %s, %s, %s)",
        (level, category, message, jsonb(details or {})),
    )


# ─── Metadata ────────────────────────────────────────────────────────────────

def set_metadata(key: str, value: dict[str, Any]) -> None:
    db.execute(
        """
        INSERT INTO app_metadata(key, value, updated_at) VALUES (%s, %s, now())
        ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
        """,
        (key, jsonb(value)),
    )


def get_metadata(key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    row = db.fetch_one("SELECT value FROM app_metadata WHERE key=%s", (key,))
    return dict(row["value"]) if row and row.get("value") is not None else (default or {})


# ─── Paper Session Management ────────────────────────────────────────────────

def start_paper_session(config_snapshot: dict[str, Any] | None = None, strategy_version: str = STRATEGY_VERSION) -> dict[str, Any]:
    """
    Her uygulama başlangıcında yeni bir paper-trade oturumu açar.
    Eski kayıtlar korunur; dashboard ve risk hesapları varsayılan olarak bu oturumu gösterir.
    """
    session_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{strategy_version}"
    row = db.fetch_one(
        """
        INSERT INTO paper_sessions(session_name, strategy_version, mode, status, config_snapshot)
        VALUES (%s, %s, %s, 'RUNNING', %s)
        RETURNING id, session_name, strategy_version, mode, started_at, status
        """,
        (session_name, strategy_version, MODE, jsonb(config_snapshot or {})),
    )
    session = dict(row) if row else {}
    set_metadata("current_paper_session", {
        "session_id": str(session.get("id", "")),
        "session_name": session.get("session_name"),
        "strategy_version": strategy_version,
        "mode": MODE,
        "started_at": session.get("started_at"),
    })
    log_event("INFO", "session", "Yeni paper session başlatıldı", session)
    return session


def get_current_session() -> dict[str, Any]:
    data = get_metadata("current_paper_session", {})
    return {
        "session_id": data.get("session_id"),
        "session_name": data.get("session_name"),
        "strategy_version": data.get("strategy_version") or STRATEGY_VERSION,
        "mode": data.get("mode") or MODE,
        "started_at": data.get("started_at"),
    }


def get_current_session_id() -> str | None:
    session_id = get_current_session().get("session_id")
    return str(session_id) if session_id else None


def get_sessions(limit: int = 50) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT
            s.*,
            COUNT(t.id) FILTER (WHERE t.status='OPEN') AS open_trades,
            COUNT(t.id) FILTER (WHERE t.status='CLOSED') AS closed_trades,
            COUNT(t.id) FILTER (WHERE t.status='CLOSED' AND t.pnl_pct > 0) AS wins,
            COUNT(t.id) FILTER (WHERE t.status='CLOSED' AND t.pnl_pct <= 0) AS losses,
            COALESCE(SUM(t.pnl_quote) FILTER (WHERE t.status='CLOSED'), 0) AS total_pnl_usdt
        FROM paper_sessions s
        LEFT JOIN paper_trades t ON t.session_id = s.id
        GROUP BY s.id
        ORDER BY s.started_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def _session_filter_sql(alias: str = "", all_time: bool = False) -> tuple[str, tuple[Any, ...]]:
    if all_time:
        return "", ()
    session_id = get_current_session_id()
    if not session_id:
        return " AND 1=0", ()
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}session_id = %s", (session_id,)



# ─── Symbols ─────────────────────────────────────────────────────────────────

def upsert_symbols(symbols: list) -> None:
    rows = [(s.symbol, s.base_asset, s.quote_asset, s.status, s.is_spot_trading_allowed, jsonb(s.filters)) for s in symbols]
    db.executemany(
        """
        INSERT INTO symbol_universe(symbol, base_asset, quote_asset, status, is_spot_trading_allowed, filters)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            status = EXCLUDED.status,
            is_spot_trading_allowed = EXCLUDED.is_spot_trading_allowed,
            last_seen_at = now()
        """,
        rows,
    )


# ─── Market Snapshots ────────────────────────────────────────────────────────

def insert_market_snapshots(features: list) -> None:
    rows = [
        (
            f.ts, f.symbol, f.price, f.rsi,
            f.price_change_24h_pct, f.price_change_15m_pct, f.price_change_5m_pct, f.price_change_30m_pct,
            f.quote_volume_24h, f.trade_count_24h, f.spread_pct, f.volume_ratio,
            f.momentum_score, f.liquidity_score, f.fake_pump_risk, f.parlayan_score,
            f.wick_body_ratio, f.bot_state, jsonb(f.extra),
        )
        for f in features
    ]
    db.executemany(
        """
        INSERT INTO market_snapshots(
            ts, symbol, price, rsi,
            price_change_24h_pct, price_change_15m_pct, price_change_5m_pct, price_change_30m_pct,
            quote_volume_24h, trade_count_24h, spread_pct, volume_ratio,
            momentum_score, liquidity_score, fake_pump_risk, parlayan_score,
            wick_body_ratio, bot_state, extra
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        rows,
    )


# ─── Parlayan Candidates ─────────────────────────────────────────────────────

def upsert_parlayan_candidate(symbol: str, data: dict[str, Any]) -> str:
    """Yeni aday ekle ya da mevcut session adayını güncelle."""
    current = get_current_session()
    session_id = current.get("session_id")
    strategy_version = current.get("strategy_version") or STRATEGY_VERSION
    mode = current.get("mode") or MODE

    existing = db.fetch_one(
        """
        SELECT id FROM parlayan_candidates
        WHERE symbol=%s
          AND status IN ('WATCHING','ENTERED')
          AND session_id=%s
        """,
        (symbol, session_id),
    )
    context = dict(data.get("context", {}) or {})
    context.update({
        "session_id": session_id,
        "strategy_version": strategy_version,
        "mode": mode,
    })
    if existing:
        db.execute(
            """
            UPDATE parlayan_candidates SET
                parlayan_score=%s, volume_ratio=%s, rsi=%s,
                price_change_24h_pct=%s, last_seen_at=now(), context=%s,
                strategy_version=%s, mode=%s
            WHERE id=%s
            """,
            (
                data["parlayan_score"], data["volume_ratio"], data["rsi"],
                data["price_change_24h_pct"], jsonb(context),
                strategy_version, mode, existing["id"],
            ),
        )
        return str(existing["id"])
    row = db.fetch_one(
        """
        INSERT INTO parlayan_candidates(
            session_id, strategy_version, mode,
            symbol, detected_at, price_at_detection,
            price_change_24h_pct, parlayan_score, volume_ratio, rsi, status, context
        ) VALUES (%s, %s, %s, %s, now(), %s, %s, %s, %s, %s, 'WATCHING', %s)
        RETURNING id
        """,
        (
            session_id, strategy_version, mode,
            symbol, data["price"], data["price_change_24h_pct"],
            data["parlayan_score"], data["volume_ratio"], data["rsi"],
            jsonb(context),
        ),
    )
    return str(row["id"]) if row else ""


def get_active_parlayan_candidates(limit: int = 30, all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"""
        SELECT * FROM parlayan_candidates
        WHERE status='WATCHING' {session_filter}
        ORDER BY parlayan_score DESC
        LIMIT %s
        """,
        (*params, limit),
    )


def expire_old_parlayan_candidates(max_hours: int = 24) -> int:
    """24 saatten eski WATCHING adayları EXPIRED yap."""
    db.execute(
        """
        UPDATE parlayan_candidates
        SET status='EXPIRED', closed_at=now()
        WHERE status='WATCHING' AND detected_at < now() - (%s || ' hours')::interval
          AND session_id = %s
        """,
        (max_hours, get_current_session_id()),
    )
    result = db.fetch_one(
        "SELECT COUNT(*) AS c FROM parlayan_candidates WHERE status='EXPIRED' AND closed_at > now() - interval '1 minute'"
    )
    return int(result["c"]) if result else 0


def update_parlayan_candidate_peak(candidate_id: str, peak_gain_pct: float) -> None:
    db.execute(
        "UPDATE parlayan_candidates SET peak_gain_pct=%s WHERE id=%s",
        (peak_gain_pct, candidate_id),
    )


# ─── Paper Trades ─────────────────────────────────────────────────────────────

def open_paper_trade(
    candidate_id: str,
    symbol: str,
    entry_price: float,
    quote_size: float,
    cfg: dict[str, Any],
    context: dict[str, Any],
) -> str | None:
    current = get_current_session()
    session_id = current.get("session_id")
    strategy_version = current.get("strategy_version") or STRATEGY_VERSION
    mode = current.get("mode") or MODE
    trade_context = dict(context or {})
    trade_context.update({
        "session_id": session_id,
        "strategy_version": strategy_version,
        "mode": mode,
    })
    row = db.fetch_one(
        """
        INSERT INTO paper_trades(
            session_id, strategy_version, mode,
            candidate_id, symbol, status, entry_price, max_price,
            quote_size, fee_rate_estimate, slippage_pct_estimate,
            stop_loss_pct, trailing_start_pct, trailing_gap_pct,
            take_profit_pct, context
        ) VALUES (%s,%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
        """,
        (
            session_id, strategy_version, mode,
            candidate_id, symbol, entry_price, entry_price,
            quote_size,
            float(cfg.get("fee_rate_estimate", 0.001)),
            float(cfg.get("slippage_pct_estimate", 0.05)),
            float(cfg.get("stop_loss_pct", 2.5)),
            float(cfg.get("trailing_start_pct", 7.0)),
            float(cfg.get("trailing_gap_pct", 3.5)),
            float(cfg.get("take_profit_pct", 15.0)),
            jsonb(trade_context),
        ),
    )
    if row:
        # Candidate'ı ENTERED yap
        db.execute(
            "UPDATE parlayan_candidates SET status='ENTERED', entry_price=%s WHERE id=%s",
            (entry_price, candidate_id),
        )
        log_event("INFO", "trade", f"Paper trade açıldı: {symbol}", {
            "trade_id": str(row["id"]), "entry": entry_price, "size": quote_size,
        })
        return str(row["id"])
    return None


def get_open_paper_trades(all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"SELECT * FROM paper_trades WHERE status='OPEN' {session_filter} ORDER BY entry_ts DESC",
        params,
    )


def get_open_trades_for_symbol(symbol: str, all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"SELECT * FROM paper_trades WHERE symbol=%s AND status='OPEN' {session_filter}",
        (symbol, *params),
    )


def update_trade_price(trade_id: str, current_price: float, max_price: float, protection_stop: float | None, protection_state: dict[str, Any]) -> None:
    db.execute(
        """
        UPDATE paper_trades SET
            last_price=%s, max_price=%s,
            protected_stop_price=%s, protection_state=%s,
            last_update_ts=now()
        WHERE id=%s
        """,
        (current_price, max_price, protection_stop, jsonb(protection_state), trade_id),
    )


def close_paper_trade(trade_id: str, exit_price: float, exit_reason: str, pnl_pct: float, pnl_quote: float) -> None:
    row = db.fetch_one(
        """
        UPDATE paper_trades SET
            status='CLOSED', exit_price=%s, exit_reason=%s,
            pnl_pct=%s, pnl_quote=%s, exit_ts=now()
        WHERE id=%s
        RETURNING candidate_id, symbol
        """,
        (exit_price, exit_reason, pnl_pct, pnl_quote, trade_id),
    )

    if row and row.get("candidate_id"):
        db.execute(
            """
            UPDATE parlayan_candidates SET
                status='CLOSED',
                closed_at=now(),
                peak_gain_pct=GREATEST(
                    peak_gain_pct,
                    COALESCE(((%s - NULLIF(entry_price, 0)) / NULLIF(entry_price, 0)) * 100, 0)
                ),
                context = context || %s
            WHERE id=%s
            """,
            (
                exit_price,
                jsonb({
                    "last_trade_id": trade_id,
                    "exit_reason": exit_reason,
                    "pnl_pct": pnl_pct,
                    "pnl_quote": pnl_quote,
                }),
                row["candidate_id"],
            ),
        )

    log_event("INFO", "trade", f"Trade kapatıldı: {exit_reason}", {
        "trade_id": trade_id, "symbol": row.get("symbol") if row else None,
        "pnl_pct": pnl_pct, "pnl_quote": pnl_quote,
    })


# ─── Cooldowns ────────────────────────────────────────────────────────────────

def get_active_cooldown(symbol: str) -> dict[str, Any] | None:
    return db.fetch_one(
        "SELECT * FROM cooldowns WHERE symbol=%s AND until_ts > now()",
        (symbol,),
    )


def set_cooldown(symbol: str, reason: str, until: datetime, details: dict[str, Any] | None = None) -> None:
    db.execute(
        """
        INSERT INTO cooldowns(symbol, reason, until_ts, details)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            reason=EXCLUDED.reason, until_ts=EXCLUDED.until_ts, details=EXCLUDED.details
        """,
        (symbol, reason, until, jsonb(details or {})),
    )


def cleanup_expired_cooldowns() -> None:
    db.execute("DELETE FROM cooldowns WHERE until_ts <= now()")


# ─── Trade Stats (Dashboard) ─────────────────────────────────────────────────

def get_trade_summary(all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COUNT(*) FILTER (WHERE status='CLOSED') AS closed_trades,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct > 0) AS wins,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct <= 0) AS losses,
            AVG(pnl_pct) FILTER (WHERE status='CLOSED') AS avg_pnl,
            SUM(pnl_quote) FILTER (WHERE status='CLOSED') AS total_pnl_usdt,
            MAX(pnl_pct) FILTER (WHERE status='CLOSED') AS best_trade_pct,
            MIN(pnl_pct) FILTER (WHERE status='CLOSED') AS worst_trade_pct
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    result = dict(row) if row else {}
    result["session"] = get_current_session()
    result["all_time"] = all_time
    return result


def get_recent_trades(limit: int = 20, all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"""
        SELECT * FROM paper_trades
        WHERE 1=1 {session_filter}
        ORDER BY entry_ts DESC
        LIMIT %s
        """,
        (*params, limit),
    )


def get_top_parlayan_today(all_time: bool = False) -> list[dict[str, Any]]:
    """Bugün tespit edilen en iyi parlayan adaylar."""
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"""
        SELECT * FROM parlayan_candidates
        WHERE detected_at > now() - interval '24 hours' {session_filter}
        ORDER BY price_change_24h_pct DESC
        LIMIT 20
        """,
        params,
    )


# ─── Professional Research / Risk Helpers ────────────────────────────────────

def get_latest_market_snapshot(symbol: str) -> dict[str, Any] | None:
    return db.fetch_one(
        """
        SELECT * FROM market_snapshots
        WHERE symbol=%s
        ORDER BY ts DESC
        LIMIT 1
        """,
        (symbol,),
    )


def insert_signal_event(
    symbol: str,
    event_type: str,
    severity: str = "INFO",
    score: float | None = None,
    price: float | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    current = get_current_session()
    event_details = dict(details or {})
    event_details.update({
        "session_id": current.get("session_id"),
        "strategy_version": current.get("strategy_version") or STRATEGY_VERSION,
        "mode": current.get("mode") or MODE,
    })
    db.execute(
        """
        INSERT INTO signal_events(
            session_id, strategy_version, mode,
            symbol, event_type, severity, score, price, details
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            current.get("session_id"),
            current.get("strategy_version") or STRATEGY_VERSION,
            current.get("mode") or MODE,
            symbol, event_type, severity, score, price, jsonb(event_details),
        ),
    )


def get_signal_events(limit: int = 100, symbol: str | None = None, all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    if symbol:
        return db.fetch_all(
            f"SELECT * FROM signal_events WHERE symbol=%s {session_filter} ORDER BY ts DESC LIMIT %s",
            (symbol, *params, limit),
        )
    return db.fetch_all(
        f"SELECT * FROM signal_events WHERE 1=1 {session_filter} ORDER BY ts DESC LIMIT %s",
        (*params, limit),
    )


def get_symbol_timeline(symbol: str, hours: int = 24, limit: int = 1000) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT
            ts, symbol, price, rsi, price_change_24h_pct, price_change_5m_pct,
            price_change_15m_pct, price_change_30m_pct, quote_volume_24h,
            trade_count_24h, spread_pct, volume_ratio, momentum_score,
            liquidity_score, fake_pump_risk, parlayan_score, wick_body_ratio,
            bot_state, extra
        FROM market_snapshots
        WHERE symbol=%s AND ts > now() - (%s || ' hours')::interval
        ORDER BY ts ASC
        LIMIT %s
        """,
        (symbol, hours, limit),
    )


def get_top_pre_pump(limit: int = 30) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (symbol)
            symbol, ts, price, rsi, price_change_24h_pct, price_change_5m_pct,
            price_change_15m_pct, price_change_30m_pct, volume_ratio,
            momentum_score, liquidity_score, fake_pump_risk, parlayan_score,
            spread_pct, extra
        FROM market_snapshots
        WHERE ts > now() - interval '15 minutes'
        ORDER BY symbol, ts DESC
        """
    )
    rows.sort(key=lambda r: float((r.get("extra") or {}).get("pre_pump_score") or 0), reverse=True)
    return rows[:limit]


def get_daily_paper_stats(all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date) AS closed_today,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct > 0 AND exit_ts::date = now()::date) AS wins_today,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct <= 0 AND exit_ts::date = now()::date) AS losses_today,
            COALESCE(SUM(pnl_quote) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date), 0) AS daily_pnl_usdt,
            COALESCE(AVG(pnl_pct) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date), 0) AS avg_pnl_today
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    return dict(row) if row else {}


def get_paper_portfolio_state(starting_equity_usdt: float = 1000.0, all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COALESCE(SUM(pnl_quote) FILTER (WHERE status='CLOSED'), 0) AS realized_pnl_usdt,
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COALESCE(SUM(quote_size) FILTER (WHERE status='OPEN'), 0) AS open_notional_usdt
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    realized = float(row.get("realized_pnl_usdt") or 0) if row else 0.0
    open_trades = int(row.get("open_trades") or 0) if row else 0
    open_notional = float(row.get("open_notional_usdt") or 0) if row else 0.0
    equity = float(starting_equity_usdt) + realized
    return {
        "starting_equity_usdt": float(starting_equity_usdt),
        "realized_pnl_usdt": realized,
        "equity_usdt": equity,
        "open_trades": open_trades,
        "open_notional_usdt": open_notional,
    }


def record_equity_snapshot(starting_equity_usdt: float = 1000.0) -> None:
    portfolio = get_paper_portfolio_state(starting_equity_usdt)
    daily = get_daily_paper_stats()
    db.execute(
        """
        INSERT INTO paper_equity_curve(
            session_id, strategy_version, mode,
            equity_usdt, realized_pnl_usdt, open_risk_usdt,
            open_trades, daily_pnl_usdt, max_drawdown_pct, details
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            get_current_session_id(), STRATEGY_VERSION, MODE,
            portfolio["equity_usdt"],
            portfolio["realized_pnl_usdt"],
            portfolio["open_notional_usdt"],
            portfolio["open_trades"],
            daily.get("daily_pnl_usdt") or 0,
            0,
            jsonb({"portfolio": portfolio, "daily": daily, "session": get_current_session()}),
        ),
    )


def get_research_summary(limit: int = 30) -> dict[str, Any]:
    return {
        "top_pre_pump": get_top_pre_pump(limit),
        "trade_summary": get_trade_summary(),
        "daily": get_daily_paper_stats(),
        "portfolio": get_paper_portfolio_state(),
        "events": get_signal_events(limit=limit),
    }


# ─── Pump Detective v4 ───────────────────────────────────────────────────────

def get_pump_detective_report(threshold_pct: float = 30.0, minutes_before: int = 60, limit: int = 50) -> dict[str, Any]:
    """
    Geçmiş snapshotlardan pump zirvesi öncesi davranışı çıkarır.
    threshold_pct: max 24h değişimi bu değerin üstünde olan coinler.
    minutes_before: zirveden kaç dakika önceki pencere analiz edilecek.
    """
    window_low = max(0, minutes_before - 5)
    window_high = minutes_before + 5
    rows = db.fetch_all(
        """
        WITH pumps AS (
          SELECT symbol, MAX(price_change_24h_pct) AS max_24h
          FROM market_snapshots
          GROUP BY symbol
          HAVING MAX(price_change_24h_pct) >= %s
        ),
        peak_times AS (
          SELECT DISTINCT ON (ms.symbol)
            ms.symbol,
            ms.ts AS peak_ts,
            ms.price_change_24h_pct AS peak_24h
          FROM market_snapshots ms
          JOIN pumps p ON p.symbol = ms.symbol
          ORDER BY ms.symbol, ms.price_change_24h_pct DESC
        ),
        before_peak AS (
          SELECT
            ms.symbol,
            pt.peak_ts,
            pt.peak_24h,
            ms.ts,
            EXTRACT(EPOCH FROM (pt.peak_ts - ms.ts)) / 60 AS minutes_before_peak,
            ms.price,
            ms.rsi,
            ms.volume_ratio,
            ms.price_change_5m_pct,
            ms.price_change_15m_pct,
            ms.price_change_30m_pct,
            ms.price_change_24h_pct,
            ms.parlayan_score,
            ms.bot_state,
            ms.extra
          FROM market_snapshots ms
          JOIN peak_times pt ON pt.symbol = ms.symbol
          WHERE ms.ts BETWEEN pt.peak_ts - (%s || ' minutes')::interval - interval '5 minutes'
                          AND pt.peak_ts - (%s || ' minutes')::interval + interval '5 minutes'
        )
        SELECT *
        FROM before_peak
        WHERE minutes_before_peak BETWEEN %s AND %s
        ORDER BY peak_24h DESC, symbol, ts DESC
        LIMIT %s
        """,
        (threshold_pct, minutes_before, minutes_before, window_low, window_high, limit),
    )
    if not rows:
        return {
            "threshold_pct": threshold_pct,
            "minutes_before": minutes_before,
            "rows": [],
            "summary": {},
        }

    def avg(key: str) -> float:
        vals = []
        for row in rows:
            try:
                value = row.get(key)
                if value is not None:
                    vals.append(float(value))
            except Exception:
                pass
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    phases: dict[str, int] = {}
    profiles: dict[str, int] = {}
    for row in rows:
        extra = row.get("extra") or {}
        phase = str(extra.get("market_phase") or row.get("bot_state") or "UNKNOWN")
        profile = str(extra.get("v4_profile") or "UNKNOWN")
        phases[phase] = phases.get(phase, 0) + 1
        profiles[profile] = profiles.get(profile, 0) + 1

    return {
        "threshold_pct": threshold_pct,
        "minutes_before": minutes_before,
        "row_count": len(rows),
        "summary": {
            "avg_peak_24h": avg("peak_24h"),
            "avg_rsi": avg("rsi"),
            "avg_volume_ratio": avg("volume_ratio"),
            "avg_5m": avg("price_change_5m_pct"),
            "avg_15m": avg("price_change_15m_pct"),
            "avg_30m": avg("price_change_30m_pct"),
            "avg_parlayan_score": avg("parlayan_score"),
            "phase_counts": phases,
            "profile_counts": profiles,
        },
        "rows": rows,
    }


def get_winning_pattern_report(all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    rows = db.fetch_all(
        f"""
        SELECT
            status,
            exit_reason,
            context->>'entry_profile' AS entry_profile,
            context->>'market_phase' AS market_phase,
            context->>'v4_profile' AS v4_profile,
            COUNT(*) AS trades,
            COUNT(*) FILTER (WHERE pnl_pct > 0) AS wins,
            COALESCE(AVG(pnl_pct), 0) AS avg_pnl_pct,
            COALESCE(SUM(pnl_quote), 0) AS total_pnl_usdt,
            COALESCE(MAX(pnl_pct), 0) AS best_pnl_pct,
            COALESCE(MIN(pnl_pct), 0) AS worst_pnl_pct
        FROM paper_trades
        WHERE status='CLOSED' {session_filter}
        GROUP BY status, exit_reason, entry_profile, market_phase, v4_profile
        ORDER BY total_pnl_usdt DESC, avg_pnl_pct DESC
        LIMIT 100
        """,
        params,
    )
    return {
        "session": get_current_session(),
        "all_time": all_time,
        "rows": rows,
    }


# ─── V4.1 Integrity / Risk / Research Overrides ──────────────────────────────
# Bu bölüm aynı isimli bazı fonksiyonları bilinçli olarak yeniden tanımlar.
# Amaç: session restart sonrası eski açık pozisyonları riskten düşürmemek,
# unrealized PnL'i hesaba katmak ve Pump Detective v2 raporlarını üretmek.

STRATEGY_VERSION = "professional_paper_v45"


def start_paper_session(config_snapshot: dict[str, Any] | None = None, strategy_version: str = STRATEGY_VERSION) -> dict[str, Any]:
    """
    V4.1: Her restart yeni session açar ama eski açık pozisyonlar riskten düşmez.
    Önceki RUNNING session'lar metadata olarak korunur; açık trade'ler global riskte izlenir.
    """
    carryover = db.fetch_one(
        """
        SELECT
            COUNT(*) AS open_trades,
            COALESCE(SUM(quote_size), 0) AS open_notional_usdt,
            ARRAY_REMOVE(ARRAY_AGG(DISTINCT symbol), NULL) AS symbols
        FROM paper_trades
        WHERE status='OPEN'
        """
    ) or {}
    session_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{strategy_version}"
    row = db.fetch_one(
        """
        INSERT INTO paper_sessions(session_name, strategy_version, mode, status, config_snapshot, notes)
        VALUES (%s, %s, %s, 'RUNNING', %s, %s)
        RETURNING id, session_name, strategy_version, mode, started_at, status
        """,
        (
            session_name,
            strategy_version,
            MODE,
            jsonb(config_snapshot or {}),
            f"carryover_open_trades={carryover.get('open_trades', 0)}",
        ),
    )
    session = dict(row) if row else {}
    set_metadata("current_paper_session", {
        "session_id": str(session.get("id", "")),
        "session_name": session.get("session_name"),
        "strategy_version": strategy_version,
        "mode": MODE,
        "started_at": session.get("started_at"),
        "carryover_open_trades": int(carryover.get("open_trades") or 0),
        "carryover_symbols": carryover.get("symbols") or [],
    })
    log_event("INFO", "session", "Yeni paper session başlatıldı; eski açık pozisyonlar global riskte izlenecek", {
        **session,
        "carryover": {
            "open_trades": int(carryover.get("open_trades") or 0),
            "open_notional_usdt": float(carryover.get("open_notional_usdt") or 0),
            "symbols": carryover.get("symbols") or [],
        },
    })
    return session


def get_open_paper_trades(all_time: bool = False) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"SELECT * FROM paper_trades WHERE status='OPEN' {session_filter} ORDER BY entry_ts DESC",
        params,
    )


def get_open_trades_for_symbol(symbol: str, all_time: bool = True) -> list[dict[str, Any]]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    return db.fetch_all(
        f"SELECT * FROM paper_trades WHERE symbol=%s AND status='OPEN' {session_filter}",
        (symbol, *params),
    )


def get_recent_entry_count(symbol: str, hours: int = 12, all_time: bool = True) -> int:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT COUNT(*) AS c
        FROM paper_trades
        WHERE symbol=%s
          AND entry_ts > now() - (%s || ' hours')::interval
          {session_filter}
        """,
        (symbol, hours, *params),
    )
    return int(row.get("c", 0)) if row else 0


def get_daily_paper_stats(all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date) AS closed_today,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct > 0 AND exit_ts::date = now()::date) AS wins_today,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct <= 0 AND exit_ts::date = now()::date) AS losses_today,
            COALESCE(SUM(pnl_quote) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date), 0) AS realized_daily_pnl_usdt,
            COALESCE(AVG(pnl_pct) FILTER (WHERE status='CLOSED' AND exit_ts::date = now()::date), 0) AS avg_pnl_today
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    realized = dict(row) if row else {}
    unrealized = get_unrealized_pnl(all_time=all_time)
    realized_daily = float(realized.get("realized_daily_pnl_usdt") or 0)
    unrealized_daily = float(unrealized.get("unrealized_pnl_usdt") or 0)
    realized["daily_pnl_usdt"] = realized_daily + unrealized_daily
    realized["realized_daily_pnl_usdt"] = realized_daily
    realized["unrealized_pnl_usdt"] = unrealized_daily
    realized["open_trades"] = unrealized.get("open_trades", 0)
    realized["all_time"] = all_time
    return realized


def get_unrealized_pnl(all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COALESCE(SUM(quote_size) FILTER (WHERE status='OPEN'), 0) AS open_notional_usdt,
            COALESCE(SUM(
                CASE
                    WHEN status='OPEN' AND COALESCE(last_price, entry_price) > 0 AND entry_price > 0
                    THEN quote_size * ((((COALESCE(last_price, entry_price) - entry_price) / entry_price) * 100)
                        - (fee_rate_estimate * 2 * 100)) / 100
                    ELSE 0
                END
            ), 0) AS unrealized_pnl_usdt,
            COALESCE(AVG(
                CASE
                    WHEN status='OPEN' AND COALESCE(last_price, entry_price) > 0 AND entry_price > 0
                    THEN (((COALESCE(last_price, entry_price) - entry_price) / entry_price) * 100)
                        - (fee_rate_estimate * 2 * 100)
                    ELSE NULL
                END
            ), 0) AS avg_unrealized_pnl_pct
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    return dict(row) if row else {
        "open_trades": 0,
        "open_notional_usdt": 0,
        "unrealized_pnl_usdt": 0,
        "avg_unrealized_pnl_pct": 0,
    }


def get_paper_portfolio_state(starting_equity_usdt: float = 1000.0, all_time: bool = False) -> dict[str, Any]:
    session_filter, params = _session_filter_sql(all_time=all_time)
    row = db.fetch_one(
        f"""
        SELECT
            COALESCE(SUM(pnl_quote) FILTER (WHERE status='CLOSED'), 0) AS realized_pnl_usdt,
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COALESCE(SUM(quote_size) FILTER (WHERE status='OPEN'), 0) AS open_notional_usdt
        FROM paper_trades
        WHERE 1=1 {session_filter}
        """,
        params,
    )
    unrealized = get_unrealized_pnl(all_time=all_time)
    realized = float(row.get("realized_pnl_usdt") or 0) if row else 0.0
    unrealized_pnl = float(unrealized.get("unrealized_pnl_usdt") or 0)
    open_trades = int(row.get("open_trades") or 0) if row else 0
    open_notional = float(row.get("open_notional_usdt") or 0) if row else 0.0
    equity = float(starting_equity_usdt) + realized + unrealized_pnl
    return {
        "starting_equity_usdt": float(starting_equity_usdt),
        "realized_pnl_usdt": realized,
        "unrealized_pnl_usdt": unrealized_pnl,
        "equity_usdt": equity,
        "open_trades": open_trades,
        "open_notional_usdt": open_notional,
        "avg_unrealized_pnl_pct": float(unrealized.get("avg_unrealized_pnl_pct") or 0),
        "all_time": all_time,
    }


def record_equity_snapshot(starting_equity_usdt: float = 1000.0) -> None:
    portfolio = get_paper_portfolio_state(starting_equity_usdt, all_time=True)
    daily = get_daily_paper_stats(all_time=True)
    db.execute(
        """
        INSERT INTO paper_equity_curve(
            session_id, strategy_version, mode,
            equity_usdt, realized_pnl_usdt, open_risk_usdt,
            open_trades, daily_pnl_usdt, max_drawdown_pct, details
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            get_current_session_id(), STRATEGY_VERSION, MODE,
            portfolio["equity_usdt"],
            portfolio["realized_pnl_usdt"],
            portfolio["open_notional_usdt"],
            portfolio["open_trades"],
            daily.get("daily_pnl_usdt") or 0,
            0,
            jsonb({"portfolio": portfolio, "daily": daily, "session": get_current_session(), "scope": "global_open_positions"}),
        ),
    )


def insert_decision_event(symbol: str, decision: dict[str, Any], feature: Any, severity: str = "INFO") -> None:
    details = {
        "action": decision.get("action"),
        "entry_ok": decision.get("entry_ok"),
        "reasons": decision.get("reasons", []),
        "market_phase": decision.get("market_phase") or (getattr(feature, "extra", {}) or {}).get("market_phase"),
        "v4_profile": decision.get("v4_profile") or (getattr(feature, "extra", {}) or {}).get("v4_profile"),
        "pre_pump_score": decision.get("pre_pump_score") or (getattr(feature, "extra", {}) or {}).get("pre_pump_score"),
        "parlayan_score": getattr(feature, "parlayan_score", None),
        "rsi": getattr(feature, "rsi", None),
        "volume_ratio": getattr(feature, "volume_ratio", None),
        "price_change_5m_pct": getattr(feature, "price_change_5m_pct", None),
        "price_change_15m_pct": getattr(feature, "price_change_15m_pct", None),
        "price_change_30m_pct": getattr(feature, "price_change_30m_pct", None),
        "price_change_24h_pct": getattr(feature, "price_change_24h_pct", None),
        "extra": getattr(feature, "extra", {}) or {},
    }
    event_type = "DECISION_ACCEPT" if decision.get("entry_ok") else "DECISION_REJECT"
    insert_signal_event(
        symbol=symbol,
        event_type=event_type,
        severity=severity,
        score=float(details.get("pre_pump_score") or details.get("parlayan_score") or 0),
        price=float(getattr(feature, "price", 0) or 0),
        details=details,
    )


def get_reject_reason_report(hours: int = 24, limit: int = 100) -> dict[str, Any]:
    rows = db.fetch_all(
        """
        SELECT
            reason,
            COUNT(*) AS count,
            AVG((details->>'pre_pump_score')::numeric) AS avg_pre_pump_score,
            AVG((details->>'parlayan_score')::numeric) AS avg_parlayan_score
        FROM (
            SELECT
                jsonb_array_elements_text(COALESCE(details->'reasons', '[]'::jsonb)) AS reason,
                details
            FROM signal_events
            WHERE event_type='DECISION_REJECT'
              AND ts > now() - (%s || ' hours')::interval
        ) x
        GROUP BY reason
        ORDER BY count DESC
        LIMIT %s
        """,
        (hours, limit),
    )
    return {"hours": hours, "rows": rows}


def get_pump_detective_v2_report(threshold_pct: float = 30.0, limit: int = 100) -> dict[str, Any]:
    """
    Pump Detective v2:
    Zirveye değil, ilk kırılma anına bakar.
    Breakout tanımı: coin sonradan threshold_pct üstüne çıkmışsa,
    24h değişimin ilk kez %8'i geçtiği an pump başlangıcı kabul edilir.
    Bu anın 30/60/120 dk öncesindeki ortak deseni çıkarır.
    """
    rows = db.fetch_all(
        """
        WITH pumped AS (
          SELECT symbol, MAX(price_change_24h_pct) AS peak_24h
          FROM market_snapshots
          GROUP BY symbol
          HAVING MAX(price_change_24h_pct) >= %s
        ),
        breakout AS (
          SELECT DISTINCT ON (ms.symbol)
            ms.symbol,
            p.peak_24h,
            ms.ts AS breakout_ts
          FROM market_snapshots ms
          JOIN pumped p ON p.symbol = ms.symbol
          WHERE ms.price_change_24h_pct >= 8
          ORDER BY ms.symbol, ms.ts ASC
        ),
        before AS (
          SELECT
            b.symbol,
            b.peak_24h,
            b.breakout_ts,
            ms.ts,
            ROUND((EXTRACT(EPOCH FROM (b.breakout_ts - ms.ts)) / 60)::numeric, 2) AS minutes_before_breakout,
            ms.price,
            ms.rsi,
            ms.volume_ratio,
            ms.price_change_5m_pct,
            ms.price_change_15m_pct,
            ms.price_change_30m_pct,
            ms.price_change_24h_pct,
            ms.parlayan_score,
            ms.bot_state,
            ms.extra
          FROM breakout b
          JOIN market_snapshots ms ON ms.symbol = b.symbol
          WHERE ms.ts BETWEEN b.breakout_ts - interval '2 hours' AND b.breakout_ts
        )
        SELECT *
        FROM before
        WHERE minutes_before_breakout BETWEEN 25 AND 35
           OR minutes_before_breakout BETWEEN 55 AND 65
           OR minutes_before_breakout BETWEEN 115 AND 125
        ORDER BY peak_24h DESC, symbol, minutes_before_breakout ASC
        LIMIT %s
        """,
        (threshold_pct, limit),
    )

    bucketed: dict[str, list[dict[str, Any]]] = {"30m": [], "60m": [], "120m": []}
    for row in rows:
        m = float(row.get("minutes_before_breakout") or 0)
        if 25 <= m <= 35:
            bucketed["30m"].append(row)
        elif 55 <= m <= 65:
            bucketed["60m"].append(row)
        elif 115 <= m <= 125:
            bucketed["120m"].append(row)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        def avg(key: str) -> float:
            vals = []
            for item in items:
                try:
                    if item.get(key) is not None:
                        vals.append(float(item.get(key)))
                except Exception:
                    pass
            return round(sum(vals) / len(vals), 4) if vals else 0.0
        phases: dict[str, int] = {}
        profiles: dict[str, int] = {}
        for item in items:
            extra = item.get("extra") or {}
            phase = str(extra.get("market_phase") or item.get("bot_state") or "UNKNOWN")
            profile = str(extra.get("v4_profile") or "UNKNOWN")
            phases[phase] = phases.get(phase, 0) + 1
            profiles[profile] = profiles.get(profile, 0) + 1
        return {
            "count": len(items),
            "avg_peak_24h": avg("peak_24h"),
            "avg_rsi": avg("rsi"),
            "avg_volume_ratio": avg("volume_ratio"),
            "avg_5m": avg("price_change_5m_pct"),
            "avg_15m": avg("price_change_15m_pct"),
            "avg_30m": avg("price_change_30m_pct"),
            "avg_24h_at_time": avg("price_change_24h_pct"),
            "avg_parlayan_score": avg("parlayan_score"),
            "phase_counts": phases,
            "profile_counts": profiles,
        }

    return {
        "version": "pump_detective_v2",
        "threshold_pct": threshold_pct,
        "breakout_definition": "first snapshot where price_change_24h_pct >= 8 after a coin later reaches threshold",
        "summary": {bucket: summarize(items) for bucket, items in bucketed.items()},
        "rows": rows,
    }


# ─── V4.3 Velocity / Paper Integrity Reports ─────────────────────────────────

STRATEGY_VERSION = "professional_paper_v45"


def patch_trade_context(trade_id: str, patch: dict[str, Any]) -> None:
    """Trade context içine JSON patch ekler; eski kolon şemasını bozmadan yeni metrik tutar."""
    db.execute(
        """
        UPDATE paper_trades
        SET context = context || %s
        WHERE id=%s
        """,
        (jsonb(patch or {}), trade_id),
    )


def get_velocity_leaderboard(minutes: int = 30, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (symbol)
            symbol, ts, price, rsi, price_change_24h_pct,
            price_change_5m_pct, price_change_15m_pct, price_change_30m_pct,
            volume_ratio, parlayan_score, bot_state, extra
        FROM market_snapshots
        WHERE ts > now() - (%s || ' minutes')::interval
        ORDER BY symbol, ts DESC
        """,
        (minutes,),
    )
    rows.sort(key=lambda r: float((r.get("extra") or {}).get("velocity_score") or 0), reverse=True)
    return rows[:limit]


def get_fast_alerts(hours: int = 24, limit: int = 100) -> list[dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT *
        FROM signal_events
        WHERE event_type='FAST_PUMP_ALERT'
          AND ts > now() - (%s || ' hours')::interval
        ORDER BY score DESC NULLS LAST, ts DESC
        LIMIT %s
        """,
        (hours, limit),
    )


def get_daily_signal_report(day: str | None = None, all_time: bool = False) -> dict[str, Any]:
    """
    Günlük dürüst performans raporu:
    - Sinyal sayısı
    - Net PnL
    - max runup/drawdown
    - en iyi/kötü işlemler
    - reject ve alert dağılımı
    """
    day_filter = "CURRENT_DATE" if not day else "%s::date"
    day_params: tuple[Any, ...] = () if not day else (day,)

    session_filter, session_params = _session_filter_sql(all_time=all_time)

    trade_rows = db.fetch_all(
        f"""
        SELECT
            symbol, strategy_version, entry_ts, exit_ts, status,
            entry_price, exit_price, quote_size, exit_reason,
            pnl_pct, pnl_quote, context,
            COALESCE((context->>'gross_pnl_pct')::numeric, pnl_pct) AS gross_pnl_pct,
            COALESCE((context->>'fee_pct')::numeric, 0) AS fee_pct,
            COALESCE((context->>'total_slippage_pct')::numeric, slippage_pct_estimate) AS total_slippage_pct,
            COALESCE((context->>'max_runup_pct')::numeric, 0) AS max_runup_pct,
            COALESCE((context->>'max_drawdown_pct')::numeric, 0) AS max_drawdown_pct,
            COALESCE((context->>'time_in_trade_min')::numeric, 0) AS time_in_trade_min
        FROM paper_trades
        WHERE entry_ts::date = {day_filter}
          {session_filter}
        ORDER BY entry_ts DESC
        """,
        (*day_params, *session_params),
    )

    summary = db.fetch_one(
        f"""
        SELECT
            COUNT(*) AS trades,
            COUNT(*) FILTER (WHERE status='CLOSED') AS closed_trades,
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct > 0) AS wins,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct <= 0) AS losses,
            COALESCE(SUM(pnl_quote) FILTER (WHERE status='CLOSED'), 0) AS net_pnl_usdt,
            COALESCE(AVG(pnl_pct) FILTER (WHERE status='CLOSED'), 0) AS avg_net_pnl_pct,
            COALESCE(AVG((context->>'gross_pnl_pct')::numeric) FILTER (WHERE status='CLOSED'), 0) AS avg_gross_pnl_pct,
            COALESCE(AVG((context->>'max_runup_pct')::numeric), 0) AS avg_max_runup_pct,
            COALESCE(AVG((context->>'max_drawdown_pct')::numeric), 0) AS avg_max_drawdown_pct
        FROM paper_trades
        WHERE entry_ts::date = {day_filter}
          {session_filter}
        """,
        (*day_params, *session_params),
    )

    signal_counts = db.fetch_all(
        f"""
        SELECT event_type, COUNT(*) AS count, COALESCE(AVG(score), 0) AS avg_score
        FROM signal_events
        WHERE ts::date = {day_filter}
          {session_filter}
        GROUP BY event_type
        ORDER BY count DESC
        """,
        (*day_params, *session_params),
    )

    exit_counts = db.fetch_all(
        f"""
        SELECT exit_reason, COUNT(*) AS count, COALESCE(AVG(pnl_pct), 0) AS avg_pnl_pct, COALESCE(SUM(pnl_quote), 0) AS total_pnl_usdt
        FROM paper_trades
        WHERE status='CLOSED'
          AND exit_ts::date = {day_filter}
          {session_filter}
        GROUP BY exit_reason
        ORDER BY total_pnl_usdt DESC
        """,
        (*day_params, *session_params),
    )

    best = sorted([dict(r) for r in trade_rows if r.get("pnl_pct") is not None], key=lambda r: float(r.get("pnl_pct") or 0), reverse=True)[:10]
    worst = sorted([dict(r) for r in trade_rows if r.get("pnl_pct") is not None], key=lambda r: float(r.get("pnl_pct") or 0))[:10]

    return {
        "version": "daily_signal_report_v42",
        "day": day or "today",
        "all_time": all_time,
        "session": get_current_session(),
        "summary": dict(summary) if summary else {},
        "signal_counts": signal_counts,
        "exit_counts": exit_counts,
        "best_trades": best,
        "worst_trades": worst,
        "recent_trades": trade_rows[:50],
    }


def get_velocity_research_report(hours: int = 24, limit: int = 100) -> dict[str, Any]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT ON (symbol)
            symbol, ts, price, rsi, volume_ratio, parlayan_score,
            price_change_5m_pct, price_change_15m_pct, price_change_30m_pct,
            price_change_24h_pct, bot_state, extra
        FROM market_snapshots
        WHERE ts > now() - (%s || ' hours')::interval
        ORDER BY symbol, ts DESC
        """,
        (hours,),
    )
    rows = sorted(rows, key=lambda r: float((r.get("extra") or {}).get("velocity_score") or 0), reverse=True)[:limit]
    return {
        "version": "velocity_research_v42",
        "hours": hours,
        "rows": rows,
    }


# ─── V4.3 Decision Quality / Market Regime / Near Miss ───────────────────────

def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _pct_change(start: float, end: float | None) -> float | None:
    if start <= 0 or end is None:
        return None
    return ((float(end) - start) / start) * 100.0


def _primary_reason(details: dict[str, Any] | None) -> str:
    details = details or {}
    reason = details.get("reason")
    if reason:
        return str(reason)
    reasons = details.get("reasons")
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])
    action = details.get("action")
    if action:
        return str(action)
    return "UNKNOWN"


def _outcome_label(max_upside_pct: float, max_drawdown_pct: float, latest_return_pct: float) -> str:
    if max_upside_pct >= 10:
        return "MISSED_BIG_WINNER"
    if max_upside_pct >= 5:
        return "MISSED_WINNER"
    if latest_return_pct <= -3 or max_drawdown_pct <= -5:
        return "GOOD_REJECT_OR_RISK_AVOIDED"
    if max_upside_pct < 2:
        return "LOW_OPPORTUNITY"
    return "NEUTRAL"


def compute_and_store_market_regime(features: list[Any]) -> dict[str, Any]:
    """
    V4.3 market regime engine.
    Coin sinyalini genel piyasa şartlarından ayırmak için BTC/ETH ve piyasa genişliğini ölçer.
    """
    if not features:
        return {"regime": "NO_DATA", "confidence": 0.0, "details": {}}

    latest = {getattr(f, "symbol", ""): f for f in features}
    btc = latest.get("BTCUSDT")
    eth = latest.get("ETHUSDT")

    btc_24h = _num(getattr(btc, "price_change_24h_pct", None)) if btc else 0.0
    btc_1h = _num((getattr(btc, "extra", {}) or {}).get("price_change_1h_pct")) if btc else 0.0
    eth_24h = _num(getattr(eth, "price_change_24h_pct", None)) if eth else 0.0

    alt_features = [f for f in features if getattr(f, "symbol", "") not in {"BTCUSDT", "ETHUSDT"}]
    total = max(len(alt_features), 1)
    positive = sum(1 for f in alt_features if _num(getattr(f, "price_change_24h_pct", None)) > 0)
    strong = sum(1 for f in alt_features if _num(getattr(f, "price_change_24h_pct", None)) >= 5)
    danger = sum(1 for f in alt_features if str((getattr(f, "extra", {}) or {}).get("market_phase") or getattr(f, "bot_state", "")) == "DANGER")
    avg_alt = sum(_num(getattr(f, "price_change_24h_pct", None)) for f in alt_features) / total

    breadth_positive_pct = positive / total * 100.0
    breadth_strong_pct = strong / total * 100.0

    if btc_24h <= -2.0 and breadth_positive_pct < 45:
        regime = "RISK_OFF"
        confidence = min(95.0, 55.0 + abs(btc_24h) * 6.0 + (45.0 - breadth_positive_pct) * 0.5)
    elif btc_24h > 1.0 and breadth_strong_pct >= 18 and avg_alt > 1.5:
        regime = "ALT_RISK_ON"
        confidence = min(95.0, 50.0 + breadth_strong_pct + max(avg_alt, 0.0) * 2.0)
    elif abs(btc_24h) <= 1.5 and breadth_strong_pct >= 12:
        regime = "ALT_ROTATION"
        confidence = min(90.0, 48.0 + breadth_strong_pct * 1.2)
    elif danger / total > 0.18:
        regime = "HOT_FOMO_MARKET"
        confidence = min(90.0, 45.0 + (danger / total) * 130.0)
    else:
        regime = "NEUTRAL"
        confidence = 55.0

    previous = get_metadata("market_regime", {})
    details = {
        "total_symbols": len(features),
        "alt_symbols": len(alt_features),
        "btc_24h_pct": round(btc_24h, 4),
        "btc_1h_pct": round(btc_1h, 4),
        "eth_24h_pct": round(eth_24h, 4),
        "breadth_positive_pct": round(breadth_positive_pct, 4),
        "breadth_strong_pct": round(breadth_strong_pct, 4),
        "avg_alt_24h_pct": round(avg_alt, 4),
        "danger_count": danger,
        "previous_regime": previous.get("regime"),
    }

    db.execute(
        """
        INSERT INTO market_regime_snapshots(
            regime, confidence, btc_24h_pct, btc_1h_pct, eth_24h_pct,
            breadth_positive_pct, breadth_strong_pct, avg_alt_24h_pct,
            danger_count, details
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            regime, confidence, btc_24h, btc_1h, eth_24h,
            breadth_positive_pct, breadth_strong_pct, avg_alt,
            danger, jsonb(details),
        ),
    )
    current = {"regime": regime, "confidence": round(confidence, 2), "details": details, "ts": utc_now()}
    set_metadata("market_regime", current)

    if previous.get("regime") and previous.get("regime") != regime:
        insert_signal_event(
            symbol="MARKET",
            event_type="MARKET_REGIME_CHANGE",
            severity="WARNING" if regime in {"RISK_OFF", "HOT_FOMO_MARKET"} else "INFO",
            score=confidence,
            price=None,
            details={"from": previous.get("regime"), "to": regime, **details},
        )
    return current


def get_market_regime_report(hours: int = 24) -> dict[str, Any]:
    rows = db.fetch_all(
        """
        SELECT *
        FROM market_regime_snapshots
        WHERE ts > now() - (%s || ' hours')::interval
        ORDER BY ts DESC
        LIMIT 500
        """,
        (hours,),
    )
    counts: dict[str, int] = {}
    for row in rows:
        regime = str(row.get("regime") or "UNKNOWN")
        counts[regime] = counts.get(regime, 0) + 1
    return {
        "current": get_metadata("market_regime", {}),
        "hours": hours,
        "regime_counts": counts,
        "recent": rows[:50],
    }


def refresh_decision_outcomes(
    hours: int = 36,
    horizons: tuple[int, ...] = (60, 240, 720),
    limit_per_horizon: int = 1500,
) -> dict[str, Any]:
    """
    Reject/alert/entry kararlarının sonradan ne olduğunu ölçer.
    Bu modül filtreleri tahminle değil sonuçla kalibre etmek için kullanılır.
    """
    inserted_total = 0
    per_horizon: dict[str, int] = {}

    for horizon in horizons:
        events = db.fetch_all(
            """
            SELECT e.*
            FROM signal_events e
            WHERE e.ts > now() - (%s || ' hours')::interval
              AND e.ts < now() - (%s || ' minutes')::interval
              AND e.price IS NOT NULL
              AND e.price > 0
              AND e.event_type IN (
                  'DECISION_REJECT',
                  'DECISION_ACCEPT',
                  'PRE_PUMP_ALERT',
                  'FAST_PUMP_ALERT',
                  'TRADE_REJECT',
                  'PAPER_ENTRY'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM decision_outcomes o
                  WHERE o.signal_event_id = e.id
                    AND o.horizon_minutes = %s
              )
            ORDER BY e.ts DESC
            LIMIT %s
            """,
            (hours, horizon, horizon, limit_per_horizon),
        )

        inserted = 0
        for event in events:
            price_at_event = _num(event.get("price"))
            if price_at_event <= 0:
                continue

            stats = db.fetch_one(
                """
                WITH future AS (
                    SELECT ts, price
                    FROM market_snapshots
                    WHERE symbol=%s
                      AND ts >= %s
                      AND ts <= %s + (%s || ' minutes')::interval
                      AND price > 0
                    ORDER BY ts ASC
                ),
                latest AS (
                    SELECT price AS latest_price
                    FROM future
                    ORDER BY ts DESC
                    LIMIT 1
                )
                SELECT
                    MAX(price) AS max_price,
                    MIN(price) AS min_price,
                    (SELECT latest_price FROM latest) AS latest_price,
                    COUNT(*) AS sample_count
                FROM future
                """,
                (event.get("symbol"), event.get("ts"), event.get("ts"), horizon),
            )
            if not stats or int(stats.get("sample_count") or 0) == 0:
                continue

            max_price = _num(stats.get("max_price"), price_at_event)
            min_price = _num(stats.get("min_price"), price_at_event)
            latest_price = _num(stats.get("latest_price"), price_at_event)
            max_upside_pct = _pct_change(price_at_event, max_price) or 0.0
            max_drawdown_pct = _pct_change(price_at_event, min_price) or 0.0
            latest_return_pct = _pct_change(price_at_event, latest_price) or 0.0

            details = dict(event.get("details") or {})
            action = str(details.get("action") or event.get("event_type") or "")
            primary_reason = _primary_reason(details)
            market_phase = str(details.get("market_phase") or (details.get("extra") or {}).get("market_phase") or "")
            v4_profile = str(details.get("v4_profile") or (details.get("extra") or {}).get("v4_profile") or "")
            label = _outcome_label(max_upside_pct, max_drawdown_pct, latest_return_pct)

            db.execute(
                """
                INSERT INTO decision_outcomes(
                    signal_event_id, horizon_minutes, symbol, event_ts, event_type,
                    action, primary_reason, market_phase, v4_profile,
                    price_at_event, latest_price, max_price, min_price,
                    max_upside_pct, max_drawdown_pct, latest_return_pct,
                    outcome_label, details
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(signal_event_id, horizon_minutes) DO NOTHING
                """,
                (
                    event.get("id"), horizon, event.get("symbol"), event.get("ts"), event.get("event_type"),
                    action, primary_reason, market_phase, v4_profile,
                    price_at_event, latest_price, max_price, min_price,
                    round(max_upside_pct, 6), round(max_drawdown_pct, 6), round(latest_return_pct, 6),
                    label, jsonb({"event_details": details, "sample_count": stats.get("sample_count")}),
                ),
            )
            inserted += 1

        per_horizon[str(horizon)] = inserted
        inserted_total += inserted

    return {"inserted_total": inserted_total, "per_horizon": per_horizon, "horizons": list(horizons), "hours": hours}


def get_decision_quality_report(hours: int = 36, horizon_minutes: int = 240, auto_refresh: bool = True) -> dict[str, Any]:
    if auto_refresh:
        refresh_decision_outcomes(hours=hours, horizons=(horizon_minutes,), limit_per_horizon=2000)

    rows = db.fetch_all(
        """
        SELECT
            event_type,
            action,
            primary_reason,
            market_phase,
            COUNT(*) AS decisions,
            COUNT(*) FILTER (WHERE outcome_label IN ('MISSED_BIG_WINNER','MISSED_WINNER')) AS missed_winners,
            COUNT(*) FILTER (WHERE outcome_label='GOOD_REJECT_OR_RISK_AVOIDED') AS good_rejects,
            ROUND(AVG(max_upside_pct), 4) AS avg_max_upside_pct,
            ROUND(AVG(max_drawdown_pct), 4) AS avg_max_drawdown_pct,
            ROUND(AVG(latest_return_pct), 4) AS avg_latest_return_pct,
            ROUND(MAX(max_upside_pct), 4) AS best_after_decision_pct,
            ROUND(MIN(max_drawdown_pct), 4) AS worst_after_decision_pct
        FROM decision_outcomes
        WHERE event_ts > now() - (%s || ' hours')::interval
          AND horizon_minutes=%s
        GROUP BY event_type, action, primary_reason, market_phase
        ORDER BY missed_winners DESC, decisions DESC
        LIMIT 120
        """,
        (hours, horizon_minutes),
    )

    headline = db.fetch_one(
        """
        SELECT
            COUNT(*) AS evaluated,
            COUNT(*) FILTER (WHERE event_type='DECISION_REJECT') AS rejects,
            COUNT(*) FILTER (WHERE event_type='DECISION_REJECT' AND outcome_label IN ('MISSED_BIG_WINNER','MISSED_WINNER')) AS missed_rejects,
            COUNT(*) FILTER (WHERE event_type='DECISION_REJECT' AND outcome_label='GOOD_REJECT_OR_RISK_AVOIDED') AS good_rejects,
            ROUND(AVG(max_upside_pct), 4) AS avg_max_upside_pct,
            ROUND(AVG(max_drawdown_pct), 4) AS avg_max_drawdown_pct
        FROM decision_outcomes
        WHERE event_ts > now() - (%s || ' hours')::interval
          AND horizon_minutes=%s
        """,
        (hours, horizon_minutes),
    )

    return {
        "version": "decision_quality_v43",
        "hours": hours,
        "horizon_minutes": horizon_minutes,
        "headline": dict(headline) if headline else {},
        "by_reason": rows,
    }


def get_danger_filter_quality(hours: int = 36, horizon_minutes: int = 240) -> dict[str, Any]:
    if True:
        refresh_decision_outcomes(hours=hours, horizons=(horizon_minutes,), limit_per_horizon=2000)

    rows = db.fetch_all(
        """
        SELECT
            primary_reason,
            market_phase,
            COUNT(*) AS decisions,
            COUNT(*) FILTER (WHERE outcome_label IN ('MISSED_BIG_WINNER','MISSED_WINNER')) AS missed_winners,
            COUNT(*) FILTER (WHERE outcome_label='GOOD_REJECT_OR_RISK_AVOIDED') AS good_rejects,
            ROUND(AVG(max_upside_pct), 4) AS avg_max_upside_pct,
            ROUND(MAX(max_upside_pct), 4) AS max_upside_pct,
            ROUND(AVG(max_drawdown_pct), 4) AS avg_max_drawdown_pct
        FROM decision_outcomes
        WHERE event_ts > now() - (%s || ' hours')::interval
          AND horizon_minutes=%s
          AND event_type='DECISION_REJECT'
          AND (
              primary_reason ILIKE '%%DANGER%%'
              OR market_phase='DANGER'
          )
        GROUP BY primary_reason, market_phase
        ORDER BY missed_winners DESC, decisions DESC
        """,
        (hours, horizon_minutes),
    )

    total = sum(int(r.get("decisions") or 0) for r in rows)
    missed = sum(int(r.get("missed_winners") or 0) for r in rows)
    good = sum(int(r.get("good_rejects") or 0) for r in rows)
    return {
        "version": "danger_filter_quality_v43",
        "hours": hours,
        "horizon_minutes": horizon_minutes,
        "total_danger_rejects_evaluated": total,
        "missed_winner_count": missed,
        "good_reject_count": good,
        "missed_winner_rate_pct": round((missed / total * 100.0), 3) if total else 0.0,
        "good_reject_rate_pct": round((good / total * 100.0), 3) if total else 0.0,
        "rows": rows,
    }


def get_near_miss_report(hours: int = 36, horizon_minutes: int = 240, min_upside_pct: float = 5.0, limit: int = 100) -> dict[str, Any]:
    refresh_decision_outcomes(hours=hours, horizons=(horizon_minutes,), limit_per_horizon=2500)
    rows = db.fetch_all(
        """
        SELECT
            o.*,
            e.details AS event_details
        FROM decision_outcomes o
        JOIN signal_events e ON e.id=o.signal_event_id
        WHERE o.event_ts > now() - (%s || ' hours')::interval
          AND o.horizon_minutes=%s
          AND o.event_type IN ('DECISION_REJECT','TRADE_REJECT')
          AND o.max_upside_pct >= %s
        ORDER BY o.max_upside_pct DESC, o.event_ts DESC
        LIMIT %s
        """,
        (hours, horizon_minutes, min_upside_pct, limit),
    )
    return {
        "version": "near_miss_v43",
        "hours": hours,
        "horizon_minutes": horizon_minutes,
        "min_upside_pct": min_upside_pct,
        "count": len(rows),
        "rows": rows,
    }


def get_pre_pump_alert_quality(hours: int = 36, horizon_minutes: int = 240, limit: int = 100) -> dict[str, Any]:
    refresh_decision_outcomes(hours=hours, horizons=(horizon_minutes,), limit_per_horizon=2500)
    summary = db.fetch_all(
        """
        SELECT
            event_type,
            COUNT(*) AS alerts,
            COUNT(*) FILTER (WHERE max_upside_pct >= 3) AS hit_3pct,
            COUNT(*) FILTER (WHERE max_upside_pct >= 5) AS hit_5pct,
            COUNT(*) FILTER (WHERE max_upside_pct >= 10) AS hit_10pct,
            ROUND(AVG(max_upside_pct), 4) AS avg_max_upside_pct,
            ROUND(AVG(max_drawdown_pct), 4) AS avg_max_drawdown_pct,
            ROUND(MAX(max_upside_pct), 4) AS best_after_alert_pct
        FROM decision_outcomes
        WHERE event_ts > now() - (%s || ' hours')::interval
          AND horizon_minutes=%s
          AND event_type IN ('PRE_PUMP_ALERT','FAST_PUMP_ALERT')
        GROUP BY event_type
        ORDER BY alerts DESC
        """,
        (hours, horizon_minutes),
    )
    examples = db.fetch_all(
        """
        SELECT o.*, e.details AS event_details
        FROM decision_outcomes o
        JOIN signal_events e ON e.id=o.signal_event_id
        WHERE o.event_ts > now() - (%s || ' hours')::interval
          AND o.horizon_minutes=%s
          AND o.event_type IN ('PRE_PUMP_ALERT','FAST_PUMP_ALERT')
        ORDER BY o.max_upside_pct DESC
        LIMIT %s
        """,
        (hours, horizon_minutes, limit),
    )
    return {
        "version": "pre_pump_alert_quality_v43",
        "hours": hours,
        "horizon_minutes": horizon_minutes,
        "summary": summary,
        "best_alerts": examples,
    }


def get_v44_trade_quality_report(hours: int = 24, all_time: bool = True) -> dict[str, Any]:
    """
    V4.4: zarar azaltma + kâr maksimize modüllerinin etkisini ölçer.
    Özellikle FAILED_BREAKOUT / STALE_MOMENTUM_EXIT / ADAPTIVE_TAKE_PROFIT çıktılarını izler.
    """
    session_filter, params = _session_filter_sql(all_time=all_time)
    rows = db.fetch_all(
        f"""
        SELECT
            symbol, strategy_version, status, entry_ts, exit_ts, exit_reason,
            pnl_pct, pnl_quote, context, protection_state,
            COALESCE((context->>'max_runup_pct')::numeric, 0) AS max_runup_pct,
            COALESCE((context->>'max_drawdown_pct')::numeric, 0) AS max_drawdown_pct,
            COALESCE((context->>'time_in_trade_min')::numeric, 0) AS time_in_trade_min,
            COALESCE((protection_state->>'follow_through_score')::numeric, NULL) AS follow_through_score,
            protection_state->>'follow_through_reason' AS follow_through_reason,
            protection_state->>'adaptive_profit_reason' AS adaptive_profit_reason
        FROM paper_trades
        WHERE entry_ts > now() - (%s || ' hours')::interval {session_filter}
        ORDER BY entry_ts DESC
        """,
        (hours, *params),
    )

    closed = [r for r in rows if r.get("status") == "CLOSED"]
    losses = [r for r in closed if _num(r.get("pnl_pct")) < 0]
    wins = [r for r in closed if _num(r.get("pnl_pct")) > 0]
    early_exits = [r for r in closed if r.get("exit_reason") in {"FAILED_BREAKOUT", "STALE_MOMENTUM_EXIT"}]
    adaptive_wins = [r for r in closed if r.get("exit_reason") == "ADAPTIVE_TAKE_PROFIT"]

    by_reason: dict[str, dict[str, Any]] = {}
    for row in closed:
        reason = row.get("exit_reason") or "UNKNOWN"
        bucket = by_reason.setdefault(reason, {"count": 0, "sum_pnl": 0.0, "wins": 0})
        pnl = _num(row.get("pnl_pct"))
        bucket["count"] += 1
        bucket["sum_pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1

    for bucket in by_reason.values():
        count = max(bucket["count"], 1)
        bucket["avg_pnl_pct"] = round(bucket["sum_pnl"] / count, 4)
        bucket["win_rate_pct"] = round(bucket["wins"] / count * 100.0, 2)
        bucket["sum_pnl"] = round(bucket["sum_pnl"], 4)

    return {
        "hours": hours,
        "total_trades": len(rows),
        "closed_trades": len(closed),
        "open_trades": len([r for r in rows if r.get("status") == "OPEN"]),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / max(len(closed), 1)) * 100.0, 2),
        "total_pnl_pct_sum": round(sum(_num(r.get("pnl_pct")) for r in closed), 4),
        "early_failure_exits": len(early_exits),
        "adaptive_take_profit_exits": len(adaptive_wins),
        "by_exit_reason": by_reason,
        "worst_trades": sorted(losses, key=lambda r: _num(r.get("pnl_pct")))[:10],
        "best_trades": sorted(wins, key=lambda r: _num(r.get("pnl_pct")), reverse=True)[:10],
    }


# ─── V4.5 Pattern Memory / Market DNA ────────────────────────────────────────

def _feature_metrics(feature: Any) -> dict[str, Any]:
    extra = getattr(feature, "extra", {}) or {}
    return {
        "price": _num(getattr(feature, "price", None)),
        "rsi": _num(getattr(feature, "rsi", None)),
        "price_change_5m_pct": _num(getattr(feature, "price_change_5m_pct", None)),
        "price_change_15m_pct": _num(getattr(feature, "price_change_15m_pct", None)),
        "price_change_30m_pct": _num(getattr(feature, "price_change_30m_pct", None)),
        "price_change_1h_pct": _num(extra.get("price_change_1h_pct")),
        "price_change_4h_pct": _num(extra.get("price_change_4h_pct")),
        "price_change_24h_pct": _num(getattr(feature, "price_change_24h_pct", None)),
        "quote_volume_24h": _num(getattr(feature, "quote_volume_24h", None)),
        "trade_count_24h": _num(getattr(feature, "trade_count_24h", None)),
        "spread_pct": _num(getattr(feature, "spread_pct", None)),
        "volume_ratio": _num(getattr(feature, "volume_ratio", None)),
        "momentum_score": _num(getattr(feature, "momentum_score", None)),
        "liquidity_score": _num(getattr(feature, "liquidity_score", None)),
        "fake_pump_risk": _num(getattr(feature, "fake_pump_risk", None)),
        "parlayan_score": _num(getattr(feature, "parlayan_score", None)),
        "pre_pump_score": _num(extra.get("pre_pump_score")),
        "velocity_score": _num(extra.get("velocity_score")),
        "fast_alarm_score": _num(extra.get("fast_alarm_score")),
        "price_velocity_1m_pct": _num(extra.get("price_velocity_1m_pct")),
        "price_velocity_5m_pct": _num(extra.get("price_velocity_5m_pct")),
        "momentum_acceleration": _num(extra.get("momentum_acceleration")),
        "volume_velocity": _num(extra.get("volume_velocity")),
        "trade_count_velocity": _num(extra.get("trade_count_velocity")),
        "directional_volume_score": _num(extra.get("directional_volume_score")),
        "up_volume_ratio": _num(extra.get("up_volume_ratio")),
        "down_volume_ratio": _num(extra.get("down_volume_ratio")),
        "directional_volume_delta": _num(extra.get("directional_volume_delta")),
        "close_location_score": _num(extra.get("close_location_score")),
        "recent_green_bar_ratio": _num(extra.get("recent_green_bar_ratio")),
        "market_phase": extra.get("market_phase"),
        "v4_profile": extra.get("v4_profile"),
    }


def _snapshot_metrics(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    extra = row.get("extra") or {}
    return {
        "snapshot_id": str(row.get("id")) if row.get("id") is not None else None,
        "ts": row.get("ts").isoformat() if isinstance(row.get("ts"), datetime) else row.get("ts"),
        "price": _num(row.get("price")),
        "rsi": _num(row.get("rsi")),
        "price_change_5m_pct": _num(row.get("price_change_5m_pct")),
        "price_change_15m_pct": _num(row.get("price_change_15m_pct")),
        "price_change_30m_pct": _num(row.get("price_change_30m_pct")),
        "price_change_1h_pct": _num(extra.get("price_change_1h_pct")),
        "price_change_4h_pct": _num(extra.get("price_change_4h_pct")),
        "price_change_24h_pct": _num(row.get("price_change_24h_pct")),
        "quote_volume_24h": _num(row.get("quote_volume_24h")),
        "trade_count_24h": _num(row.get("trade_count_24h")),
        "spread_pct": _num(row.get("spread_pct")),
        "volume_ratio": _num(row.get("volume_ratio")),
        "momentum_score": _num(row.get("momentum_score")),
        "liquidity_score": _num(row.get("liquidity_score")),
        "fake_pump_risk": _num(row.get("fake_pump_risk")),
        "parlayan_score": _num(row.get("parlayan_score")),
        "pre_pump_score": _num(extra.get("pre_pump_score")),
        "velocity_score": _num(extra.get("velocity_score")),
        "fast_alarm_score": _num(extra.get("fast_alarm_score")),
        "price_velocity_1m_pct": _num(extra.get("price_velocity_1m_pct")),
        "price_velocity_5m_pct": _num(extra.get("price_velocity_5m_pct")),
        "momentum_acceleration": _num(extra.get("momentum_acceleration")),
        "volume_velocity": _num(extra.get("volume_velocity")),
        "trade_count_velocity": _num(extra.get("trade_count_velocity")),
        "directional_volume_score": _num(extra.get("directional_volume_score")),
        "up_volume_ratio": _num(extra.get("up_volume_ratio")),
        "down_volume_ratio": _num(extra.get("down_volume_ratio")),
        "directional_volume_delta": _num(extra.get("directional_volume_delta")),
        "close_location_score": _num(extra.get("close_location_score")),
        "recent_green_bar_ratio": _num(extra.get("recent_green_bar_ratio")),
        "market_phase": extra.get("market_phase") or row.get("bot_state"),
        "v4_profile": extra.get("v4_profile"),
    }


def _label_lifecycle_result(result_pct: float, threshold_pct: float, max_drawdown_pct: float | None = None) -> str:
    if result_pct >= threshold_pct * 2:
        return "EXPLOSIVE_WINNER"
    if result_pct >= threshold_pct:
        return "THRESHOLD_WINNER"
    if result_pct >= threshold_pct * 0.5:
        return "PARTIAL_FOLLOW_THROUGH"
    if max_drawdown_pct is not None and max_drawdown_pct <= -5:
        return "FAILED_AND_DRAWDOWN"
    return "NO_FOLLOW_THROUGH"


def record_pattern_memory_samples(
    features: list[Any],
    thresholds_pct: tuple[float, ...] = (10, 20, 30, 50, 100),
    horizons_minutes: tuple[int, ...] = (15, 30, 60, 240),
    dedupe_cooldown_minutes: int = 180,
) -> dict[str, Any]:
    """
    Her scan'de güçlü hareketleri learning dataset'e yazar.
    Örnek: son 60 dakikada +30 yapan coin için 60 dakika önceki metrikleri,
    trigger anındaki metrikleri ve hareket sonucunu coin_lifecycle_events tablosuna ekler.
    """
    current = get_current_session()
    session_id = current.get("session_id")
    strategy_version = current.get("strategy_version") or STRATEGY_VERSION
    mode = current.get("mode") or MODE
    regime = (get_metadata("market_regime", {}) or {}).get("regime")
    inserted_events: list[dict[str, Any]] = []

    for feature in features:
        symbol = getattr(feature, "symbol", None)
        trigger_price = _num(getattr(feature, "price", None))
        trigger_ts = getattr(feature, "ts", None) or utc_now()
        if not symbol or trigger_price <= 0:
            continue

        trigger_metrics = _feature_metrics(feature)
        for horizon in horizons_minutes:
            before = db.fetch_one(
                """
                SELECT *
                FROM market_snapshots
                WHERE symbol=%s
                  AND ts <= %s - (%s || ' minutes')::interval
                  AND price > 0
                ORDER BY ts DESC
                LIMIT 1
                """,
                (symbol, trigger_ts, horizon),
            )
            if not before:
                continue
            start_price = _num(before.get("price"))
            if start_price <= 0:
                continue
            result_pct = ((trigger_price - start_price) / start_price) * 100.0

            future_window = db.fetch_one(
                """
                SELECT
                    MAX(price) AS max_price,
                    MIN(price) AS min_price
                FROM market_snapshots
                WHERE symbol=%s
                  AND ts >= %s
                  AND ts <= %s + (%s || ' minutes')::interval
                  AND price > 0
                """,
                (symbol, before.get("ts"), trigger_ts, horizon),
            ) or {}
            max_price = _num(future_window.get("max_price"), trigger_price)
            min_price = _num(future_window.get("min_price"), start_price)
            max_upside_pct = ((max_price - start_price) / start_price) * 100.0 if start_price > 0 else result_pct
            max_drawdown_pct = ((min_price - start_price) / start_price) * 100.0 if start_price > 0 else 0.0

            for threshold in thresholds_pct:
                if result_pct < threshold:
                    continue

                duplicate = db.fetch_one(
                    """
                    SELECT id
                    FROM coin_lifecycle_events
                    WHERE symbol=%s
                      AND threshold_pct=%s
                      AND horizon_minutes=%s
                      AND detected_at > now() - (%s || ' minutes')::interval
                    LIMIT 1
                    """,
                    (symbol, threshold, horizon, dedupe_cooldown_minutes),
                )
                if duplicate:
                    continue

                before_metrics = _snapshot_metrics(before)
                outcome_label = _label_lifecycle_result(result_pct, threshold, max_drawdown_pct)
                event = db.fetch_one(
                    """
                    INSERT INTO coin_lifecycle_events(
                        session_id, strategy_version, mode, symbol,
                        event_type, threshold_pct, horizon_minutes,
                        start_ts, start_price, trigger_ts, trigger_price,
                        result_pct, max_upside_pct, max_drawdown_pct,
                        market_regime, outcome_label,
                        before_metrics, trigger_metrics, after_metrics, details
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id, symbol, threshold_pct, horizon_minutes, result_pct, outcome_label
                    """,
                    (
                        session_id, strategy_version, mode, symbol,
                        "MOVE_THRESHOLD_CROSSED", threshold, horizon,
                        before.get("ts"), start_price, trigger_ts, trigger_price,
                        result_pct, max_upside_pct, max_drawdown_pct,
                        regime, outcome_label,
                        jsonb(before_metrics), jsonb(trigger_metrics), jsonb({}),
                        jsonb({
                            "dedupe_cooldown_minutes": dedupe_cooldown_minutes,
                            "source": "pattern_memory_engine_v45",
                            "start_snapshot_id": str(before.get("id")),
                        }),
                    ),
                )
                if not event:
                    continue
                event_id = event["id"]
                db.executemany(
                    """
                    INSERT INTO pattern_memory_samples(
                        lifecycle_event_id, symbol, sample_ts, stage, offset_minutes, price, metrics
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    [
                        (event_id, symbol, before.get("ts"), "BEFORE", -horizon, start_price, jsonb(before_metrics)),
                        (event_id, symbol, trigger_ts, "TRIGGER", 0, trigger_price, jsonb(trigger_metrics)),
                    ],
                )
                inserted_events.append(dict(event))

    if inserted_events:
        log_event("INFO", "pattern_memory", "Pattern Memory olayları kaydedildi", {"inserted": len(inserted_events), "events": inserted_events[:20]})

    return {
        "enabled": True,
        "inserted": len(inserted_events),
        "events": inserted_events[:50],
        "thresholds_pct": list(thresholds_pct),
        "horizons_minutes": list(horizons_minutes),
    }


def get_pattern_memory_report(hours: int = 72, threshold_pct: float | None = None, limit: int = 100) -> dict[str, Any]:
    params: list[Any] = [hours]
    threshold_filter = ""
    if threshold_pct is not None:
        threshold_filter = "AND threshold_pct >= %s"
        params.append(threshold_pct)
    params.append(limit)
    events = db.fetch_all(
        f"""
        SELECT *
        FROM coin_lifecycle_events
        WHERE detected_at > now() - (%s || ' hours')::interval
        {threshold_filter}
        ORDER BY detected_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    summary = db.fetch_all(
        f"""
        SELECT
            threshold_pct,
            horizon_minutes,
            market_regime,
            COUNT(*) AS samples,
            ROUND(AVG(result_pct), 4) AS avg_result_pct,
            ROUND(AVG(max_upside_pct), 4) AS avg_max_upside_pct,
            ROUND(AVG(max_drawdown_pct), 4) AS avg_max_drawdown_pct,
            COUNT(*) FILTER (WHERE outcome_label IN ('EXPLOSIVE_WINNER','THRESHOLD_WINNER')) AS winners
        FROM coin_lifecycle_events
        WHERE detected_at > now() - (%s || ' hours')::interval
        {threshold_filter}
        GROUP BY threshold_pct, horizon_minutes, market_regime
        ORDER BY threshold_pct DESC, samples DESC
        LIMIT 100
        """,
        tuple(params[:-1]),
    )
    return {
        "version": "pattern_memory_v45",
        "hours": hours,
        "threshold_pct": threshold_pct,
        "summary": summary,
        "events": events,
    }


def _avg_metric(rows: list[dict[str, Any]], key: str) -> float:
    vals = []
    for row in rows:
        metrics = row.get("before_metrics") or {}
        value = metrics.get(key)
        try:
            if value is not None:
                vals.append(float(value))
        except Exception:
            continue
    return round(sum(vals) / len(vals), 6) if vals else 0.0


def refresh_market_dna_profiles(lookback_days: int = 30, min_samples: int = 8) -> dict[str, Any]:
    """
    Lifecycle olaylarından rule kalibrasyonu için istatistik profilleri üretir.
    Bu fonksiyon stratejiyi otomatik değiştirmez; dashboard/research için evidence layer üretir.
    """
    groups = db.fetch_all(
        """
        SELECT threshold_pct, horizon_minutes, COALESCE(market_regime, 'UNKNOWN') AS market_regime
        FROM coin_lifecycle_events
        WHERE detected_at > now() - (%s || ' days')::interval
        GROUP BY threshold_pct, horizon_minutes, COALESCE(market_regime, 'UNKNOWN')
        HAVING COUNT(*) >= %s
        ORDER BY threshold_pct DESC, horizon_minutes ASC
        """,
        (lookback_days, min_samples),
    )
    updated = 0
    metric_keys = [
        "volume_ratio",
        "pre_pump_score",
        "velocity_score",
        "fast_alarm_score",
        "momentum_acceleration",
        "directional_volume_score",
        "up_volume_ratio",
        "spread_pct",
        "liquidity_score",
        "parlayan_score",
        "price_change_5m_pct",
        "price_change_15m_pct",
        "price_change_24h_pct",
    ]

    for group in groups:
        threshold = _num(group.get("threshold_pct"))
        horizon = int(_num(group.get("horizon_minutes")))
        regime = str(group.get("market_regime") or "UNKNOWN")
        rows = db.fetch_all(
            """
            SELECT *
            FROM coin_lifecycle_events
            WHERE detected_at > now() - (%s || ' days')::interval
              AND threshold_pct=%s
              AND horizon_minutes=%s
              AND COALESCE(market_regime, 'UNKNOWN')=%s
            """,
            (lookback_days, threshold, horizon, regime),
        )
        if len(rows) < min_samples:
            continue

        sample_count = len(rows)
        winners = [r for r in rows if r.get("outcome_label") in ("EXPLOSIVE_WINNER", "THRESHOLD_WINNER")]
        win_rate = len(winners) / sample_count * 100.0 if sample_count else 0.0
        avg_result = sum(_num(r.get("result_pct")) for r in rows) / sample_count
        avg_upside = sum(_num(r.get("max_upside_pct")) for r in rows) / sample_count
        avg_drawdown = sum(_num(r.get("max_drawdown_pct")) for r in rows) / sample_count

        feature_stats = {key: _avg_metric(rows, key) for key in metric_keys}
        recommendations = {
            "candidate_rule_bias": "tighten" if win_rate < 45 else "normal" if win_rate < 65 else "can_relax_slightly",
            "min_volume_ratio_hint": round(feature_stats.get("volume_ratio", 0) * 0.85, 4),
            "min_velocity_score_hint": round(feature_stats.get("velocity_score", 0) * 0.80, 4),
            "min_directional_volume_score_hint": round(feature_stats.get("directional_volume_score", 0) * 0.85, 4),
            "note": "Hints are descriptive research outputs, not automatic trading changes.",
        }
        profile_key = f"{regime}:{threshold:g}:{horizon}"
        db.execute(
            """
            INSERT INTO market_dna_profiles(
                profile_key, profile_type, horizon_minutes, threshold_pct,
                sample_count, win_rate, avg_result_pct, avg_max_upside_pct, avg_max_drawdown_pct,
                feature_stats, recommendations, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (profile_key) DO UPDATE SET
                sample_count=EXCLUDED.sample_count,
                win_rate=EXCLUDED.win_rate,
                avg_result_pct=EXCLUDED.avg_result_pct,
                avg_max_upside_pct=EXCLUDED.avg_max_upside_pct,
                avg_max_drawdown_pct=EXCLUDED.avg_max_drawdown_pct,
                feature_stats=EXCLUDED.feature_stats,
                recommendations=EXCLUDED.recommendations,
                updated_at=now()
            """,
            (
                profile_key, regime, horizon, threshold,
                sample_count, win_rate, avg_result, avg_upside, avg_drawdown,
                jsonb(feature_stats), jsonb(recommendations),
            ),
        )
        updated += 1

    if updated:
        log_event("INFO", "market_dna", "Market DNA profilleri yenilendi", {"profiles_updated": updated, "lookback_days": lookback_days})
    return {"enabled": True, "profiles_updated": updated, "lookback_days": lookback_days, "min_samples": min_samples}


def get_market_dna_report(limit: int = 100, refresh: bool = False) -> dict[str, Any]:
    refresh_result = {}
    if refresh:
        refresh_result = refresh_market_dna_profiles()
    profiles = db.fetch_all(
        """
        SELECT *
        FROM market_dna_profiles
        ORDER BY threshold_pct DESC, sample_count DESC, updated_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    return {
        "version": "market_dna_v45",
        "refresh": refresh_result,
        "profiles": profiles,
    }
