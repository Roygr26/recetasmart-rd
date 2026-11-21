"""Clase base abstracta para scrapers de supermercados dominicanos."""

from __future__ import annotations

import abc
import asyncio
import logging
import random
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Pool de User-Agents reales para rotar
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0",
]


class BaseScraper(abc.ABC):
    """Clase base con lógica compartida: reintentos, User-Agent rotation, fallback a Playwright."""

    STORE_NAME: str = ""  # 'jumbo' o 'sirena'
    BASE_URL: str = ""
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.5

    def _random_ua(self) -> str:
        return random.choice(_USER_AGENTS)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._random_ua(),
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
            "Referer": self.BASE_URL,
        }

    # ── Método principal con reintentos ───────────────────────────
    async def search(self, query: str) -> list[dict[str, Any]]:
        """Busca productos con reintentos. Intenta httpx primero, luego Playwright."""
        last_error: Exception | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                results = await self._search_api(query)
                if results:
                    logger.info(
                        "%s: httpx OK para '%s' (intento %d, %d resultados)",
                        self.STORE_NAME, query, attempt, len(results),
                    )
                    return results
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "%s: httpx falló para '%s' (intento %d): %s",
                    self.STORE_NAME, query, attempt, exc,
                )
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY * attempt)

        # Fallback a Playwright
        logger.info("%s: Intentando fallback Playwright para '%s'", self.STORE_NAME, query)
        try:
            results = await self._search_playwright(query)
            if results:
                return results
        except Exception as pw_exc:
            logger.error(
                "%s: Playwright también falló para '%s': %s",
                self.STORE_NAME, query, pw_exc,
            )

        logger.warning("%s: Sin resultados para '%s'", self.STORE_NAME, query)
        return []

    # ── Métodos abstractos que cada scraper implementa ────────────
    @abc.abstractmethod
    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Búsqueda via httpx (API interna / endpoint de búsqueda)."""
        ...

    async def _search_playwright(self, query: str) -> list[dict[str, Any]]:
        """Fallback con Playwright headless. Implementar en subclase si aplica."""
        return []

    # ── Helpers ───────────────────────────────────────────────────
    @staticmethod
    def _clean_price(raw: Any) -> float:
        """Convierte un precio crudo a float."""
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            cleaned = raw.replace("RD$", "").replace("$", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return 0.0
        return 0.0
