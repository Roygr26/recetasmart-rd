"""Servicio de IA — Gemini con Structured Outputs (extracción + verificación de matches)."""

from __future__ import annotations

import json
import logging
import re
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

_EXTRACT_SYSTEM = """Eres un chef experto dominicano y un comprador experto de supermercados en República Dominicana (Jumbo y La Sirena).
Tu tarea es analizar el texto de una receta o lista de platos y extraer TODOS los ingredientes comprables, con datos PRECISOS para encontrar el producto EXACTO en el supermercado.

Reglas generales:
- Normaliza los nombres al español dominicano estándar (habichuelas, ají, guineo, plátano, auyama, cilantro/cilantrico, sazón, etc.).
- Si la cantidad no se especifica, estima una cantidad razonable para la receta.
- Si es una lista de platos, infiere los ingredientes típicos de cada plato.
- Agrupa ingredientes repetidos sumando cantidades.
- NO incluyas agua del grifo ni utensilios.

Para cada ingrediente genera:
- search_term: el nombre del producto tal como aparece en el supermercado, SIN tildes, 1 a 3 palabras, en singular. Debe describir la forma correcta del producto
  (ej: "platano verde", "cebolla roja", "aceite de soya", "pasta de tomate", "carne molida res", "pechuga pollo", "habichuelas rojas", "ajo").
- alt_terms: 1 o 2 términos alternativos o sinónimos usados en tiendas dominicanas (ej: para "cilantro" -> ["cilantrico", "culantro"]; para "carne molida de res" -> ["molida de res"]). Lista vacía si no hay.
- required_words: las palabras (sin tildes) que OBLIGATORIAMENTE deben aparecer en el nombre del producto correcto. Normalmente el sustantivo principal y, si importa, su tipo
  (ej: "platano verde" -> ["platano", "verde"]; "aceite de oliva" -> ["aceite", "oliva"]; "carne molida de res" -> ["molida", "res"]; "pollo entero" -> ["pollo"]; "sal" -> ["sal"]).
  No pongas marcas ni palabras opcionales.
- excluded_words: palabras que indican un producto EQUIVOCADO para este ingrediente (formas procesadas, derivados, otros usos).
  Ejemplos: "platano verde" -> ["chips", "tostones", "maduro", "harina"]; "ajo" (fresco) -> ["polvo", "pasta", "sal", "galleta", "vinagre"]; "sal" -> ["ajo", "citrica", "papitas", "platanitos", "galleta"];
  "aceite" de cocinar -> ["esencial", "corporal", "capilar", "sardina", "spray"]; "pollo entero" -> ["muslo", "pechuga", "alas", "sandwich", "ensalada", "rostizado", "nuggets"];
  "arroz" -> ["integral", "harina", "leche", "galleta", "cereal"]; "espaguetis" -> ["salsa", "albondigas", "integral", "gluten"]; "huevos" -> ["clara", "pasteurizada", "codorniz"]; "carne molida de res" -> ["pollo", "cerdo", "pavo"].
- category: la categoría del producto en el supermercado (una del enum).
- quantity_grams: la cantidad necesaria convertida aproximadamente a gramos (o ml para líquidos). 1 lb = 454 g, 1 taza ≈ 240 g, 1 cda ≈ 15 g, 1 cdta ≈ 5 g, 1 unidad de cebolla ≈ 200 g, 1 diente de ajo ≈ 5 g.

Reglas de exactitud:
- Usa la forma fresca/natural salvo que la receta pida otra cosa explícitamente ("ajo en polvo", "habichuelas enlatadas", "maíz en lata").
- Distingue variedades: "plátano verde" ≠ "plátano maduro", "cebolla roja" ≠ "cebolla blanca", "aceite de oliva" ≠ "aceite de soya", "leche evaporada" ≠ "leche entera".
- Nombres usados en las tiendas dominicanas: el pimiento morrón se vende como "aji morron" (search_term "aji morron", required_words ["aji", "morron"]);
  el ají cubanela como "aji cubanela"; el cilantro también como "cilantrico"; los frijoles como "habichuelas".
- "aceite" sin especificar = aceite vegetal de cocinar (soya/canola). "sal" = sal de mesa. "sazón" = sazón líquido o en polvo de cocina."""

