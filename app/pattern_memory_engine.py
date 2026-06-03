from __future__ import annotations

from typing import Any

from . import storage


class PatternMemoryEngine:
    """
    V4.5 Pattern Memory Engine.

    Her scan'de coin lifecycle olaylarını yakalar:
    - Hangi coin hangi zaman penceresinde +10/+20/+30/+50/+100 yaptı?
    - Hareketten önce hangi metrikler kıpırdadı?
    - Trigger anındaki velocity / directional volume / regime nasıldı?
    - Sonuç learning dataset'e nasıl yazıldı?
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def process_scan(self, features: list[Any]) -> dict[str, Any]:
        cfg = self.config.get("pattern_memory", {})
        if not cfg.get("enabled", True):
            return {"enabled": False, "inserted": 0, "events": []}

        thresholds = tuple(float(x) for x in cfg.get("thresholds_pct", [10, 20, 30, 50, 100]))
        horizons = tuple(int(x) for x in cfg.get("horizons_minutes", [15, 30, 60, 240]))
        cooldown_minutes = int(cfg.get("dedupe_cooldown_minutes", 180))
        max_features = int(cfg.get("max_features_per_scan", 240))
        return storage.record_pattern_memory_samples(
            features=features[:max_features],
            thresholds_pct=thresholds,
            horizons_minutes=horizons,
            dedupe_cooldown_minutes=cooldown_minutes,
        )
