from __future__ import annotations

from typing import Any

from . import storage


class MarketDNAEngine:
    """
    V4.5 Market DNA / Self Learning Engine.

    Pattern Memory olaylarını toplu istatistiğe dönüştürür.
    Strateji kurallarını doğrudan değiştirmez; karar destek katmanı olarak hangi
    metriklerin çalışan/çalışmayan hareketlerde ayrıştığını raporlar.
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def refresh(self) -> dict[str, Any]:
        cfg = self.config.get("market_dna", {})
        if not cfg.get("enabled", True):
            return {"enabled": False, "profiles_updated": 0}
        return storage.refresh_market_dna_profiles(
            lookback_days=int(cfg.get("lookback_days", 30)),
            min_samples=int(cfg.get("min_samples", 8)),
        )
