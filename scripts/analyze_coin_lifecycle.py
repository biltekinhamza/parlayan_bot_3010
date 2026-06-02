from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean


def f(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    return []


def summarize_symbol(rows: list[dict]) -> dict:
    if not rows:
        return {}
    symbol = rows[0].get("symbol")
    prices = [f(r.get("price")) for r in rows if f(r.get("price")) > 0]
    vols = [f(r.get("volume_ratio"), 1.0) for r in rows]
    pre_scores = [f((r.get("extra") or {}).get("pre_pump_score")) for r in rows]
    phases = [(r.get("extra") or {}).get("market_phase") for r in rows]
    changes_5m = [f(r.get("price_change_5m_pct")) for r in rows]
    changes_15m = [f(r.get("price_change_15m_pct")) for r in rows]

    first_price = prices[0] if prices else 0
    max_price = max(prices) if prices else 0
    end_price = prices[-1] if prices else 0
    max_gain_pct = ((max_price - first_price) / first_price * 100) if first_price else 0
    end_gain_pct = ((end_price - first_price) / first_price * 100) if first_price else 0

    best_idx = max(range(len(rows)), key=lambda i: f((rows[i].get("extra") or {}).get("pre_pump_score"))) if rows else 0
    best = rows[best_idx]

    return {
        "symbol": symbol,
        "snapshots": len(rows),
        "first_ts": rows[0].get("ts"),
        "last_ts": rows[-1].get("ts"),
        "max_gain_from_first_pct": round(max_gain_pct, 4),
        "end_gain_from_first_pct": round(end_gain_pct, 4),
        "avg_volume_ratio": round(mean(vols), 4) if vols else 0,
        "max_volume_ratio": round(max(vols), 4) if vols else 0,
        "max_pre_pump_score": round(max(pre_scores), 4) if pre_scores else 0,
        "best_pre_pump_ts": best.get("ts"),
        "best_pre_pump_phase": (best.get("extra") or {}).get("market_phase"),
        "phase_sequence": [p for i, p in enumerate(phases) if p and (i == 0 or p != phases[i - 1])],
        "max_5m_pct": round(max(changes_5m), 4) if changes_5m else 0,
        "max_15m_pct": round(max(changes_15m), 4) if changes_15m else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Coin yükseliş yaşam döngüsü analizi üretir.")
    parser.add_argument("--timelines", default="timelines_48h.json")
    parser.add_argument("--out", default="coin_lifecycle_report.json")
    args = parser.parse_args()

    rows = load_rows(Path(args.timelines))
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol:
            by_symbol[symbol].append(row)

    summaries = [summarize_symbol(items) for items in by_symbol.values()]
    summaries = [s for s in summaries if s]
    summaries.sort(key=lambda s: (s["max_gain_from_first_pct"], s["max_pre_pump_score"]), reverse=True)

    output = {
        "symbols_analyzed": len(summaries),
        "best_lifecycles": summaries[:50],
        "notes": [
            "max_gain_from_first_pct coinin ilk snapshot sonrası en fazla nereye kadar yükseldiğini gösterir.",
            "best_pre_pump_ts yükselişten önceki en güçlü sinyal anını bulmak için kullanılır.",
            "phase_sequence VOLUME_WAKEUP -> EARLY_MOMENTUM -> ACCUMULATION_BREAKOUT gibi davranış kalıplarını gösterir.",
        ],
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
