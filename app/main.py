from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import config_store
from .db import db
from .scanner import ScannerService
from .storage import log_event, set_metadata, start_paper_session, get_current_session

APP_VERSION = "1.4.5-professional-paper-v4.5"

scanner_service: ScannerService | None = None
scanner_task: asyncio.Task | None = None
position_monitor_task: asyncio.Task | None = None


def scanner_status() -> dict:
    return {
        "version": APP_VERSION,
        "initialized": scanner_service is not None,
        "running": bool(scanner_service.running) if scanner_service else False,
        "scanner_task_active": bool(scanner_task and not scanner_task.done()),
        "position_monitor_active": bool(position_monitor_task and not position_monitor_task.done()),
        "session": get_current_session(),
    }


def start_scanner() -> dict:
    global scanner_task
    if scanner_service is None:
        return {"ok": False, "message": "Scanner başlatılmamış"}
    if scanner_task and not scanner_task.done() and scanner_service.running:
        return {"ok": True, "message": "Scanner zaten çalışıyor", "status": scanner_status()}
    scanner_task = asyncio.create_task(scanner_service.run_loop())
    log_event("INFO", "scanner", "Scanner dashboard'dan başlatıldı", {})
    return {"ok": True, "message": "Scanner başlatıldı", "status": scanner_status()}


def stop_scanner() -> dict:
    global scanner_task
    if scanner_service is None:
        return {"ok": False, "message": "Scanner başlatılmamış"}
    scanner_service.stop()
    if scanner_task and not scanner_task.done():
        scanner_task.cancel()
    scanner_task = None
    log_event("INFO", "scanner", "Scanner dashboard'dan durduruldu", {})
    return {"ok": True, "message": "Scanner durduruldu", "status": scanner_status()}


def apply_runtime_config(config: dict, reason: str | None = None) -> dict:
    if scanner_service is not None:
        scanner_service.reload_config(config)
    log_event("INFO", "config", "Runtime config güncellendi", {"reason": reason})
    return {"ok": True, "status": scanner_status()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scanner_service, scanner_task, position_monitor_task

    db.connect()
    config = config_store.get()

    session = start_paper_session(config_snapshot=config, strategy_version=config.get("app", {}).get("strategy_version", "professional_paper_v44"))
    scanner_service = ScannerService(config)
    set_metadata("scanner_state", {"running": False})
    log_event("INFO", "app", "Parlayan Bot başlatıldı", {
        "version": APP_VERSION,
        "mode": config.get("app", {}).get("mode", "paper"),
        "session": session,
    })

    # Pozisyon monitörü her zaman çalışır
    if config.get("position_monitor", {}).get("enabled", True):
        position_monitor_task = asyncio.create_task(scanner_service.run_position_monitor_loop())

    # Auto-scan
    auto_env = os.getenv("AUTO_SCANNER_ENABLED", "true").lower() == "true"
    if auto_env and config.get("scanner", {}).get("auto_scan_enabled", True):
        scanner_task = asyncio.create_task(scanner_service.run_loop())

    try:
        yield
    finally:
        if scanner_service:
            scanner_service.stop()
            await scanner_service.close()
        if scanner_task and not scanner_task.done():
            scanner_task.cancel()
        if position_monitor_task and not position_monitor_task.done():
            position_monitor_task.cancel()
        log_event("INFO", "app", "Parlayan Bot durduruldu", {})
        db.close()


app = FastAPI(
    title="Parlayan Bot — Günlük Hareket Eden Coinler",
    version=APP_VERSION,
    lifespan=lifespan,
)
app.include_router(router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
def index():
    return FileResponse("app/static/index.html")
