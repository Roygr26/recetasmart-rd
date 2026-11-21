"""Scraper para sirena.do — La Sirena supermercados de República Dominicana."""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from backend.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class SirenaScraper(BaseScraper):
    """Extrae productos de La Sirena (sirena.do)."""

    STORE_NAME = "sirena"
    BASE_URL = "https://www.sirena.do"

    # La Sirena suele usar un backend tipo Magento / algolia / custom API
    _SEARCH_ENDPOINTS = [
        "https://www.sirena.do/rest/V1/msp/searchautocomplete/get",
        "https://www.sirena.do/catalogsearch/result/?q={query}",
        "https://www.sirena.do/rest/V1/products",
    ]

    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Intenta múltiples endpoints de búsqueda de La Sirena."""
        encoded = quote(query)

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Opción 1: Autocomplete/search API
            try:
                resp = await client.get(
                    f"{self.BASE_URL}/rest/V1/msp/searchautocomplete/get",
                    headers={
                        **self._headers(),
                        "Accept": "application/json",
                    },
                    params={"queryText": query, "storeId": 1},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return self._parse_autocomplete(data)
                    if isinstance(data, dict):
                        items = data.get("items", data.get("products", data.get("data", [])))
                        if items:
                            return self._parse_autocomplete(items)
            except Exception as e:
                logger.debug("Sirena autocomplete falló: %s", e)

            # Opción 2: GraphQL endpoint (Magento 2 con GraphQL)
            try:
                graphql_query = {
                    "query": """
                    {
                      products(search: "%s", pageSize: 10) {
                        items {
                          name
                          sku
                          price_range {
                            minimum_price {
                              final_price { value currency }
                            }
                          }
                          image { url label }
                          url_key
                        }
                      }
                    }
                    """ % query.replace('"', '\\"')
                }
                resp = await client.post(
                    f"{self.BASE_URL}/graphql",
                    headers={
                        **self._headers(),
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Store": "default",
                    },
                    json=graphql_query,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", {}).get("products", {}).get("items", [])
                    if items:
                        return self._parse_graphql(items)
            except Exception as e:
                logger.debug("Sirena GraphQL falló: %s", e)

            # Opción 3: Buscar en la página HTML y extraer datos JSON embebidos
            try:
                resp = await client.get(
                    f"{self.BASE_URL}/catalogsearch/result/",
                    headers=self._headers(),
                    params={"q": query},
                )
                if resp.status_code == 200:
                    return self._parse_html_search(resp.text)
            except Exception as e:
                logger.debug("Sirena HTML search falló: %s", e)

        return []

    async def _search_playwright(self, query: str) -> list[dict[str, Any]]:
        """Fallback: renderiza la búsqueda con Playwright."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("Playwright no instalado")
            return []

        results: list[dict[str, Any]] = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=self._random_ua())
                url = f"{self.BASE_URL}/catalogsearch/result/?q={quote(query)}"
                await page.goto(url, wait_until="networkidle", timeout=20000)
                await page.wait_for_timeout(2000)

                # Selectores comunes de Magento/temas custom
                cards = await page.query_selector_all(
                    ".product-item, "
                    ".product-card, "
                    "[class*='product-item'], "
                    "li.item.product"
                )

                for card in cards[:10]:
                    try:
                        name_el = await card.query_selector(
                            ".product-item-link, "
                            ".product-name, "
                            "a.product-item-link, "
                            "h2 a, h3 a"
                        )
                        price_el = await card.query_selector(
                            ".price, "
                            "[data-price-type='finalPrice'], "
                            ".special-price .price, "
                            ".normal-price .price"
                        )
                        img_el = await card.query_selector("img.product-image-photo, img")
                        link_el = await card.query_selector("a.product-item-link, a")

                        name = await name_el.inner_text() if name_el else ""
                        price_text = await price_el.inner_text() if price_el else "0"
                        img_url = await img_el.get_attribute("src") if img_el else ""
                        prod_url = await link_el.get_attribute("href") if link_el else ""

                        if name and price_text:
                            results.append({
                                "store": "sirena",
                                "name": name.strip(),
                                "brand": "",
                                "price": self._clean_price(price_text),
                                "unit": "",
                                "image_url": img_url or "",
                                "product_url": prod_url or "",
                                "available": True,
                            })
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            logger.error("Sirena Playwright error: %s", e)

        return results

    # ── Parsers ──────────────────────────────────────────────────
    def _parse_autocomplete(self, items: list[dict]) -> list[dict[str, Any]]:
        """Parsea items del autocomplete/REST API."""
        results: list[dict[str, Any]] = []
        for item in items[:10]:
            try:
                name = item.get("name", item.get("title", item.get("product_name", "")))
                price = item.get("price", item.get("final_price", 0))
                image = item.get("image", item.get("thumbnail", item.get("image_url", "")))
                url = item.get("url", item.get("product_url", ""))

                if isinstance(price, str):
                    price = self._clean_price(price)

                if name and price:
                    results.append({
                        "store": "sirena",
                        "name": name,
                        "brand": item.get("brand", ""),
                        "price": float(price),
                        "unit": "",
                        "image_url": image if image and image.startswith("http") else f"{self.BASE_URL}{image}" if image else "",
                        "product_url": url if url and url.startswith("http") else f"{self.BASE_URL}{url}" if url else "",
                        "available": True,
                    })
            except Exception:
                continue
        return results

    def _parse_graphql(self, items: list[dict]) -> list[dict[str, Any]]:
        """Parsea items de la respuesta GraphQL de Magento 2."""
        results: list[dict[str, Any]] = []
        for item in items[:10]:
            try:
                name = item.get("name", "")
                price_range = item.get("price_range", {})
                min_price = price_range.get("minimum_price", {})
                final_price = min_price.get("final_price", {})
                price = final_price.get("value", 0)
                image_info = item.get("image", {})
                image_url = image_info.get("url", "") if isinstance(image_info, dict) else ""
                url_key = item.get("url_key", "")

                if name and price:
                    results.append({
                        "store": "sirena",
                        "name": name,
                        "brand": "",
                        "price": float(price),
                        "unit": "",
                        "image_url": image_url,
                        "product_url": f"{self.BASE_URL}/{url_key}.html" if url_key else "",
                        "available": True,
                    })
            except Exception:
                continue
        return results

    def _parse_html_search(self, html: str) -> list[dict[str, Any]]:
        """Extrae productos de HTML de resultados de búsqueda (Magento)."""
        results: list[dict[str, Any]] = []

        # Intentar encontrar JSON embebido en la página
        json_patterns = [
            r'var\s+productData\s*=\s*(\[.*?\]);',
            r'"items"\s*:\s*(\[.*?\])',
            r'data-product-data=\'(\[.*?\])\'',
        ]
        for pattern in json_patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    items = json.loads(match.group(1))
                    return self._parse_autocomplete(items)
                except (json.JSONDecodeError, IndexError):
                    continue

        # Fallback: regex para datos de producto en HTML
        # Buscar bloques de producto
        product_blocks = re.findall(
            r'<a[^>]*class="[^"]*product-item-link[^"]*"[^>]*href="([^"]*)"[^>]*>\s*(.*?)\s*</a>',
            html,
            re.DOTALL,
        )
        price_blocks = re.findall(
            r'data-price-amount="([^"]*)"',
            html,
        )

        for i, (url, name) in enumerate(product_blocks[:10]):
            price = float(price_blocks[i]) if i < len(price_blocks) else 0.0
            clean_name = re.sub(r'<[^>]+>', '', name).strip()
            if clean_name and price > 0:
                results.append({
                    "store": "sirena",
                    "name": clean_name,
                    "brand": "",
                    "price": price,
                    "unit": "",
                    "image_url": "",
                    "product_url": url,
                    "available": True,
                })

        return results
