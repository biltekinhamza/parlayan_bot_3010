from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import db, jsonb

STRATEGY_VERSION = "professional_paper_v4"
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

STRATEGY_VERSION = "professional_paper_v41"


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


# ─── V4.2 Velocity / Paper Integrity Reports ─────────────────────────────────

STRATEGY_VERSION = "professional_paper_v42"


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
