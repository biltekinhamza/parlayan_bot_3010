from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal
from datetime import date, datetime, time
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://parlayan:parlayan_pass@localhost:5432/parlayan")


def json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def fetch_rows(conn: psycopg.Connection, threshold: float, minutes_before: int, limit: int) -> list[dict[str, Any]]:
    window_low = max(0, minutes_before - 5)
    window_high = minutes_before + 5
    sql = """
    WITH pumps AS (
      SELECT symbol, MAX(price_change_24h_pct) AS max_24h
      FROM market_snapshots
      GROUP BY symbol
      HAVING MAX(price_change_24h_pct) >= %(threshold)s
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
      WHERE ms.ts BETWEEN pt.peak_ts - (%(minutes)s || ' minutes')::interval - interval '5 minutes'
                      AND pt.peak_ts - (%(minutes)s || ' minutes')::interval + interval '5 minutes'
    )
    SELECT *
    FROM before_peak
    WHERE minutes_before_peak BETWEEN %(window_low)s AND %(window_high)s
    ORDER BY peak_24h DESC, symbol, ts DESC
    LIMIT %(limit)s
    """
    return list(conn.execute(sql, {
        "threshold": threshold,
        "minutes": minutes_before,
        "window_low": window_low,
        "window_high": window_high,
        "limit": limit,
    }).fetchall())


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        out: list[float] = []
        for row in rows:
            try:
                if row.get(key) is not None:
                    out.append(float(row[key]))
            except Exception:
                pass
        return out

    def avg(key: str) -> float:
        vals = values(key)
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    phase_counts: dict[str, int] = {}
    profile_counts: dict[str, int] = {}
    for row in rows:
        extra = row.get("extra") or {}
        phase = str(extra.get("market_phase") or row.get("bot_state") or "UNKNOWN")
        profile = str(extra.get("v4_profile") or "UNKNOWN")
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
        profile_counts[profile] = profile_counts.get(profile, 0) + 1

    return {
        "rows": len(rows),
        "avg_peak_24h": avg("peak_24h"),
        "avg_rsi": avg("rsi"),
        "avg_volume_ratio": avg("volume_ratio"),
        "avg_5m": avg("price_change_5m_pct"),
        "avg_15m": avg("price_change_15m_pct"),
        "avg_30m": avg("price_change_30m_pct"),
        "avg_parlayan_score": avg("parlayan_score"),
        "phase_counts": phase_counts,
        "profile_counts": profile_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Pump Detective v4: Pump öncesi davranış analizi.")
    parser.add_argument("--threshold", type=float, default=30.0, help="Pump sayılacak max 24h yüzdesi.")
    parser.add_argument("--minutes-before", type=int, default=60, help="Zirveden kaç dakika önce analiz edilecek.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", default="pump_detective_v4_report.json")
    args = parser.parse_args()

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        rows = fetch_rows(conn, args.threshold, args.minutes_before, args.limit)

    report = {
        "threshold": args.threshold,
        "minutes_before": args.minutes_before,
        "summary": summarize(rows),
        "rows": rows,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(json_safe(report), handle, ensure_ascii=False, indent=2)
    print(json.dumps(json_safe(report["summary"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
