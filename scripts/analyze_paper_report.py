from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def as_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("trades", "items", "data", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def summarize_trades(trades: list[dict]) -> dict:
    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    open_trades = [t for t in trades if str(t.get("status", "")).upper() == "OPEN"]
    pnls = [as_float(t.get("pnl_pct")) for t in closed if t.get("pnl_pct") is not None]
    pnl_quotes = [as_float(t.get("pnl_quote")) for t in closed if t.get("pnl_quote") is not None]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    reasons = Counter(str(t.get("exit_reason") or "UNKNOWN") for t in closed)
    by_symbol = defaultdict(list)
    for t in closed:
        by_symbol[str(t.get("symbol"))].append(as_float(t.get("pnl_pct")))

    worst_symbols = sorted(
        ((symbol, len(values), sum(values), mean(values)) for symbol, values in by_symbol.items()),
        key=lambda item: item[2],
    )[:10]

    best_symbols = sorted(
        ((symbol, len(values), sum(values), mean(values)) for symbol, values in by_symbol.items()),
        key=lambda item: item[2],
        reverse=True,
    )[:10]

    return {
        "total_trades": len(trades),
        "open_trades": len(open_trades),
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round((len(wins) / len(closed) * 100), 2) if closed else 0.0,
        "avg_pnl_pct": round(mean(pnls), 4) if pnls else 0.0,
        "median_pnl_pct": round(median(pnls), 4) if pnls else 0.0,
        "total_pnl_quote": round(sum(pnl_quotes), 4) if pnl_quotes else 0.0,
        "best_trade_pct": round(max(pnls), 4) if pnls else 0.0,
        "worst_trade_pct": round(min(pnls), 4) if pnls else 0.0,
        "exit_reasons": dict(reasons.most_common()),
        "best_symbols": [
            {"symbol": s, "trades": c, "total_pnl_pct": round(total, 4), "avg_pnl_pct": round(avg, 4)}
            for s, c, total, avg in best_symbols
        ],
        "worst_symbols": [
            {"symbol": s, "trades": c, "total_pnl_pct": round(total, 4), "avg_pnl_pct": round(avg, 4)}
            for s, c, total, avg in worst_symbols
        ],
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Paper trade performans raporu üretir.")
    parser.add_argument("--trades", default="trades.json", help="trades.json yolu")
    parser.add_argument("--out", default="paper_performance_report.json", help="çıktı JSON yolu")
    args = parser.parse_args()

    trades = load_json(Path(args.trades))
    summary = summarize_trades(trades)
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
