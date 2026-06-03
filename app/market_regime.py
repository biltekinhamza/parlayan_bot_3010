from __future__ import annotations

from typing import Any

from . import storage


def compute_market_regime(features: list[Any]) -> dict[str, Any]:
    return storage.compute_and_store_market_regime(features)


def get_market_regime_report(hours: int = 24) -> dict[str, Any]:
    return storage.get_market_regime_report(hours=hours)
