"""Scraper para sirena.do — Grupo Ramos (VTEX Commerce API)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from backend.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class SirenaScraper(BaseScraper):
    """Extrae productos de La Sirena República Dominicana (sirena.do)."""

    STORE_NAME = "sirena"
    BASE_URL = "https://www.sirena.do"
    _VTEX_ENDPOINTS = [
        "https://www.sirena.do/api/catalog_system/pub/products/search",
        "https://gruporamos.vtexcommercestable.com.br/api/catalog_system/pub/products/search",
    ]

    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Busca productos mediante el catálogo VTEX oficial de La Sirena."""
        encoded = quote(query)

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            headers = {
                **self._headers(),
                "Accept": "application/json",
            }

            for base_endpoint in self._VTEX_ENDPOINTS:
                try:
                    resp = await client.get(
                        f"{base_endpoint}/{encoded}",
                        headers=headers,
                        params={"_from": 0, "_to": 39},
                    )
                    # VTEX responde 200 o 206 (Partial Content)
                    if resp.status_code in (200, 206):
                        data = resp.json()
                        if isinstance(data, list) and data:
                            results = self._parse_vtex_products(data)
                            if results:
                                return results
                except Exception as e:
                    logger.debug("Sirena endpoint %s falló para '%s': %s", base_endpoint, query, e)

            # Fallback: búsqueda por parámetro ft
            try:
                resp = await client.get(
                    self._VTEX_ENDPOINTS[0],
                    headers=headers,
                    params={"ft": query, "_from": 0, "_to": 39},
                )
                if resp.status_code in (200, 206):
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return self._parse_vtex_products(data)
            except Exception as e:
                logger.debug("Sirena ft search falló para '%s': %s", query, e)

        return []

    def _parse_vtex_products(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parsea la respuesta del catalog_system de VTEX de Grupo Ramos / La Sirena."""
        results: list[dict[str, Any]] = []
        for prod in products[:40]:
            try:
                name = prod.get("productName", prod.get("nameComplete", "")).strip()
                if not name:
                    continue

                brand = prod.get("brand", "")
                items = prod.get("items", [])
                price = 0.0
                unit = ""
                image_url = ""
                available = True

                if items:
                    item = items[0]
                    images = item.get("images", [])
                    if images:
                        image_url = images[0].get("imageUrl", "")

                    sellers = item.get("sellers", [])
                    if sellers:
                        comm_info = sellers[0].get("commertialOffer", {})
                        price = comm_info.get("Price", 0.0)
                        available = bool(comm_info.get("IsAvailable", True)) and (
                            comm_info.get("AvailableQuantity", 1) or 0
                        ) > 0
                        if not price or price <= 0:
                            price = comm_info.get("ListPrice", 0.0)

                    unit = item.get("measurementUnit", "")
                    if item.get("unitMultiplier"):
                        unit = f"{item['unitMultiplier']} {unit}".strip()

                categories = prod.get("categories") or []
                category = categories[0] if categories else ""

                link = prod.get("link", prod.get("linkText", ""))
                product_url = f"{self.BASE_URL}/{link}" if link and not link.startswith("http") else link or self.BASE_URL

                if name and price > 0:
                    results.append({
                        "store": "sirena",
                        "name": name,
                        "brand": brand,
                        "price": float(price),
                        "unit": unit,
                        "image_url": image_url,
                        "product_url": product_url,
                        "available": available,
                        "category": category,
                    })
            except Exception as e:
                logger.debug("Error parseando producto Sirena: %s", e)
                continue

        return results
