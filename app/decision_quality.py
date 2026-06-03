from __future__ import annotations

from . import storage


def refresh(hours: int = 36) -> dict:
    return storage.refresh_decision_outcomes(hours=hours)


def report(hours: int = 36, horizon_minutes: int = 240, auto_refresh: bool = True) -> dict:
    return storage.get_decision_quality_report(hours=hours, horizon_minutes=horizon_minutes, auto_refresh=auto_refresh)
