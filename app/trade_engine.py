from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import storage
from .risk_manager import PaperRiskManager


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class TradeEngine:
    """
    Professional paper trade engine v4.2.

    Bu motor hâlâ gerçek emir göndermez. Amacı kendimizi kandırmayan paper-trade:
    - Eski session açık pozisyonları global riskte izler.
    - Aynı sembolde eski session açık trade varsa tekrar girmez.
    - Entry/exit fill fiyatlarına gerçekçi adverse slippage uygular.
    - Stop grace period faza göre değişir; FOMO/DANGER grace almaz.
    - PnL hesabı fill fiyatları + fee ile yapılır, slippage ayrıca iki kez düşülmez.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.risk_manager = PaperRiskManager(config)

    @property
    def cfg(self) -> dict[str, Any]:
        return self.config.get("paper_trading", {})

    def maybe_open(
        self,
        candidate_id: str,
        symbol: str,
        price: float,
        context: dict[str, Any],
    ) -> str | None:
        cfg = self.cfg

        if not cfg.get("enabled", True):
            self._reject(symbol, "paper_disabled", price, context)
            return None

        parlayan_score = _f(context.get("parlayan_score"))
        hard_min_score = _f(cfg.get("hard_min_parlayan_score"), 45)
        if parlayan_score < hard_min_score:
            self._reject(symbol, "hard_min_parlayan_score", price, context, {"parlayan_score": parlayan_score, "min": hard_min_score})
            return None

        # V4.1: current session değil, tüm session açık pozisyonları global riskte say.
        open_trades = storage.get_open_paper_trades(all_time=True)
        if len(open_trades) >= int(cfg.get("max_open_trades", 3)):
            self._reject(symbol, "max_open_trades_global", price, context, {"open_trades": len(open_trades), "max": cfg.get("max_open_trades")})
            return None

        if storage.get_open_trades_for_symbol(symbol, all_time=True):
            self._reject(symbol, "symbol_already_open_global", price, context)
            return None

        if storage.get_active_cooldown(symbol):
            self._reject(symbol, "symbol_in_cooldown", price, context)
            return None

        max_entries = int(cfg.get("max_entries_per_symbol_12h", 1))
        recent = storage.get_recent_entry_count(symbol, hours=12, all_time=True)
        if recent >= max_entries:
            self._reject(symbol, "recent_entry_limit_global", price, context, {"recent_entries_12h": recent, "max": max_entries})
            return None

        quote_size = _f(cfg.get("default_quote_size_usdt"), 100)

        risk_decision = self.risk_manager.evaluate_new_position(symbol, quote_size, context)
        if not risk_decision.allowed:
            storage.insert_signal_event(
                symbol=symbol,
                event_type="RISK_BLOCK",
                severity="WARNING",
                score=_f(context.get("pre_pump_score")),
                price=price,
                details={"reason": risk_decision.reason, **risk_decision.details},
            )
            storage.log_event("WARNING", "risk", f"{symbol} risk filtresi: {risk_decision.reason}", risk_decision.details)
            return None

        entry_fill_price, slippage_details = self._entry_fill(symbol, price, context)
        enriched_context = dict(context or {})
        enriched_context.update({
            "raw_signal_price": price,
            "entry_fill_price": entry_fill_price,
            "execution_model": "adverse_slippage_v4_2",
            "slippage_details": slippage_details,
        })

        trade_cfg = {
            "fee_rate_estimate": _f(cfg.get("fee_rate_estimate"), 0.001),
            "slippage_pct_estimate": float(slippage_details["entry_slippage_pct"]),
            "stop_loss_pct": _f(cfg.get("stop_loss_pct"), 2.5),
            "trailing_start_pct": _f(cfg.get("trailing_start_pct"), 7.0),
            "trailing_gap_pct": _f(cfg.get("trailing_gap_pct"), 3.5),
            "take_profit_pct": _f(cfg.get("take_profit_pct"), 15.0),
        }

        trade_id = storage.open_paper_trade(candidate_id, symbol, entry_fill_price, quote_size, trade_cfg, enriched_context)
        if trade_id:
            storage.insert_signal_event(
                symbol=symbol,
                event_type="EXECUTION_FILL",
                severity="INFO",
                score=_f(context.get("pre_pump_score") or context.get("parlayan_score")),
                price=entry_fill_price,
                details={
                    "side": "BUY",
                    "trade_id": trade_id,
                    "raw_price": price,
                    "fill_price": entry_fill_price,
                    **slippage_details,
                },
            )
        return trade_id

    def _reject(self, symbol: str, reason: str, price: float, context: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
        details = {"reason": reason, "context": context}
        if extra:
            details.update(extra)
        storage.insert_signal_event(
            symbol=symbol,
            event_type="TRADE_REJECT",
            severity="INFO",
            score=_f(context.get("pre_pump_score") or context.get("parlayan_score")),
            price=price,
            details=details,
        )

    def _dynamic_slippage_pct(self, context: dict[str, Any], side: str) -> float:
        cfg = self.cfg
        base = _f(cfg.get("slippage_pct_estimate"), 0.05)
        spread = _f((context.get("professional_metrics") or {}).get("spread_pct") or context.get("spread_pct"), 0.0)
        volume_ratio = max(_f(context.get("volume_ratio"), 1.0), 0.01)
        phase = str(context.get("market_phase") or "")

        spread_component = min(max(spread, 0.0) * 0.55, 0.40)
        thin_volume_component = min(max(1.0 - volume_ratio, 0.0) * 0.08, 0.18)
        hot_phase_component = 0.10 if phase in {"FOMO", "LATE_FOMO", "DANGER"} else 0.0
        sell_extra = 0.03 if side.upper() == "SELL" else 0.0

        pct = base + spread_component + thin_volume_component + hot_phase_component + sell_extra
        return round(min(max(pct, 0.01), _f(cfg.get("max_slippage_pct_estimate"), 0.75)), 4)

    def _entry_fill(self, symbol: str, raw_price: float, context: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        slippage_pct = self._dynamic_slippage_pct(context, "BUY")
        fill = raw_price * (1 + slippage_pct / 100)
        return fill, {
            "symbol": symbol,
            "side": "BUY",
            "raw_price": raw_price,
            "entry_slippage_pct": slippage_pct,
            "fill_price": fill,
        }

    def _exit_fill(self, raw_exit_reference: float, context: dict[str, Any], reason: str) -> tuple[float, dict[str, Any]]:
        slippage_pct = self._dynamic_slippage_pct(context, "SELL")
        if reason in {"STOP_LOSS", "PROFIT_PROTECTION", "TRAILING_STOP"}:
            slippage_pct += _f(self.cfg.get("stop_extra_slippage_pct"), 0.08)
        fill = raw_exit_reference * (1 - slippage_pct / 100)
        return fill, {
            "side": "SELL",
            "reason": reason,
            "raw_exit_reference": raw_exit_reference,
            "exit_slippage_pct": round(slippage_pct, 4),
            "fill_price": fill,
        }

    def update_open_trades(self, latest_prices: dict[str, float]) -> None:
        cfg = self.cfg
        now = datetime.now(timezone.utc)
        storage.cleanup_expired_cooldowns()

        # V4.1: eski session açık pozisyonları da yönet.
        for trade in storage.get_open_paper_trades(all_time=True):
            symbol = trade["symbol"]
            price = latest_prices.get(symbol)
            if price is None or price <= 0:
                continue
            self._update_one(trade, price, now, cfg)

    def _update_one(self, trade: dict[str, Any], price: float, now: datetime, cfg: dict[str, Any]) -> None:
        symbol = trade["symbol"]
        entry = _f(trade["entry_price"])
        if entry <= 0:
            return

        context = dict(trade.get("context") or {})
        current_max = max(_f(trade.get("max_price"), entry), price)
        max_gain_pct = ((current_max - entry) / entry) * 100
        current_gain_pct = ((price - entry) / entry) * 100

        fee_pct = _f(trade.get("fee_rate_estimate", 0.001)) * 2 * 100

        stop_loss_pct = self._phase_stop_loss_pct(trade, context, cfg)
        trailing_start_pct = _f(trade.get("trailing_start_pct"), _f(cfg.get("trailing_start_pct"), 7.0))
        trailing_gap_pct = self._phase_trailing_gap_pct(context, trade, cfg)
        take_profit_pct = _f(trade.get("take_profit_pct"), _f(cfg.get("take_profit_pct"), 15.0))
        max_minutes = _f(cfg.get("max_minutes_in_trade", 180))

        state = dict(trade.get("protection_state") or {})
        protected_stop = _f(trade.get("protected_stop_price"), 0.0) or None
        current_net_gain_pct = current_gain_pct - fee_pct
        state["max_runup_pct"] = round(max(_f(state.get("max_runup_pct"), -999.0), max_gain_pct - fee_pct), 4)
        state["max_drawdown_pct"] = round(min(_f(state.get("max_drawdown_pct"), 0.0), current_net_gain_pct), 4)

        if current_gain_pct >= take_profit_pct:
            exit_fill, fill_details = self._exit_fill(price, context, "TAKE_PROFIT")
            net_pnl = ((exit_fill - entry) / entry * 100) - fee_pct
            pnl_usdt = _f(trade["quote_size"]) * net_pnl / 100
            self._record_close_context(trade, exit_fill, "TAKE_PROFIT", net_pnl, pnl_usdt, fee_pct, fill_details, state, now)
            storage.close_paper_trade(str(trade["id"]), exit_fill, "TAKE_PROFIT", net_pnl, pnl_usdt)
            storage.insert_signal_event(symbol, "EXIT", "INFO", net_pnl, exit_fill, {"reason": "TAKE_PROFIT", "trade_id": str(trade["id"]), **fill_details})
            self._post_close(symbol, "TAKE_PROFIT", net_pnl, trade)
            return

        be_start = _f(cfg.get("break_even_start_pct", 3.5))
        if max_gain_pct >= be_start:
            be_buffer = _f(cfg.get("break_even_buffer_pct", 1.5))
            be_price = entry * (1 + (fee_pct + be_buffer) / 100)
            if protected_stop is None or be_price > protected_stop:
                protected_stop = be_price
                state["break_even_active"] = True
                state["break_even_price"] = round(be_price, 8)
                state["break_even_buffer_pct"] = be_buffer

        if max_gain_pct >= trailing_start_pct:
            trail_price = current_max * (1 - trailing_gap_pct / 100)
            if protected_stop is not None:
                trail_price = max(trail_price, protected_stop)
            if protected_stop is None or trail_price > protected_stop:
                protected_stop = trail_price
            state["trailing_active"] = True
            state["trailing_stop"] = round(protected_stop, 8)
            state["trailing_gap_pct"] = trailing_gap_pct

        ladder_stop, ladder_state = self._smart_trailing_ladder(entry, max_gain_pct, fee_pct, context, cfg)
        if ladder_stop is not None and (protected_stop is None or ladder_stop > protected_stop):
            protected_stop = ladder_stop
            state.update(ladder_state)
            state["smart_ladder_active"] = True

        entry_ts = trade.get("entry_ts")
        age_minutes = 0.0
        if isinstance(entry_ts, datetime):
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=timezone.utc)
            age_minutes = (now - entry_ts).total_seconds() / 60

        gross_stop = entry * (1 - stop_loss_pct / 100)
        active_stop = protected_stop if protected_stop else gross_stop
        state["effective_stop_loss_pct"] = stop_loss_pct
        state["active_stop"] = round(active_stop, 8)
        state["current_unrealized_pct"] = round(current_gain_pct - fee_pct, 4)

        storage.update_trade_price(str(trade["id"]), price, current_max, protected_stop, state)

        grace_minutes = self._phase_grace_minutes(context, cfg)
        stop_grace_active = protected_stop is None and age_minutes < grace_minutes
        if price <= active_stop and not stop_grace_active:
            reason = "PROFIT_PROTECTION" if protected_stop else "STOP_LOSS"
            reference = min(price, active_stop) if reason == "STOP_LOSS" else min(price, active_stop)
            exit_fill, fill_details = self._exit_fill(reference, context, reason)
            net_pnl = ((exit_fill - entry) / entry * 100) - fee_pct
            pnl_usdt = _f(trade["quote_size"]) * net_pnl / 100
            self._record_close_context(trade, exit_fill, reason, net_pnl, pnl_usdt, fee_pct, fill_details, state, now)
            storage.close_paper_trade(str(trade["id"]), exit_fill, reason, net_pnl, pnl_usdt)
            storage.insert_signal_event(symbol, "EXIT", "INFO", net_pnl, exit_fill, {
                "reason": reason,
                "trade_id": str(trade["id"]),
                "age_minutes": round(age_minutes, 2),
                "active_stop": active_stop,
                "entry_profile": context.get("entry_profile"),
                "grace_minutes": grace_minutes,
                **fill_details,
            })
            self._post_close(symbol, reason, net_pnl, trade)
            return

        if stop_grace_active:
            state["stop_grace_active"] = True
            state["stop_grace_remaining_minutes"] = round(max(grace_minutes - age_minutes, 0), 2)
            storage.update_trade_price(str(trade["id"]), price, current_max, protected_stop, state)

        if isinstance(entry_ts, datetime) and age_minutes >= max_minutes:
            exit_fill, fill_details = self._exit_fill(price, context, "MAX_TIME_EXIT")
            net_pnl = ((exit_fill - entry) / entry * 100) - fee_pct
            pnl_usdt = _f(trade["quote_size"]) * net_pnl / 100
            self._record_close_context(trade, exit_fill, "MAX_TIME_EXIT", net_pnl, pnl_usdt, fee_pct, fill_details, state, now)
            storage.close_paper_trade(str(trade["id"]), exit_fill, "MAX_TIME_EXIT", net_pnl, pnl_usdt)
            storage.insert_signal_event(symbol, "EXIT", "INFO", net_pnl, exit_fill, {"reason": "MAX_TIME_EXIT", "trade_id": str(trade["id"]), **fill_details})
            self._post_close(symbol, "MAX_TIME_EXIT", net_pnl, trade)

    def _smart_trailing_ladder(
        self,
        entry: float,
        max_gain_pct: float,
        fee_pct: float,
        context: dict[str, Any],
        cfg: dict[str, Any],
    ) -> tuple[float | None, dict[str, Any]]:
        """
        V4.2 kademeli kâr koruma merdiveni:
        +3.5% -> break-even, +5 -> +2, +8 -> +4, +12 -> +7, +15 -> sıkı trailing.
        Değerler config ile değiştirilebilir.
        """
        ladder = self.config.get("smart_trailing_ladder") or [
            {"runup_pct": 3.5, "lock_pct": 0.0, "label": "break_even"},
            {"runup_pct": 5.0, "lock_pct": 2.0, "label": "lock_2"},
            {"runup_pct": 8.0, "lock_pct": 4.0, "label": "lock_4"},
            {"runup_pct": 12.0, "lock_pct": 7.0, "label": "lock_7"},
            {"runup_pct": 15.0, "lock_pct": 10.0, "label": "tight_trailing"},
        ]
        best: dict[str, Any] | None = None
        for step in ladder:
            if max_gain_pct >= _f(step.get("runup_pct")):
                if best is None or _f(step.get("lock_pct")) > _f(best.get("lock_pct")):
                    best = step
        if best is None:
            return None, {}
        lock_pct = _f(best.get("lock_pct"))
        # fee'yi korumak için kilit stop'a round-trip fee ekle.
        stop_price = entry * (1 + (lock_pct + fee_pct) / 100)
        return stop_price, {
            "smart_ladder_step": best.get("label"),
            "smart_ladder_runup_pct": _f(best.get("runup_pct")),
            "smart_ladder_lock_pct": lock_pct,
            "smart_ladder_stop": round(stop_price, 8),
        }

    def _record_close_context(
        self,
        trade: dict[str, Any],
        exit_fill: float,
        reason: str,
        net_pnl: float,
        pnl_usdt: float,
        fee_pct: float,
        fill_details: dict[str, Any],
        state: dict[str, Any],
        now: datetime,
    ) -> None:
        entry = _f(trade.get("entry_price"))
        gross_pnl_pct = ((exit_fill - entry) / entry * 100) if entry > 0 else 0.0
        entry_slippage = _f((trade.get("context") or {}).get("slippage_details", {}).get("entry_slippage_pct"), _f(trade.get("slippage_pct_estimate")))
        exit_slippage = _f(fill_details.get("exit_slippage_pct"))
        entry_ts = trade.get("entry_ts")
        time_in_trade_min = 0.0
        if isinstance(entry_ts, datetime):
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=timezone.utc)
            time_in_trade_min = max((now - entry_ts).total_seconds() / 60.0, 0.0)
        storage.patch_trade_context(str(trade["id"]), {
            "exit_fill_price": exit_fill,
            "exit_reason": reason,
            "gross_pnl_pct": round(gross_pnl_pct, 5),
            "fee_pct": round(fee_pct, 5),
            "entry_slippage_pct": round(entry_slippage, 5),
            "exit_slippage_pct": round(exit_slippage, 5),
            "total_slippage_pct": round(entry_slippage + exit_slippage, 5),
            "net_pnl_pct": round(net_pnl, 5),
            "net_pnl_usdt": round(pnl_usdt, 5),
            "max_runup_pct": state.get("max_runup_pct"),
            "max_drawdown_pct": state.get("max_drawdown_pct"),
            "time_in_trade_min": round(time_in_trade_min, 2),
            "paper_integrity_model": "net_pnl_after_fill_fee_slippage_v4_2",
        })

    def _phase_stop_loss_pct(self, trade: dict[str, Any], context: dict[str, Any], cfg: dict[str, Any]) -> float:
        base = _f(trade.get("stop_loss_pct"), _f(cfg.get("stop_loss_pct"), 2.5))
        phase = str(context.get("market_phase") or "")
        profile = str(context.get("entry_profile") or "")
        phase_cfg = self.config.get("phase_risk", {}).get("stop_loss_pct", {})
        if phase in phase_cfg:
            return _f(phase_cfg[phase], base)
        if profile == "RECOVERY_ENTRY" or phase == "RECOVERY_COMPRESSION":
            return max(base, _f(cfg.get("recovery_stop_loss_pct"), base))
        if profile in {"MOMENTUM_ENTRY", "VOLUME_WAKEUP_ENTRY"}:
            return max(base, _f(cfg.get("momentum_stop_loss_pct"), base))
        return base

    def _phase_trailing_gap_pct(self, context: dict[str, Any], trade: dict[str, Any], cfg: dict[str, Any]) -> float:
        base = _f(trade.get("trailing_gap_pct"), _f(cfg.get("trailing_gap_pct"), 3.5))
        phase = str(context.get("market_phase") or "")
        phase_cfg = self.config.get("phase_risk", {}).get("trailing_gap_pct", {})
        return _f(phase_cfg.get(phase), base) if phase in phase_cfg else base

    def _phase_grace_minutes(self, context: dict[str, Any], cfg: dict[str, Any]) -> float:
        phase = str(context.get("market_phase") or "")
        profile = str(context.get("entry_profile") or "")
        phase_cfg = self.config.get("phase_risk", {}).get("initial_stop_grace_minutes", {})
        if phase in phase_cfg:
            return _f(phase_cfg[phase], 0.0)
        if phase in {"FOMO", "LATE_FOMO", "DANGER", "DISTRIBUTION"}:
            return 0.0
        if profile == "RECOVERY_ENTRY":
            return min(_f(cfg.get("initial_stop_grace_minutes"), 0.0), 8.0)
        if profile in {"MOMENTUM_ENTRY", "VOLUME_WAKEUP_ENTRY"}:
            return min(_f(cfg.get("initial_stop_grace_minutes"), 0.0), 4.0)
        return _f(cfg.get("initial_stop_grace_minutes"), 0.0)

    def _post_close(self, symbol: str, reason: str, pnl_pct: float, trade: dict[str, Any]) -> None:
        cfg = self.cfg
        now = datetime.now(timezone.utc)

        if reason == "STOP_LOSS" and pnl_pct < 0:
            minutes = int(cfg.get("cooldown_after_stop_minutes", 120))
            storage.set_cooldown(symbol, "stop_loss", now + timedelta(minutes=minutes), {"pnl_pct": pnl_pct})

        elif reason == "PROFIT_PROTECTION" and pnl_pct < 0:
            minutes = int(cfg.get("cooldown_after_protection_stop_minutes", 60))
            storage.set_cooldown(symbol, "protection_stop", now + timedelta(minutes=minutes), {"pnl_pct": pnl_pct})

        elif reason == "MAX_TIME_EXIT":
            minutes = int(cfg.get("cooldown_after_max_time_minutes", 30))
            storage.set_cooldown(symbol, "max_time", now + timedelta(minutes=minutes), {"pnl_pct": pnl_pct})

        storage.record_equity_snapshot(float(self.config.get("paper_trading", {}).get("starting_equity_usdt", 1000)))
        self._check_repeated_losses(symbol, cfg, now)

    def _check_repeated_losses(self, symbol: str, cfg: dict[str, Any], now: datetime) -> None:
        from .db import db
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS c FROM paper_trades
            WHERE symbol=%s AND status='CLOSED' AND pnl_pct < 0
              AND exit_ts > now() - interval '12 hours'
            """,
            (symbol,),
        )
        if row and int(row.get("c", 0)) >= 2:
            minutes = int(cfg.get("cooldown_after_two_losses_minutes", 480))
            storage.set_cooldown(symbol, "repeated_losses", now + timedelta(minutes=minutes), {})
            storage.log_event("WARNING", "trade",
                f"{symbol} 12 saatte 2 zararlı işlem → {minutes} dk cooldown", {})
