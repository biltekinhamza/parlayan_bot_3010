from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_STABLE_BASE_ASSETS = {
    "USDE",
    "USDC",
    "FDUSD",
    "TUSD",
    "USDP",
    "DAI",
    "BUSD",
    "USDD",
    "USDJ",
    "USD1",
    "PYUSD",
    "RLUSD",
    "AEUR",
    "EUR",
    "EURC",
    "EURI",
    "PAX",
    "PAXG",
    "GUSD",
    "SUSD",
    "LUSD",
    "FRAX",
    "USDS",
    "USDX",
    "XUSD",
    "USDL",
}

DEFAULT_BLOCKED_SYMBOLS = {
    "USDEUSDT",
    "USDCUSDT",
    "FDUSDUSDT",
    "TUSDUSDT",
    "USDPUSDT",
    "DAIUSDT",
    "BUSDUSDT",
    "USDDUSDT",
    "USDJUSDT",
    "USD1USDT",
    "PYUSDUSDT",
    "RLUSDUSDT",
    "AEURUSDT",
    "EURUSDT",
    "EURCUSDT",
    "EURIUSDT",
    "PAXUSDT",
    "PAXGUSDT",
    "GUSDUSDT",
    "SUSDUSDT",
    "LUSDUSDT",
    "FRAXUSDT",
    "USDSUSDT",
    "USDXUSDT",
    "XUSDUSDT",
    "USDLUSDT",
}


@dataclass(frozen=True, slots=True)
class StableAssetDecision:
    blocked: bool
    reason: str
    symbol: str
    base_asset: str | None
    quote_asset: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "symbol": self.symbol,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
        }


def stable_filter_config(config: dict[str, Any]) -> dict[str, Any]:
    return dict(config.get("stable_asset_filter", {}) or {})


def is_enabled(config: dict[str, Any]) -> bool:
    return bool(stable_filter_config(config).get("enabled", True))


def get_stable_base_assets(config: dict[str, Any]) -> set[str]:
    cfg = stable_filter_config(config)
    configured = cfg.get("stable_base_assets")
    if configured is None:
        configured = sorted(DEFAULT_STABLE_BASE_ASSETS)
    extra = cfg.get("extra_stable_base_assets", [])
    return {str(item).upper().strip() for item in [*configured, *extra] if str(item).strip()}


def get_blocked_symbols(config: dict[str, Any]) -> set[str]:
    cfg = stable_filter_config(config)
    configured = cfg.get("blocked_symbols")
    if configured is None:
        configured = sorted(DEFAULT_BLOCKED_SYMBOLS)
    extra = cfg.get("extra_blocked_symbols", [])
    return {str(item).upper().strip() for item in [*configured, *extra] if str(item).strip()}


def infer_base_asset(symbol: str, quote_asset: str = "USDT") -> str:
    sym = str(symbol or "").upper().strip()
    quote = str(quote_asset or "USDT").upper().strip()
    if quote and sym.endswith(quote) and len(sym) > len(quote):
        return sym[: -len(quote)]
    return sym


def evaluate_symbol(
    symbol: str,
    config: dict[str, Any],
    base_asset: str | None = None,
    quote_asset: str | None = None,
) -> StableAssetDecision:
    sym = str(symbol or "").upper().strip()
    quote = str(quote_asset or config.get("binance", {}).get("quote_asset", "USDT")).upper().strip()
    base = str(base_asset or infer_base_asset(sym, quote)).upper().strip()

    if not is_enabled(config):
        return StableAssetDecision(False, "stable_asset_filter_disabled", sym, base, quote)

    if sym in get_blocked_symbols(config):
        return StableAssetDecision(True, f"stable/synthetic symbol blocked: {sym}", sym, base, quote)

    if base in get_stable_base_assets(config):
        return StableAssetDecision(True, f"stable/synthetic base asset blocked: {base}", sym, base, quote)

    return StableAssetDecision(False, "allowed", sym, base, quote)
