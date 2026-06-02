-- Parlayan Bot — Veritabanı Şeması
-- Hedef: Professional paper trading v4.2: velocity alarms, self-honest risk, smart trailing, daily reports

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─── Temel Tablolar ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL,            -- INFO | WARNING | ERROR
    category TEXT NOT NULL,         -- scanner | trade | config | app
    message TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_bot_events_ts ON bot_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_bot_events_category ON bot_events (category, ts DESC);

CREATE TABLE IF NOT EXISTS symbol_universe (
    symbol TEXT PRIMARY KEY,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    status TEXT NOT NULL,
    is_spot_trading_allowed BOOLEAN NOT NULL DEFAULT false,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Piyasa Verileri ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    price NUMERIC NOT NULL,
    rsi NUMERIC,
    price_change_24h_pct NUMERIC,
    price_change_15m_pct NUMERIC,
    price_change_5m_pct NUMERIC,
    price_change_30m_pct NUMERIC,
    quote_volume_24h NUMERIC,
    trade_count_24h BIGINT,
    spread_pct NUMERIC,
    volume_ratio NUMERIC,
    momentum_score NUMERIC,
    liquidity_score NUMERIC,
    fake_pump_risk NUMERIC,
    parlayan_score NUMERIC,         -- Ana parlayan skoru (0-100)
    wick_body_ratio NUMERIC,
    bot_state TEXT NOT NULL DEFAULT 'WATCH',
    extra JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON market_snapshots (ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts ON market_snapshots (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_parlayan ON market_snapshots (parlayan_score DESC, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_24h ON market_snapshots (price_change_24h_pct DESC, ts DESC);

-- 30 günden eski snapshot'ları otomatik sil
CREATE OR REPLACE FUNCTION cleanup_old_snapshots() RETURNS void AS $$
BEGIN
    DELETE FROM market_snapshots WHERE ts < now() - interval '30 days';
END;
$$ LANGUAGE plpgsql;

-- ─── Parlayan Adaylar ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS parlayan_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    price_at_detection NUMERIC NOT NULL,
    price_change_24h_pct NUMERIC NOT NULL,
    parlayan_score NUMERIC NOT NULL,
    volume_ratio NUMERIC,
    rsi NUMERIC,
    status TEXT NOT NULL DEFAULT 'WATCHING',    -- WATCHING | ENTERED | CLOSED | EXPIRED
    entry_price NUMERIC,
    peak_gain_pct NUMERIC NOT NULL DEFAULT 0,
    closed_at TIMESTAMPTZ,
    context JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON parlayan_candidates (status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_symbol ON parlayan_candidates (symbol, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON parlayan_candidates (parlayan_score DESC);

-- ─── İşlemler ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS paper_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID REFERENCES parlayan_candidates(id) ON DELETE SET NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',        -- OPEN | CLOSED
    entry_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    entry_price NUMERIC NOT NULL,
    last_price NUMERIC,
    max_price NUMERIC NOT NULL,
    exit_ts TIMESTAMPTZ,
    exit_price NUMERIC,
    exit_reason TEXT,                           -- TAKE_PROFIT | TRAILING_STOP | STOP_LOSS | PROFIT_PROTECTION | MAX_TIME_EXIT
    quote_size NUMERIC NOT NULL,
    fee_rate_estimate NUMERIC NOT NULL DEFAULT 0.001,
    slippage_pct_estimate NUMERIC NOT NULL DEFAULT 0.05,
    stop_loss_pct NUMERIC NOT NULL DEFAULT 2.5,
    trailing_start_pct NUMERIC NOT NULL DEFAULT 7.0,
    trailing_gap_pct NUMERIC NOT NULL DEFAULT 3.5,
    take_profit_pct NUMERIC NOT NULL DEFAULT 15.0,
    pnl_pct NUMERIC,
    pnl_quote NUMERIC,
    protected_stop_price NUMERIC,
    protection_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_update_ts TIMESTAMPTZ,
    context JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON paper_trades (status, entry_ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON paper_trades (symbol, entry_ts DESC);

-- ─── Cooldown ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cooldowns (
    symbol TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    until_ts TIMESTAMPTZ NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cooldowns_until ON cooldowns (until_ts);


-- ─── Profesyonel Paper Trading Araştırma Katmanı ─────────────────────────────
-- Bu bölüm canlı emir vermez. Coinlerin yükseliş öncesi/zamanındaki davranışını
-- dakika dakika analiz etmek için kullanılır.

CREATE TABLE IF NOT EXISTS signal_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,       -- PRE_PUMP_ALERT | ENTRY_BLOCKED | PAPER_ENTRY | EXIT | RISK_BLOCK | PHASE_CHANGE
    severity TEXT NOT NULL DEFAULT 'INFO',
    score NUMERIC,
    price NUMERIC,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_ts ON signal_events (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_type_ts ON signal_events (event_type, ts DESC);

CREATE TABLE IF NOT EXISTS paper_equity_curve (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity_usdt NUMERIC NOT NULL,
    realized_pnl_usdt NUMERIC NOT NULL DEFAULT 0,
    open_risk_usdt NUMERIC NOT NULL DEFAULT 0,
    open_trades INT NOT NULL DEFAULT 0,
    daily_pnl_usdt NUMERIC NOT NULL DEFAULT 0,
    max_drawdown_pct NUMERIC NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_equity_curve_ts ON paper_equity_curve (ts DESC);

CREATE TABLE IF NOT EXISTS symbol_research_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    note_type TEXT NOT NULL,        -- AUTO_PATTERN | MANUAL | REVIEW
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_research_notes_symbol_ts ON symbol_research_notes (symbol, ts DESC);

CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_score_ts ON market_snapshots (symbol, parlayan_score DESC, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_extra_gin ON market_snapshots USING GIN (extra);

CREATE OR REPLACE VIEW latest_symbol_snapshot AS
SELECT DISTINCT ON (symbol)
    *
FROM market_snapshots
ORDER BY symbol, ts DESC;


-- ─── Paper Trade Sessions ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_sessions_started ON paper_sessions (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_sessions_strategy ON paper_sessions (strategy_version, started_at DESC);

ALTER TABLE parlayan_candidates
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

ALTER TABLE signal_events
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

ALTER TABLE paper_equity_curve
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

CREATE INDEX IF NOT EXISTS idx_candidates_session_status ON parlayan_candidates (session_id, status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_session_status ON paper_trades (session_id, status, entry_ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_session_ts ON signal_events (session_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_equity_session_ts ON paper_equity_curve (session_id, ts DESC);



-- ─── V4.1 Idempotent Migrations ─────────────────────────────────────────────
-- Eski DB ile güvenli çalışması için kolonlar yoksa eklenir.
ALTER TABLE parlayan_candidates ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL;
ALTER TABLE parlayan_candidates ADD COLUMN IF NOT EXISTS strategy_version TEXT;
ALTER TABLE parlayan_candidates ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy_version TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'paper';

CREATE INDEX IF NOT EXISTS idx_candidates_session_status ON parlayan_candidates (session_id, status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_session_status ON paper_trades (session_id, status, entry_ts DESC);

CREATE TABLE IF NOT EXISTS paper_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_name TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING',
    config_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_sessions_started ON paper_sessions (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_sessions_strategy ON paper_sessions (strategy_version, started_at DESC);

CREATE TABLE IF NOT EXISTS signal_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    strategy_version TEXT,
    mode TEXT NOT NULL DEFAULT 'paper',
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'INFO',
    score NUMERIC,
    price NUMERIC,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_signal_events_ts ON signal_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_symbol_ts ON signal_events (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signal_events_type_ts ON signal_events (event_type, ts DESC);

CREATE TABLE IF NOT EXISTS paper_equity_curve (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES paper_sessions(id) ON DELETE SET NULL,
    strategy_version TEXT,
    mode TEXT NOT NULL DEFAULT 'paper',
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    equity_usdt NUMERIC NOT NULL,
    realized_pnl_usdt NUMERIC NOT NULL DEFAULT 0,
    open_risk_usdt NUMERIC NOT NULL DEFAULT 0,
    open_trades INTEGER NOT NULL DEFAULT 0,
    daily_pnl_usdt NUMERIC NOT NULL DEFAULT 0,
    max_drawdown_pct NUMERIC NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_equity_curve_ts ON paper_equity_curve (ts DESC);
CREATE INDEX IF NOT EXISTS idx_equity_curve_session_ts ON paper_equity_curve (session_id, ts DESC);
