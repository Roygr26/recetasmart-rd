"""Comparator — Orquesta el flujo completo: caché → scraping → matching → verificación → cálculo."""

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
from backend.services.ai_service import extract_ingredients, verify_matches
from backend.services.matcher import fold, match_best_product

logger = logging.getLogger(__name__)

_jumbo = JumboScraper()
_sirena = SirenaScraper()

# Versión del formato de caché: al cambiar el formato de los productos se invalida lo anterior
_CACHE_VERSION = "v2"

# Máximo de búsquedas simultáneas por tienda (evita bloqueos/rate limit de las tiendas)
_STORE_CONCURRENCY = 6


async def _search_with_cache(store: str, query: str, sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    """Busca productos, usando caché primero."""
    q = fold(query).strip()
    cache_key = f"{_CACHE_VERSION}:{q}"
    cached = await get_cached(store, cache_key)
    if cached is not None:
        logger.info("Cache HIT para %s:'%s' (%d resultados)", store, q, len(cached))
        return cached

    scraper = _jumbo if store == "jumbo" else _sirena
    async with sem:
        results = await scraper.search(q)

    if results:
        await set_cache(store, cache_key, results)
    return results


async def _search_store(store: str, terms: list[str], sem: asyncio.Semaphore) -> list[dict[str, Any]]:
    """Busca todos los términos de un ingrediente en una tienda y une los resultados sin duplicados."""
    results_per_term = await asyncio.gather(
        *(_search_with_cache(store, t, sem) for t in terms), return_exceptions=True
    )
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for res in results_per_term:
        if isinstance(res, BaseException):
            logger.warning("Error buscando en %s: %s", store, res)
            continue
        for p in res:
            key = fold(p.get("name", "")).strip()
            if key and key not in seen:
                seen.add(key)
                merged.append(p)
    return merged


def _search_terms(ing: Ingredient) -> list[str]:
    """Término principal + alternativos + nombre (máx. 3, sin repetidos)."""
    terms: list[str] = []
    for t in [ing.search_term, *ing.alt_terms[:2], ing.name]:
        t = (t or "").strip()
        if t and fold(t) not in {fold(x) for x in terms}:
            terms.append(t)
    return terms[:3]


def _to_option(store: str, p: dict[str, Any]) -> ProductOption:
    return ProductOption(
        store=store,
        name=p.get("name", ""),
        brand=p.get("brand", ""),
        price=p.get("price", 0),
        unit=p.get("unit", ""),
        image_url=p.get("image_url", ""),
        product_url=p.get("product_url", ""),
        available=p.get("available", True),
        category=p.get("category", ""),
    )


def _build_ingredient(raw: dict[str, Any]) -> Ingredient | None:
    def str_list(v: Any) -> list[str]:
        return [t for t in (v or []) if isinstance(t, str) and t.strip()]

    try:
        return Ingredient(
            name=raw.get("name", ""),
            quantity=raw.get("quantity", ""),
            search_term=raw.get("search_term") or raw.get("name", ""),
            alt_terms=str_list(raw.get("alt_terms")),
            required_words=str_list(raw.get("required_words")),
            excluded_words=str_list(raw.get("excluded_words")),
            category=raw.get("category") or "",
            quantity_grams=max(0.0, float(raw.get("quantity_grams") or 0)),
        )
    except Exception as e:
        logger.warning("Ingrediente inválido %s: %s", raw, e)
        return None


async def find_matches(
    ingredients: list[Ingredient],
) -> tuple[list[tuple[list[dict], list[dict]]], list[dict[str, dict[str, Any]]]]:
    """Busca y selecciona el producto exacto de cada ingrediente en ambas tiendas."""
    sems = {
        "jumbo": asyncio.Semaphore(_STORE_CONCURRENCY),
        "sirena": asyncio.Semaphore(_STORE_CONCURRENCY),
    }

    async def search_both(ing: Ingredient) -> tuple[list[dict], list[dict]]:
        terms = _search_terms(ing)
        j, s = await asyncio.gather(
            _search_store("jumbo", terms, sems["jumbo"]),
            _search_store("sirena", terms, sems["sirena"]),
        )
        return j, s

    # ── Búsqueda en paralelo ──
    all_results = list(await asyncio.gather(*(search_both(ing) for ing in ingredients)))

    # ── Matching estricto determinista ──
    matches: list[dict[str, dict[str, Any]]] = []
    for ing, (jumbo_prods, sirena_prods) in zip(ingredients, all_results):
        ing_d = ing.model_dump()
        matches.append({
            "jumbo": match_best_product(ing_d, jumbo_prods),
            "sirena": match_best_product(ing_d, sirena_prods),
        })

    # ── Verificación final con IA (UNA sola llamada para toda la receta) ──
    verify_tasks: list[dict[str, Any]] = []
    tops: dict[str, list[int]] = {}
    for i, (ing, (jumbo_prods, sirena_prods)) in enumerate(zip(ingredients, all_results)):
        for store, prods in (("jumbo", jumbo_prods), ("sirena", sirena_prods)):
            ranked = matches[i][store].get("ranked") or []
            if not ranked:
                continue
            top = [idx for idx, _ in ranked[:3]]
            task_id = f"{i}-{store}"
            tops[task_id] = top
            verify_tasks.append({
                "id": task_id,
                "ingredient": ing.name,
                "quantity": ing.quantity,
                "candidates": [prods[idx].get("name", "") for idx in top],
            })

    decisions = await verify_matches(verify_tasks)
    if decisions is not None:
        for task_id, top in tops.items():
            choice = decisions.get(task_id)
            if choice is None:
                continue  # sin decisión → se mantiene el matching determinista
            i, store = task_id.split("-", 1)
            m = matches[int(i)][store]
            if choice == -1:
                m["match_index"] = -1
                m["confidence"] = 0.0
                m["reasoning"] = "La verificación IA descartó los candidatos"
            elif 0 <= choice < len(top):
                m["match_index"] = top[choice]
                m["confidence"] = max(m.get("confidence", 0.0), 0.95)
                m["reasoning"] = "Verificado por IA"

    return all_results, matches


async def compare_recipe(recipe_text: str, sucursal: str = "La Vega") -> ComparisonResult:
    """
    Flujo completo:
    1. Extraer ingredientes con IA (con palabras obligatorias/excluidas, categoría y gramos)
    2. Buscar cada ingrediente en ambas tiendas (término principal + alternativos, en paralelo)
    3. Matching estricto (descarta no-alimentos, agotados y formas equivocadas)
    4. Verificación final con IA en una sola llamada (con fallback determinista)
    5. Calcular totales y recomendar
    """
    # ── 1. Extraer ingredientes ───────────────────────────────────
    logger.info("Extrayendo ingredientes de la receta...")
    raw_ingredients = await extract_ingredients(recipe_text)

    if not raw_ingredients:
        return ComparisonResult(
            recommendation="No se pudieron extraer ingredientes de la receta proporcionada."
        )

    ingredients = [
        ing for ing in (_build_ingredient(r) for r in raw_ingredients if r.get("name")) if ing
    ]
    logger.info("Ingredientes extraídos: %d", len(ingredients))

    # ── 2-4. Búsqueda + matching + verificación ───────────────────
    all_results, matches = await find_matches(ingredients)

    comparisons: list[IngredientComparison] = []

    for i, (ing, (jumbo_prods, sirena_prods)) in enumerate(zip(ingredients, all_results)):
        jm, sm = matches[i]["jumbo"], matches[i]["sirena"]

        jumbo_idx = jm.get("match_index", -1)
        best_jumbo = _to_option("jumbo", jumbo_prods[jumbo_idx]) if 0 <= jumbo_idx < len(jumbo_prods) else None

        sirena_idx = sm.get("match_index", -1)
        best_sirena = _to_option("sirena", sirena_prods[sirena_idx]) if 0 <= sirena_idx < len(sirena_prods) else None

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

        # Opciones mostradas: solo candidatos válidos, en orden de relevancia
        jumbo_options = [_to_option("jumbo", jumbo_prods[idx]) for idx, _ in (jm.get("ranked") or [])[:5]]
        sirena_options = [_to_option("sirena", sirena_prods[idx]) for idx, _ in (sm.get("ranked") or [])[:5]]

        comparisons.append(
            IngredientComparison(
                ingredient=ing,
                jumbo_options=jumbo_options,
                sirena_options=sirena_options,
                best_jumbo=best_jumbo,
                best_sirena=best_sirena,
                cheapest_store=cheapest,
                jumbo_confidence=jm.get("confidence", 0.0) if best_jumbo else 0.0,
                sirena_confidence=sm.get("confidence", 0.0) if best_sirena else 0.0,
            )
        )

    # ── 5. Calcular totales ───────────────────────────────────────
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

    # Para que la comparación sea justa, la recomendación se basa SOLO en los
    # ingredientes encontrados en ambas tiendas (si no, la tienda que encontró
    # menos productos parecería más barata).
    common = [c for c in comparisons if c.best_jumbo and c.best_sirena]
    cmp_jumbo = sum(c.best_jumbo.price for c in common)
    cmp_sirena = sum(c.best_sirena.price for c in common)
    cmp_mixed = sum(min(c.best_jumbo.price, c.best_sirena.price) for c in common)
    missing_jumbo = sum(1 for c in comparisons if not c.best_jumbo)
    missing_sirena = sum(1 for c in comparisons if not c.best_sirena)

    notes = []
    if missing_jumbo:
        notes.append(f"{missing_jumbo} producto{'s' if missing_jumbo != 1 else ''} no encontrado{'s' if missing_jumbo != 1 else ''} en Jumbo")
    if missing_sirena:
        notes.append(f"{missing_sirena} producto{'s' if missing_sirena != 1 else ''} no encontrado{'s' if missing_sirena != 1 else ''} en La Sirena")
    scope = " (comparando solo los productos disponibles en ambas tiendas)" if notes else ""
    note_text = f" ⚠️ {'; '.join(notes)}." if notes else ""

    # Generar recomendación
    if total_jumbo == 0 and total_sirena == 0:
        recommendation = "No se encontraron productos en ninguna tienda para esta receta."
    elif not common:
        only = "Jumbo" if total_jumbo > 0 and total_sirena == 0 else "La Sirena" if total_sirena > 0 and total_jumbo == 0 else ""
        recommendation = (
            f"No hay productos comunes para comparar entre ambas tiendas.{note_text}"
            + (f" Solo {only} tiene productos para esta receta." if only else "")
        )
    elif cmp_mixed < cmp_jumbo and cmp_mixed < cmp_sirena:
        recommendation = (
            f"🧠 ¡Compra Mixta Inteligente! Ahorras RD${cmp_jumbo - cmp_mixed:,.2f} vs. Jumbo "
            f"y RD${cmp_sirena - cmp_mixed:,.2f} vs. La Sirena comprando en ambas tiendas{scope}.{note_text}"
        )
    elif cmp_jumbo <= cmp_sirena:
        recommendation = (
            f"🟢 Jumbo es más económico para esta receta{scope}. "
            f"Ahorras RD${cmp_sirena - cmp_jumbo:,.2f} vs. La Sirena.{note_text}"
        )
    else:
        recommendation = (
            f"🔵 La Sirena es más económica para esta receta{scope}. "
            f"Ahorras RD${cmp_jumbo - cmp_sirena:,.2f} vs. Jumbo.{note_text}"
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
