"""Servicio de IA — usa Gemini 2.5 Flash con Structured Outputs."""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Cliente Gemini ────────────────────────────────────────────────
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


# ── 1. Extracción de ingredientes de una receta ──────────────────

_EXTRACT_SYSTEM = """Eres un chef experto dominicano y asistente de cocina inteligente.
Tu tarea es analizar el texto de una receta o lista de platos y extraer TODOS los ingredientes necesarios.

Reglas:
- Normaliza los nombres de ingredientes al español dominicano estándar.
- Incluye cantidades aproximadas si no se especifican.
- Para cada ingrediente, genera un término de búsqueda optimizado para buscar en un supermercado online dominicano (sin tildes, nombre genérico del producto).
- Si es una lista de platos, infiere los ingredientes típicos de cada plato.
- Agrupa ingredientes repetidos sumando cantidades.
- NO incluyas agua del grifo ni utensilios, solo ingredientes comprables."""

_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre del ingrediente"},
                    "quantity": {"type": "string", "description": "Cantidad con unidad"},
                    "search_term": {"type": "string", "description": "Término de búsqueda para supermercado"},
                },
                "required": ["name", "quantity", "search_term"],
            },
        }
    },
    "required": ["ingredients"],
}


async def extract_ingredients(recipe_text: str) -> list[dict[str, str]]:
    """Extrae ingredientes del texto de una receta usando Gemini."""
    client = _get_client()
    models_to_try = list(dict.fromkeys([settings.GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.1-flash-lite"]))
    last_err = None

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=f"Analiza esta receta y extrae los ingredientes:\n\n{recipe_text}",
                config=types.GenerateContentConfig(
                    system_instruction=_EXTRACT_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=_EXTRACT_SCHEMA,
                    temperature=0.2,
                ),
            )
            data = json.loads(response.text)
            return data.get("ingredients", [])
        except Exception as e:
            logger.warning("Error en extract_ingredients con %s: %s", model_name, e)
            last_err = e

    if last_err:
        raise last_err
    return []


# ── 2. Matching inteligente de productos ─────────────────────────

_MATCH_SYSTEM = """Eres un asistente de compras inteligente para supermercados en República Dominicana.

Tu tarea es analizar una lista de productos encontrados en un supermercado y seleccionar el MEJOR match para un ingrediente dado.

Reglas:
- Selecciona el producto que mejor corresponda al ingrediente buscado.
- Considera la cantidad necesaria vs. la unidad de venta.
- Si hay varias opciones, prefiere la que tenga mejor relación precio/cantidad.
- Ignora productos que claramente no son el ingrediente buscado.
- Si ningún producto es un buen match, responde con match_index = -1."""

_MATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "match_index": {
            "type": "integer",
            "description": "Índice (0-based) del mejor producto, o -1 si no hay match",
        },
        "confidence": {
            "type": "number",
            "description": "Confianza del match entre 0.0 y 1.0",
        },
        "reasoning": {
            "type": "string",
            "description": "Breve explicación del match (1 línea)",
        },
    },
    "required": ["match_index", "confidence", "reasoning"],
}


async def match_best_product(
    ingredient: dict[str, str],
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    """Usa Gemini para seleccionar el mejor producto que matchea un ingrediente."""
    if not products:
        return {"match_index": -1, "confidence": 0.0, "reasoning": "No hay productos disponibles"}

    # Preparar descripción de productos para Gemini
    products_desc = "\n".join(
        f"  [{i}] {p.get('name', '?')} — RD${p.get('price', 0):.2f} — {p.get('unit', 'N/A')}"
        for i, p in enumerate(products)
    )

    prompt = f"""Ingrediente buscado: {ingredient.get('name', '')} ({ingredient.get('quantity', '')})

Productos disponibles:
{products_desc}

Selecciona el mejor match."""

    client = _get_client()
    models_to_try = list(dict.fromkeys([settings.GEMINI_MODEL, "gemini-3.5-flash", "gemini-3.1-flash-lite"]))

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_MATCH_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=_MATCH_SCHEMA,
                    temperature=0.1,
                ),
            )

            result = json.loads(response.text)
            idx = result.get("match_index", -1)
            # Validar índice
            if idx < 0 or idx >= len(products):
                result["match_index"] = -1
            return result
        except Exception as e:
            logger.warning("Error en match_best_product con %s: %s", model_name, e)

    # Fallback: seleccionar el más barato si la IA no responde
    cheapest = min(range(len(products)), key=lambda i: products[i].get("price", float("inf")))
    return {"match_index": cheapest, "confidence": 0.5, "reasoning": "Fallback: producto más barato"}
