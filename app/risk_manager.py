from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import storage


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str
    details: dict[str, Any]


class PaperRiskManager:
    """
    V4.1 global paper risk manager.

    Kritik düzeltme:
    - Risk sadece current session'a bakmaz.
    - Restart öncesinden kalan açık pozisyonları global riskte sayar.
    - Günlük zarar hesabına unrealized PnL dahil edilir.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def cfg(self) -> dict[str, Any]:
        return self.config.get("risk", {})

    def evaluate_new_position(self, symbol: str, quote_size: float, context: dict[str, Any]) -> RiskDecision:
        if not self.cfg.get("enabled", True):
            return RiskDecision(True, "risk_disabled", {})

        # V4.1: risk scope global açık pozisyonlardır.
        summary_current = storage.get_trade_summary()
        open_trades_global = storage.get_open_paper_trades(all_time=True)
        daily_global = storage.get_daily_paper_stats(all_time=True)
        portfolio_global = storage.get_paper_portfolio_state(
            float(self.config.get("paper_trading", {}).get("starting_equity_usdt", 1000)),
            all_time=True,
        )

        max_open = int(self.cfg.get("max_open_trades", self.config.get("paper_trading", {}).get("max_open_trades", 3)))
        if len(open_trades_global) >= max_open:
            return RiskDecision(False, "max_open_trades_global", {
                "open_trades_global": len(open_trades_global),
                "max_open": max_open,
                "symbols": [t.get("symbol") for t in open_trades_global],
            })

        # Aynı sembol açık kalmışsa restart sonrası tekrar girme.
        if storage.get_open_trades_for_symbol(symbol, all_time=True):
            return RiskDecision(False, "symbol_already_open_global", {"symbol": symbol})

        daily_loss_limit = abs(_f(self.cfg.get("daily_max_loss_usdt"), 35.0))
        if _f(daily_global.get("daily_pnl_usdt")) <= -daily_loss_limit:
            return RiskDecision(False, "daily_loss_limit_including_unrealized", {
                "daily": daily_global,
                "limit": daily_loss_limit,
            })

        unrealized_limit = abs(_f(self.cfg.get("max_unrealized_loss_usdt"), daily_loss_limit * 0.65))
        if _f(portfolio_global.get("unrealized_pnl_usdt")) <= -unrealized_limit:
            return RiskDecision(False, "unrealized_loss_limit", {
                "portfolio": portfolio_global,
                "limit": unrealized_limit,
            })

        max_daily_trades = int(self.cfg.get("max_daily_closed_trades", 40))
        if int(daily_global.get("closed_today") or 0) >= max_daily_trades:
            return RiskDecision(False, "max_daily_trade_count", {"daily": daily_global, "limit": max_daily_trades})

        max_symbol_risk = _f(self.cfg.get("max_position_usdt", quote_size))
        if quote_size > max_symbol_risk:
            return RiskDecision(False, "position_size_too_large", {"quote_size": quote_size, "max": max_symbol_risk})

        min_equity = _f(self.cfg.get("min_equity_usdt"), 100.0)
        if _f(portfolio_global.get("equity_usdt")) < min_equity:
            return RiskDecision(False, "equity_too_low_including_unrealized", {"portfolio": portfolio_global, "min_equity": min_equity})

        min_pre_pump = _f(self.cfg.get("min_pre_pump_score_for_entry"), 62.0)
        pre_pump_score = _f(context.get("pre_pump_score"))
        if pre_pump_score < min_pre_pump:
            return RiskDecision(False, "pre_pump_score_low", {"pre_pump_score": pre_pump_score, "min": min_pre_pump})

        phase = str(context.get("market_phase") or "")
        blocked_phases = set(self.cfg.get("blocked_market_phases", ["FOMO", "LATE_FOMO", "DANGER", "DISTRIBUTION"]))
        if phase in blocked_phases:
            return RiskDecision(False, "blocked_market_phase", {"phase": phase})

        max_open_notional = _f(self.cfg.get("max_total_open_notional_usdt"), 999999)
        if _f(portfolio_global.get("open_notional_usdt")) + quote_size > max_open_notional:
            return RiskDecision(False, "max_total_open_notional", {
                "current_open_notional": portfolio_global.get("open_notional_usdt"),
                "quote_size": quote_size,
                "limit": max_open_notional,
            })

        return RiskDecision(True, "allowed", {
            "daily_global": daily_global,
            "portfolio_global": portfolio_global,
            "current_session_summary": summary_current,
        })
