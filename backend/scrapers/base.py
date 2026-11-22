"""Clase base abstracta para scrapers de supermercados dominicanos."""

from __future__ import annotations

import abc
import asyncio
import logging
import random
import re
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

# Palabras secundarias que a veces impiden que el buscador del supermercado encuentre el producto
_QUALIFIERS = {
    "fresco", "fresca", "frescos", "frescas",
    "molido", "molida", "molidos", "molidas",
    "entero", "entera", "picado", "picada",
    "maduro", "madura", "maduros", "maduras",
    "verde", "verdes", "rojo", "roja", "blanco", "blanca",
    "de", "del", "al", "para", "con", "en", "o", "u",
    "extra", "virgen", "gran", "puro", "pura",
}


class BaseScraper(abc.ABC):
    """Clase base con lógica compartida: reintentos, User-Agent rotation, términos alternativos."""

    STORE_NAME: str = ""  # 'jumbo' o 'sirena'
    BASE_URL: str = ""
    MAX_RETRIES: int = 2
    RETRY_DELAY: float = 1.0

    def _random_ua(self) -> str:
        return random.choice(_USER_AGENTS)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._random_ua(),
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
            "Referer": self.BASE_URL,
        }

    def _generate_fallback_queries(self, query: str) -> list[str]:
        """Genera variaciones simplificadas de la búsqueda si la principal falla."""
        words = [w.strip() for w in re.split(r"[\s,\-/]+", query.strip().lower()) if w.strip()]
        if len(words) <= 1:
            return []

        fallbacks: list[str] = []

        # Quitar solo calificadores secundarios (ej: "albahaca fresca" -> "albahaca").
        # NUNCA se parte el término en palabras sueltas: "pasta de tomate" -> "pasta"
        # traería espaguetis y produciría matches incorrectos.
        essential_words = [w for w in words if w not in _QUALIFIERS]
        if essential_words and len(essential_words) < len(words):
            fallbacks.append(" ".join(essential_words))

        return fallbacks

    # ── Método principal con reintentos y fallback inteligente ────
    async def search(self, query: str) -> list[dict[str, Any]]:
        """Busca productos. Si el término original no da resultados, prueba variaciones simplificadas."""
        queries_to_try = [query] + self._generate_fallback_queries(query)

        for q in queries_to_try:
            for attempt in range(1, self.MAX_RETRIES + 1):
                try:
                    results = await self._search_api(q)
                    if results:
                        logger.info(
                            "%s: Encontrados %d productos para '%s'%s",
                            self.STORE_NAME, len(results), q,
                            f" (fallback de '{query}')" if q != query else "",
                        )
                        return results
                except Exception as exc:
                    logger.warning(
                        "%s: Error buscando '%s' (intento %d): %s",
                        self.STORE_NAME, q, attempt, exc,
                    )
                    if attempt < self.MAX_RETRIES:
                        await asyncio.sleep(self.RETRY_DELAY)

        # Fallback a Playwright (si estuviese disponible)
        try:
            results = await self._search_playwright(query)
            if results:
                return results
        except Exception:
            pass

        logger.warning("%s: Sin resultados para '%s' (ni con términos alternativos)", self.STORE_NAME, query)
        return []

    # ── Métodos abstractos que cada scraper implementa ────────────
    @abc.abstractmethod
    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Búsqueda via HTTP API."""
        ...

    async def _search_playwright(self, query: str) -> list[dict[str, Any]]:
        """Fallback con Playwright opcional."""
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