_CATEGORIES = [
    "frutas_vegetales", "carnes", "pescados_mariscos", "lacteos_huevos",
    "granos_arroz_pastas", "condimentos_especias", "aceites", "enlatados",
    "panaderia", "bebidas", "congelados", "dulces_reposteria", "otros",
]

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
                    "search_term": {"type": "string", "description": "Término de búsqueda exacto para supermercado, sin tildes"},
                    "alt_terms": {"type": "array", "items": {"type": "string"}, "description": "Términos alternativos"},
                    "required_words": {"type": "array", "items": {"type": "string"}, "description": "Palabras obligatorias en el nombre del producto"},
                    "excluded_words": {"type": "array", "items": {"type": "string"}, "description": "Palabras que indican un producto equivocado"},
                    "category": {"type": "string", "enum": _CATEGORIES},
                    "quantity_grams": {"type": "number", "description": "Cantidad necesaria aproximada en gramos o ml"},
                },
                "required": [
                    "name", "quantity", "search_term", "alt_terms",
                    "required_words", "excluded_words", "category", "quantity_grams",
                ],
            },
        }
    },
    "required": ["ingredients"],
}


def _models_to_try() -> list[str]:
    return list(dict.fromkeys([
        settings.GEMINI_MODEL,
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]))


async def extract_ingredients(recipe_text: str) -> list[dict[str, Any]]:
    """Extrae ingredientes del texto de una receta usando Gemini."""
    client = _get_client()
    last_err = None

    for model_name in _models_to_try():
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=f"Analiza esta receta y extrae los ingredientes:\n\n{recipe_text}",
                config=types.GenerateContentConfig(
                    system_instruction=_EXTRACT_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=_EXTRACT_SCHEMA,
                    temperature=0.1,
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




# ── 2. Verificación final de matches (UNA sola llamada por receta) ──

_VERIFY_SYSTEM = """Eres un comprador experto de supermercados en República Dominicana.
Para cada ingrediente de una receta recibes hasta 3 productos candidatos de una tienda (ya prefiltrados).
Debes elegir el candidato que sea EXACTAMENTE el ingrediente pedido, en la forma correcta para cocinar la receta.

Reglas:
- El producto debe ser el mismo alimento y la misma variedad/forma (fresco vs. polvo, verde vs. maduro, res vs. pollo, oliva vs. soya...).
- Si varios son correctos, elige el de presentación más adecuada para la cantidad necesaria.
- Si NINGUNO es el ingrediente correcto, responde choice = -1. Es preferible -1 a un producto equivocado."""

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Identificador recibido (ej: '3-jumbo')"},
                    "choice": {"type": "integer", "description": "Índice del candidato correcto (0-2) o -1"},
                },
                "required": ["id", "choice"],
            },
        }
    },
    "required": ["decisions"],
}


async def verify_matches(tasks: list[dict[str, Any]]) -> dict[str, int] | None:
    """Verifica en lote los candidatos de todos los ingredientes con una sola llamada a Gemini.

    `tasks`: [{"id": "0-jumbo", "ingredient": "...", "quantity": "...", "candidates": ["...", ...]}]
    Devuelve {id: indice_elegido | -1} o None si la IA no está disponible (se usa el matching determinista).
    """
    if not tasks or not settings.AI_VERIFY_MATCHES or not settings.GEMINI_API_KEY:
        return None

    lines = []
    for t in tasks:
        cands = "\n".join(f"    [{i}] {c}" for i, c in enumerate(t["candidates"]))
        lines.append(f"- id={t['id']} | ingrediente: {t['ingredient']} ({t['quantity']})\n{cands}")
    prompt = "Elige el candidato correcto para cada ingrediente:\n\n" + "\n".join(lines)

    client = _get_client()
    for model_name in _models_to_try():
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_VERIFY_SYSTEM,
                    response_mime_type="application/json",
                    response_schema=_VERIFY_SCHEMA,
                    temperature=0.0,
                ),
            )
            data = json.loads(response.text)
            result: dict[str, int] = {}
            for d in data.get("decisions", []):
                try:
                    result[str(d["id"])] = int(d["choice"])
                except (KeyError, TypeError, ValueError):
                    continue
            return result
        except Exception as e:
            logger.warning("Error en verify_matches con %s: %s", model_name, e)

    return None
