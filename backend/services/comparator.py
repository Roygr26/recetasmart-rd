"""Comparator — Orquesta el flujo completo: caché → scraping → matching → cálculo."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.database import get_cached, set_cache
from backend.models import (
    ComparisonResult,
    Ingredient,
    IngredientComparison,
    ProductOption,
)
from backend.scrapers.jumbo import JumboScraper
from backend.scrapers.sirena import SirenaScraper
from backend.services.ai_service import extract_ingredients, match_best_product

logger = logging.getLogger(__name__)

_jumbo = JumboScraper()
_sirena = SirenaScraper()


async def _search_with_cache(store: str, query: str) -> list[dict[str, Any]]:
    """Busca productos, usando caché primero."""
    # 1. Intentar caché
    cached = await get_cached(store, query)
    if cached is not None:
        logger.info("Cache HIT para %s:'%s' (%d resultados)", store, query, len(cached))
        return cached

    # 2. Scraping
    scraper = _jumbo if store == "jumbo" else _sirena
    results = await scraper.search(query)

    # 3. Guardar en caché
    if results:
        await set_cache(store, query, results)

    return results


async def _search_both(query: str) -> tuple[list[dict], list[dict]]:
    """Busca un término en ambas tiendas en paralelo."""
    jumbo_results, sirena_results = await asyncio.gather(
        _search_with_cache("jumbo", query),
        _search_with_cache("sirena", query),
    )
    return jumbo_results, sirena_results


async def compare_recipe(recipe_text: str, sucursal: str = "La Vega") -> ComparisonResult:
    """
    Flujo completo:
    1. Extraer ingredientes con IA
    2. Buscar cada ingrediente en ambas tiendas (en paralelo)
    3. Matching inteligente por ingrediente
    4. Calcular totales y recomendar
    """
    # ── 1. Extraer ingredientes ───────────────────────────────────
    logger.info("Extrayendo ingredientes de la receta...")
    raw_ingredients = await extract_ingredients(recipe_text)

    if not raw_ingredients:
        return ComparisonResult(
            recommendation="No se pudieron extraer ingredientes de la receta proporcionada."
        )

    ingredients = [
        Ingredient(
            name=ing.get("name", ""),
            quantity=ing.get("quantity", ""),
            search_term=ing.get("search_term", ing.get("name", "")),
        )
        for ing in raw_ingredients
        if ing.get("name")
    ]

    logger.info("Ingredientes extraídos: %d", len(ingredients))

    # ── 2. Buscar en ambas tiendas en paralelo ────────────────────
    search_tasks = [
        _search_both(ing.search_term) for ing in ingredients
    ]
    all_results = await asyncio.gather(*search_tasks)

    # ── 3. Matching inteligente por ingrediente ───────────────────
    comparisons: list[IngredientComparison] = []

    for ing, (jumbo_prods, sirena_prods) in zip(ingredients, all_results):
        # Matching para Jumbo
        jumbo_match = await match_best_product(
            {"name": ing.name, "quantity": ing.quantity},
            jumbo_prods,
        )
        # Matching para Sirena
        sirena_match = await match_best_product(
            {"name": ing.name, "quantity": ing.quantity},
            sirena_prods,
        )

        # Construir opciones de producto
        best_jumbo = None
        best_sirena = None

        jumbo_idx = jumbo_match.get("match_index", -1)
        if 0 <= jumbo_idx < len(jumbo_prods):
            p = jumbo_prods[jumbo_idx]
            best_jumbo = ProductOption(
                store="jumbo",
                name=p.get("name", ""),
                brand=p.get("brand", ""),
                price=p.get("price", 0),
                unit=p.get("unit", ""),
                image_url=p.get("image_url", ""),
                product_url=p.get("product_url", ""),
                available=p.get("available", True),
            )

        sirena_idx = sirena_match.get("match_index", -1)
        if 0 <= sirena_idx < len(sirena_prods):
            p = sirena_prods[sirena_idx]
            best_sirena = ProductOption(
                store="sirena",
                name=p.get("name", ""),
                brand=p.get("brand", ""),
                price=p.get("price", 0),
                unit=p.get("unit", ""),
                image_url=p.get("image_url", ""),
                product_url=p.get("product_url", ""),
                available=p.get("available", True),
            )

        # Determinar tienda más barata
        cheapest = ""
        if best_jumbo and best_sirena:
            if best_jumbo.price < best_sirena.price:
                cheapest = "jumbo"
            elif best_sirena.price < best_jumbo.price:
                cheapest = "sirena"
            else:
                cheapest = "empate"
        elif best_jumbo:
            cheapest = "jumbo"
        elif best_sirena:
            cheapest = "sirena"

        jumbo_options = [
            ProductOption(**p) for p in jumbo_prods[:5]
        ] if jumbo_prods else []

        sirena_options = [
            ProductOption(**{**p, "store": "sirena"}) for p in sirena_prods[:5]
        ] if sirena_prods else []

        comparisons.append(
            IngredientComparison(
                ingredient=ing,
                jumbo_options=jumbo_options,
                sirena_options=sirena_options,
                best_jumbo=best_jumbo,
                best_sirena=best_sirena,
                cheapest_store=cheapest,
            )
        )

    # ── 4. Calcular totales ───────────────────────────────────────
    total_jumbo = sum(
        c.best_jumbo.price for c in comparisons if c.best_jumbo
    )
    total_sirena = sum(
        c.best_sirena.price for c in comparisons if c.best_sirena
    )
    total_mixed = sum(
        min(
            c.best_jumbo.price if c.best_jumbo else float("inf"),
            c.best_sirena.price if c.best_sirena else float("inf"),
        )
        for c in comparisons
        if c.best_jumbo or c.best_sirena
    )

    # Reemplazar inf con 0 si no había ninguna opción
    if total_mixed == float("inf"):
        total_mixed = 0.0

    savings_vs_jumbo = total_jumbo - total_mixed if total_jumbo > 0 else 0
    savings_vs_sirena = total_sirena - total_mixed if total_sirena > 0 else 0

    # Generar recomendación
    if total_jumbo == 0 and total_sirena == 0:
        recommendation = "No se encontraron productos en ninguna tienda para esta receta."
    elif total_mixed < total_jumbo and total_mixed < total_sirena:
        recommendation = (
            f"🧠 ¡Compra Mixta Inteligente! Ahorras RD${savings_vs_jumbo:,.2f} vs. Jumbo "
            f"y RD${savings_vs_sirena:,.2f} vs. La Sirena comprando en ambas tiendas."
        )
    elif total_jumbo <= total_sirena:
        recommendation = (
            f"🟢 Jumbo es más económico para esta receta. "
            f"Ahorras RD${total_sirena - total_jumbo:,.2f} vs. La Sirena."
        )
    else:
        recommendation = (
            f"🔵 La Sirena es más económica para esta receta. "
            f"Ahorras RD${total_jumbo - total_sirena:,.2f} vs. Jumbo."
        )

    return ComparisonResult(
        ingredients=comparisons,
        total_jumbo=round(total_jumbo, 2),
        total_sirena=round(total_sirena, 2),
        total_mixed=round(total_mixed, 2),
        savings_vs_jumbo=round(savings_vs_jumbo, 2),
        savings_vs_sirena=round(savings_vs_sirena, 2),
        recommendation=recommendation,
    )
