#!/usr/bin/env python3
"""
Parlayan Bot — Rapor Dışa Aktarma
Kullanım: python export_report.py
Çıktı: parlayan_rapor_YYYYMMDD_HHMMSS.zip
"""

import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime


def run(cmd: list[str]) -> str:
    """Komut çalıştırır ve çıktıyı Windows kodlama hatalarına takılmadan döndürür."""
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        print("HATA: Komut başarısız oldu:")
        print(" ".join(cmd))
        if err:
            print(err[:2000])
        sys.exit(result.returncode)

    return result.stdout or ""


def docker_db_cmd(container: str, *args: str) -> list[str]:
    """PostgreSQL komutlarını container içinde UTF-8 client encoding ile çalıştırır."""
    return [
        "docker", "exec",
        "-e", "PGCLIENTENCODING=UTF8",
        container,
        *args,
    ]


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"parlayan_rapor_{ts}.zip"

    container = os.getenv("DB_CONTAINER", "parlayan_db")
    db_user = os.getenv("DB_USER", "parlayan")
    db_name = os.getenv("DB_NAME", "parlayan")

    print(f"📦 Rapor hazırlanıyor: {zip_name}")

    # 1. SQL dump
    print("  → Veritabanı dump alınıyor...")
    sql_dump = run(docker_db_cmd(
        container,
        "pg_dump",
        "-U", db_user,
        "--encoding=UTF8",
        db_name,
    ))

    # 2. JSON özetler (psql ile)
    def query_json(sql: str) -> str:
        wrapped_sql = f"SELECT COALESCE(json_agg(t), '[]'::json) FROM ({sql}) t"

        out = run(docker_db_cmd(
            container,
            "psql",
            "-U", db_user,
            db_name,
            "-v", "ON_ERROR_STOP=1",
            "--no-align",
            "--tuples-only",
            "-c", wrapped_sql,
        ))

        cleaned = (out or "").strip()
        return cleaned if cleaned and cleaned != "null" else "[]"

    print("  → İşlem özeti alınıyor...")
    trades_json = query_json("""
        SELECT symbol, status, entry_ts::text, exit_ts::text,
               entry_price::float, exit_price::float,
               pnl_pct::float, pnl_quote::float,
               exit_reason, quote_size::float,
               stop_loss_pct::float, take_profit_pct::float,
               max_price::float
        FROM paper_trades ORDER BY entry_ts DESC LIMIT 200
    """)

    print("  → Parlayan adaylar alınıyor...")
    candidates_json = query_json("""
        SELECT symbol, status, detected_at::text,
               price_at_detection::float, price_change_24h_pct::float,
               parlayan_score::float, volume_ratio::float, rsi::float,
               peak_gain_pct::float, entry_price::float
        FROM parlayan_candidates
        WHERE detected_at > now() - interval '7 days'
        ORDER BY detected_at DESC LIMIT 300
    """)

    print("  → Market snapshot özeti alınıyor...")
    snapshots_json = query_json("""
        SELECT DISTINCT ON (symbol)
            symbol, ts::text, price::float,
            price_change_24h_pct::float, price_change_5m_pct::float,
            price_change_15m_pct::float, volume_ratio::float,
            rsi::float, parlayan_score::float,
            momentum_score::float, liquidity_score::float, fake_pump_risk::float,
            extra
        FROM market_snapshots
        WHERE ts > now() - interval '10 minutes'
        ORDER BY symbol, ts DESC
    """)

    print("  → Bot olayları alınıyor...")
    events_json = query_json("""
        SELECT level, category, message, ts::text,
               details::text
        FROM bot_events
        ORDER BY ts DESC LIMIT 500
    """)

    print("  → İstatistikler alınıyor...")
    stats_json = query_json("""
        SELECT
            COUNT(*) FILTER (WHERE status='OPEN') AS open_trades,
            COUNT(*) FILTER (WHERE status='CLOSED') AS closed_trades,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct > 0) AS wins,
            COUNT(*) FILTER (WHERE status='CLOSED' AND pnl_pct <= 0) AS losses,
            ROUND(AVG(pnl_pct) FILTER (WHERE status='CLOSED')::numeric, 3) AS avg_pnl,
            ROUND(SUM(pnl_quote) FILTER (WHERE status='CLOSED')::numeric, 3) AS total_pnl_usdt,
            ROUND(MAX(pnl_pct) FILTER (WHERE status='CLOSED')::numeric, 3) AS best_pct,
            ROUND(MIN(pnl_pct) FILTER (WHERE status='CLOSED')::numeric, 3) AS worst_pct
        FROM paper_trades
    """)


    print("  → Coin zaman çizelgesi alınıyor...")
    timelines_json = query_json("""
        SELECT symbol, ts::text, price::float,
               price_change_24h_pct::float, price_change_5m_pct::float,
               price_change_15m_pct::float, price_change_30m_pct::float,
               volume_ratio::float, rsi::float, parlayan_score::float,
               momentum_score::float, liquidity_score::float, fake_pump_risk::float,
               spread_pct::float, extra
        FROM market_snapshots
        WHERE ts > now() - interval '48 hours'
        ORDER BY symbol, ts ASC
        LIMIT 200000
    """)

    print("  → Profesyonel sinyal olayları alınıyor...")
    signal_events_json = query_json("""
        SELECT symbol, event_type, severity, score::float, price::float, ts::text, details
        FROM signal_events
        ORDER BY ts DESC LIMIT 5000
    """)

    print("  → Equity curve alınıyor...")
    equity_json = query_json("""
        SELECT ts::text, equity_usdt::float, realized_pnl_usdt::float,
               open_risk_usdt::float, open_trades, daily_pnl_usdt::float,
               max_drawdown_pct::float, details
        FROM paper_equity_curve
        ORDER BY ts ASC LIMIT 20000
    """)


    # Manifest
    manifest = {
        "version": "parlayan_bot_professional_paper_v3",
        "exported_at": datetime.now().isoformat(),
        "contents": [
            "rapor.sql",
            "trades.json",
            "candidates.json",
            "snapshots.json",
            "events.json",
            "stats.json",
            "timelines_48h.json",
            "signal_events.json",
            "equity_curve.json",
        ],
    }

    # ZIP'e yaz
    print(f"  → {zip_name} oluşturuluyor...")
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr("rapor.sql", sql_dump)
        zf.writestr("trades.json", trades_json)
        zf.writestr("candidates.json", candidates_json)
        zf.writestr("snapshots.json", snapshots_json)
        zf.writestr("events.json", events_json)
        zf.writestr("stats.json", stats_json)
        zf.writestr("timelines_48h.json", timelines_json)
        zf.writestr("signal_events.json", signal_events_json)
        zf.writestr("equity_curve.json", equity_json)

    size_kb = os.path.getsize(zip_name) / 1024
    print(f"\n✅ Rapor hazır: {zip_name} ({size_kb:.0f} KB)")
    print("   Bu dosyayı Claude'a yükleyerek analiz ettirebilirsin.")


if __name__ == "__main__":
    main()
