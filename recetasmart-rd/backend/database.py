"""Gestión de caché SQLite asíncrono para resultados de búsqueda."""

from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

from backend.config import settings

_DB_PATH = settings.DB_PATH
_TTL_SECONDS = settings.CACHE_TTL_HOURS * 3600


async def _get_db() -> aiosqlite.Connection:
    """Abre (o crea) la base de datos y asegura que la tabla exista."""
    db = await aiosqlite.connect(_DB_PATH)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS product_cache (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            store       TEXT    NOT NULL,
            query       TEXT    NOT NULL,
            results     TEXT    NOT NULL,
            created_at  REAL    NOT NULL,
            UNIQUE(store, query)
        )
    """)
    await db.commit()
    return db


async def get_cached(store: str, query: str) -> list[dict[str, Any]] | None:
    """Devuelve los resultados cacheados si existen y no han expirado."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "SELECT results, created_at FROM product_cache WHERE store = ? AND query = ?",
            (store, query.lower().strip()),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        results_json, created_at = row
        if (time.time() - created_at) > _TTL_SECONDS:
            # Expirado → borrar
            await db.execute(
                "DELETE FROM product_cache WHERE store = ? AND query = ?",
                (store, query.lower().strip()),
            )
            await db.commit()
            return None
        return json.loads(results_json)
    finally:
        await db.close()


async def set_cache(store: str, query: str, results: list[dict[str, Any]]) -> None:
    """Almacena resultados en caché."""
    db = await _get_db()
    try:
        await db.execute(
            """INSERT INTO product_cache (store, query, results, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(store, query) DO UPDATE SET results = excluded.results, created_at = excluded.created_at
            """,
            (store, query.lower().strip(), json.dumps(results, ensure_ascii=False), time.time()),
        )
        await db.commit()
    finally:
        await db.close()


async def clear_expired() -> int:
    """Elimina entradas expiradas. Devuelve la cantidad eliminada."""
    db = await _get_db()
    try:
        cutoff = time.time() - _TTL_SECONDS
        cursor = await db.execute(
            "DELETE FROM product_cache WHERE created_at < ?", (cutoff,)
        )
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()
