"""Scraper para jumbo.com.do — Centro Cuesta Nacional (Magento 2 GraphQL API)."""

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
    BASE_URL = "https://jumbo.com.do"
    _GRAPHQL_ENDPOINT = "https://jumbo.com.do/graphql"

    async def _search_api(self, query: str) -> list[dict[str, Any]]:
        """Busca productos mediante el endpoint GraphQL oficial de Jumbo (Magento 2)."""
        clean_query = query.replace('"', '\\"').strip()
        graphql_query = {
            "query": f"""
            {{
              products(search: "{clean_query}", pageSize: 30) {{
                items {{
                  name
                  sku
                  price_range {{
                    minimum_price {{
                      final_price {{
                        value
                        currency
                      }}
                      regular_price {{
                        value
                      }}
                    }}
                  }}
                  small_image {{
                    url
                  }}
                  url_key
                  stock_status
                  categories {{
                    name
                  }}
                }}
              }}
            }}
            """
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            headers = {
                **self._headers(),
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

            try:
                resp = await client.post(
                    self._GRAPHQL_ENDPOINT,
                    headers=headers,
                    json=graphql_query,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("data", {}).get("products", {}).get("items", [])
                    if items:
                        return self._parse_graphql_products(items)
            except Exception as e:
                logger.debug("Jumbo GraphQL falló para '%s': %s", query, e)

            # Fallback 2: Autocomplete o endpoint de búsqueda rápida de Magento
            try:
                resp = await client.get(
                    f"{self.BASE_URL}/search/ajax/suggest",
                    headers={**self._headers(), "X-Requested-With": "XMLHttpRequest"},
                    params={"q": query},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        return self._parse_suggest_products(data)
            except Exception as e:
                logger.debug("Jumbo suggest falló para '%s': %s", query, e)

        return []

    def _parse_graphql_products(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parsea la lista de items devueltos por GraphQL de Magento 2."""
        results: list[dict[str, Any]] = []
        for it in items:
            try:
                name = it.get("name", "").strip()
                if not name:
                    continue

                price_range = it.get("price_range", {})
                min_price = price_range.get("minimum_price", {})
                final_price = min_price.get("final_price", {})
                price = final_price.get("value", 0.0)

                if not price or float(price) <= 0:
                    regular_price = min_price.get("regular_price", {})
                    price = regular_price.get("value", 0.0)

                if not price or float(price) <= 0:
                    continue

                image_obj = it.get("small_image", {})
                image_url = image_obj.get("url", "") if isinstance(image_obj, dict) else ""

                url_key = it.get("url_key", "")
                product_url = f"{self.BASE_URL}/{url_key}.html" if url_key else self.BASE_URL

                categories = it.get("categories") or []
                category = " / ".join(
                    c.get("name", "") for c in categories if isinstance(c, dict) and c.get("name")
                )

                results.append({
                    "store": "jumbo",
                    "name": name,
                    "brand": "",
                    "price": float(price),
                    "unit": "",
                    "image_url": image_url,
                    "product_url": product_url,
                    "available": it.get("stock_status", "IN_STOCK") != "OUT_OF_STOCK",
                    "category": category,
                })
            except Exception as e:
                logger.debug("Error parseando producto Jumbo: %s", e)
                continue

        return results

    def _parse_suggest_products(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Parsea sugerencias del ajax suggest."""
        results: list[dict[str, Any]] = []
        for it in items[:10]:
            title = it.get("title", "").strip()
            if title:
                results.append({
                    "store": "jumbo",
                    "name": title,
                    "brand": "",
                    "price": 0.0,
                    "unit": "",
                    "image_url": "",
                    "product_url": f"{self.BASE_URL}/catalogsearch/result/?q={quote(title)}",
                    "available": True,
                })
        return results
