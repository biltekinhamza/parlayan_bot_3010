from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://parlayan:parlayan_pass@localhost:5432/parlayan")
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if is_dataclass(value):
        return _json_safe(asdict(value))
    return value


def jsonb(value: Any) -> Jsonb:
    return Jsonb(_json_safe(value if value is not None else {}))


class Database:
    def __init__(self, dsn: str = DATABASE_URL):
        self.dsn = dsn
        self.pool: ConnectionPool | None = None

    def connect(self) -> None:
        if self.pool is None:
            self.pool = ConnectionPool(conninfo=self.dsn, min_size=1, max_size=8, kwargs={"row_factory": dict_row})
            self.ensure_schema()

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()
            self.pool = None

    def ensure_schema(self) -> None:
        if not SCHEMA_PATH.exists():
            return
        with self.connection() as conn:
            with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
                conn.execute(handle.read())
            conn.commit()

    def connection(self):
        if self.pool is None:
            self.connect()
        assert self.pool is not None
        return self.pool.connection()

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> None:
        with self.connection() as conn:
            conn.execute(sql, params)
            conn.commit()

    def fetch_one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple[Any, ...] | dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def executemany(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
        rows = list(rows)
        if not rows:
            return
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()


db = Database()
