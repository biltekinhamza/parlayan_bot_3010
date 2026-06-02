from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx


class BinanceRateLimitError(RuntimeError):
    pass


class BinancePublicClient:
    """Public Binance REST client. API anahtarı kullanmaz."""

    def __init__(self, base_url: str, timeout_seconds: int = 12, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds)
        self._last_request_ts = 0.0
        self.min_request_gap_seconds = 0.10  # Rate limit koruması

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(1, self.max_retries + 1):
            elapsed = time.monotonic() - self._last_request_ts
            if elapsed < self.min_request_gap_seconds:
                await asyncio.sleep(self.min_request_gap_seconds - elapsed)
            self._last_request_ts = time.monotonic()
            response = await self._client.get(path, params=params)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1"))
                await asyncio.sleep(min(max(retry_after, attempt), 10))
                continue
            if response.status_code == 418:
                raise BinanceRateLimitError("Binance IP geçici olarak engellendi.")
            if 500 <= response.status_code < 600:
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            response.raise_for_status()
            return response.json()
        raise BinanceRateLimitError(f"Binance isteği {self.max_retries} denemede başarısız: {path}")

    async def exchange_info(self) -> dict[str, Any]:
        return await self._get("/api/v3/exchangeInfo")

    async def ticker_24hr(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v3/ticker/24hr")
        return [data] if isinstance(data, dict) else data

    async def book_ticker(self) -> list[dict[str, Any]]:
        data = await self._get("/api/v3/ticker/bookTicker")
        return [data] if isinstance(data, dict) else data

    async def ticker_prices(self) -> dict[str, float]:
        data = await self._get("/api/v3/ticker/price")
        items = [data] if isinstance(data, dict) else data
        out: dict[str, float] = {}
        for item in items:
            try:
                out[str(item.get("symbol"))] = float(item.get("price"))
            except Exception:
                continue
        return out

    async def klines(self, symbol: str, interval: str = "5m", limit: int = 120) -> list[list[Any]]:
        return await self._get("/api/v3/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
