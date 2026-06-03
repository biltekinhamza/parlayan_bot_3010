from __future__ import annotations

from . import storage


def reject_reason_report(hours: int = 24, limit: int = 100) -> dict:
    return storage.get_reject_reason_report(hours=hours, limit=limit)


def near_miss_report(hours: int = 36, horizon_minutes: int = 240, min_upside_pct: float = 5.0, limit: int = 100) -> dict:
    return storage.get_near_miss_report(hours=hours, horizon_minutes=horizon_minutes, min_upside_pct=min_upside_pct, limit=limit)
