"""Scraper para jumbo.com.do — usa la API interna de VTEX como fuente primaria."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from backend.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class JumboScraper(BaseScraper):
    """Extrae productos de Jumbo República Dominicana (jumbo.com.do)."""

    STORE_NAME = "jumbo"
    BASE_URL = "https://www.jumbo.com.do"

    # Jumbo usa VTEX Commerce — endpoint de búsqueda inteligente
    _SEARCH_API = "https://www.jumbo.com.do/api/catalog_system/pub/products/search"
    _SEARCH_INTELLIGENT = "https://www.jumbo.com.do/api/io/_v/api/intelligent-search/product_search/v2/facets"

    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Busca mediante la API VTEX de Jumbo."""
        encoded = quote(query)

        # Intentar primero el endpoint de búsqueda de catálogo VTEX
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Opción 1: búsqueda de catálogo
            try:
                resp = await client.get(
                    f"{self._SEARCH_API}/{encoded}",
                    headers={
                        **self._headers(),
                        "Accept": "application/json",
                    },
                    params={"_from": 0, "_to": 9},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return self._parse_vtex_products(data)
            except Exception as e:
                logger.debug("Jumbo catalog_system falló: %s", e)

            # Opción 2: búsqueda por texto en la URL de búsqueda
            try:
                resp = await client.get(
                    f"{self.BASE_URL}/api/catalog_system/pub/products/search",
                    headers={
                        **self._headers(),
                        "Accept": "application/json",
                    },
                    params={"ft": query, "_from": 0, "_to": 9},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return self._parse_vtex_products(data)
            except Exception as e:
                logger.debug("Jumbo ft search falló: %s", e)

            # Opción 3: intelligent-search
            try:
                resp = await client.get(
                    f"{self.BASE_URL}/api/io/_v/api/intelligent-search/product_search/v2/facets",
                    headers={
                        **self._headers(),
                        "Accept": "application/json",
                    },
                    params={"query": query, "page": 1, "count": 10, "locale": "es-DO"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    products = data.get("products", data.get("data", []))
                    if products:
                        return self._parse_intelligent_search(products)
            except Exception as e:
                logger.debug("Jumbo intelligent-search falló: %s", e)

        return []

    async def _search_playwright(self, query: str) -> list[dict[str, Any]]:
        """Fallback: renderiza la página de búsqueda con Playwright."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright no instalado, no se puede usar fallback")
            return []

        results: list[dict[str, Any]] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=self._random_ua())
                url = f"{self.BASE_URL}/{quote(query)}?_q={quote(query)}&map=ft"
                await page.goto(url, wait_until="networkidle", timeout=20000)
                await page.wait_for_timeout(2000)

                # Buscar tarjetas de producto
                cards = await page.query_selector_all(
                    ".vtex-search-result-3-x-galleryItem, "
                    "[class*='productCard'], "
                    "[class*='product-card'], "
                    ".vtex-product-summary-2-x-container"
                )

                for card in cards[:10]:
                    try:
                        name_el = await card.query_selector(
                            "[class*='productBrand'], "
                            "[class*='nameContainer'], "
                            "h2, h3, .vtex-product-summary-2-x-productBrand"
                        )
                        price_el = await card.query_selector(
                            "[class*='sellingPrice'], "
                            "[class*='price'], "
                            ".vtex-product-price-1-x-sellingPriceValue"
                        )
                        img_el = await card.query_selector("img")
                        link_el = await card.query_selector("a")

                        name = await name_el.inner_text() if name_el else ""
                        price_text = await price_el.inner_text() if price_el else "0"
                        img_url = await img_el.get_attribute("src") if img_el else ""
                        prod_url = await link_el.get_attribute("href") if link_el else ""

                        if name and price_text:
                            results.append({
                                "store": "jumbo",
                                "name": name.strip(),
                                "brand": "",
                                "price": self._clean_price(price_text),
                                "unit": "",
                                "image_url": img_url or "",
                                "product_url": f"{self.BASE_URL}{prod_url}" if prod_url and not prod_url.startswith("http") else prod_url or "",
                                "available": True,
                            })
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            logger.error("Jumbo Playwright error: %s", e)

        return results

    # ── Parsers ──────────────────────────────────────────────────
    def _parse_vtex_products(self, products: list[dict]) -> list[dict[str, Any]]:
        """Parsea la respuesta del catalog_system de VTEX."""
        results: list[dict[str, Any]] = []
        for prod in products[:10]:
            try:
                name = prod.get("productName", prod.get("nameComplete", ""))
                brand = prod.get("brand", "")
                items = prod.get("items", [])
                price = 0.0
                unit = ""
                image_url = ""

                if items:
                    item = items[0]
                    # Imagen
                    images = item.get("images", [])
                    if images:
                        image_url = images[0].get("imageUrl", "")
                    # Precio
                    sellers = item.get("sellers", [])
                    if sellers:
                        comm_info = sellers[0].get("commertialOffer", {})
                        price = comm_info.get("Price", 0)
                        if not price:
                            price = comm_info.get("ListPrice", 0)
                    unit = item.get("measurementUnit", "")
                    if item.get("unitMultiplier"):
                        unit = f"{item['unitMultiplier']} {unit}"

                link = prod.get("link", prod.get("linkText", ""))
                product_url = f"{self.BASE_URL}/{link}" if link and not link.startswith("http") else link

                if name and price > 0:
                    results.append({
                        "store": "jumbo",
                        "name": name,
                        "brand": brand,
                        "price": float(price),
                        "unit": unit,
                        "image_url": image_url,
                        "product_url": product_url or "",
                        "available": True,
                    })
            except Exception:
                continue
        return results

    def _parse_intelligent_search(self, products: list[dict]) -> list[dict[str, Any]]:
        """Parsea respuesta de intelligent-search."""
        results: list[dict[str, Any]] = []
        for prod in products[:10]:
            try:
                name = prod.get("productName", prod.get("name", ""))
                brand = prod.get("brand", "")
                price = 0.0
                image_url = ""
                product_url = ""

                # Precio
                price_range = prod.get("priceRange", {})
                selling = price_range.get("sellingPrice", {})
                price = selling.get("lowPrice", 0)
                if not price:
                    price = selling.get("highPrice", 0)

                # Imágenes
                images = prod.get("images", [])
                if images:
                    image_url = images[0].get("imageUrl", images[0].get("url", ""))

                # URL
                link = prod.get("link", prod.get("linkText", ""))
                product_url = f"{self.BASE_URL}{link}" if link and not link.startswith("http") else link or ""

                if name and price > 0:
                    results.append({
                        "store": "jumbo",
                        "name": name,
                        "brand": brand,
                        "price": float(price),
                        "unit": "",
                        "image_url": image_url,
                        "product_url": product_url,
                        "available": True,
                    })
            except Exception:
                continue
        return results
