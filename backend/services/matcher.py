"""Matching determinista de productos: normalización, filtros duros y puntuación.

El objetivo es que el producto elegido sea EXACTAMENTE el ingrediente pedido:
- Se descartan productos no alimenticios (cosméticos, mascotas, hogar...).
- Se descartan productos agotados.
- Todas las palabras clave del ingrediente deben aparecer en el nombre.
- Se descartan formas incorrectas (chips, polvo, salsa...) salvo que el ingrediente las pida.
- Entre los válidos, se prefiere el que mejor coincide y cuyo tamaño cubre la cantidad necesaria.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

# ── Normalización de texto ───────────────────────────────────────

_STOP_WORDS = {
    "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "con", "sin",
    "en", "para", "al", "a", "o", "u", "y", "e", "por", "tipo", "x",
}


def fold(text: str) -> str:
    """Minúsculas y sin tildes: 'Plátano' -> 'platano'."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def stem(word: str) -> str:
    """Singularización simple en español (suficiente para nombres de productos).

    tomates -> tomate, limones -> limon, frijoles -> frijol, ajies -> aji, cebollas -> cebolla.
    """
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-2]                # ajies -> aji
    if len(w) > 4 and w.endswith("es") and w[-3] in "lnrdjy":
        return w[:-2]                # limones -> limon, frijoles -> frijol
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]                # tomates -> tomate, cebollas -> cebolla
    return w


# Variantes ortográficas / sinónimos usados por las tiendas → forma canónica
_SYNONYMS = {
    "spaghetti": "espagueti", "spaguetti": "espagueti", "espaguetti": "espagueti",
    "spagetti": "espagueti", "espaghetti": "espagueti",
    "fetuccini": "fettuccine", "fettuccini": "fettuccine", "fetuccine": "fettuccine",
    "culantro": "cilantro", "cilantrico": "cilantro", "cilantrillo": "cilantro",
    "pimiento": "aji", "morrone": "morron",   # en RD el pimiento morrón se vende como "Ají Morrón"
    "frijol": "habichuela", "frijole": "habichuela", "poroto": "habichuela",
    "yogurt": "yogur", "ketchup": "ketchup", "catsup": "ketchup",
    "guandule": "guandul", "gandul": "guandul", "gandule": "guandul",
    "batata": "batata", "camote": "batata", "zanahoria": "zanahoria",
    "nuece": "nuez", "maice": "maiz", "aceitune": "aceituna",
    "lt": "l", "litro": "l", "onza": "oz", "onz": "oz", "gr": "g", "gramo": "g", "libra": "lb",
}

# Tokens de tamaño/unidad: no cuentan como "palabras extra" del producto
_UNIT_TOKENS = {"lb", "oz", "g", "kg", "ml", "l", "gl", "und", "un", "unidad", "paq", "paquete",
                "aprox", "peso", "pack", "no", "uds", "ud", "doypack", "lata", "botella", "funda"}

# Variedades que cambian el producto: penalizadas si el ingrediente no las pide
_VARIANT_WORDS = {stem(w) for w in (
    "integral", "light", "diet", "organico", "organica", "gluten", "descremada", "descremado",
    "deslactosada", "deslactosado", "saborizada", "saborizado", "sabor", "proteina", "quinoa",
    "picante", "ahumado", "ahumada", "albondiga", "albondigas", "precocida", "precocido",
    "instantaneo", "instantanea", "parboiled", "keto", "vegana", "vegano", "bio", "integrale", "pelado", "pelada",
)}


def tokens(text: str) -> list[str]:
    """Tokens normalizados (sin tildes, singularizados, sinónimos unificados, sin stop words)."""
    raw = re.findall(r"[a-z0-9ñ]+", fold(text))
    out = []
    for t in raw:
        if t in _STOP_WORDS or t.isdigit():
            continue
        s = stem(t)
        out.append(_SYNONYMS.get(s, _SYNONYMS.get(t, s)))
    return out


def _has_word(word: str, name_tokens: set[str]) -> bool:
    """Coincidencia EXACTA del token normalizado.

    No se usa coincidencia aproximada a propósito: 'pimiento' ~ 'pimienta' o
    'naranja' ~ 'naranjada' son productos distintos. Las variantes ortográficas
    reales se cubren con _SYNONYMS.
    """
    return word in name_tokens


def _word_list(values: Any) -> list[str]:
    """Convierte una lista de frases en tokens normalizados únicos."""
    out: list[str] = []
    for v in values or []:
        for t in tokens(str(v)):
            if t not in out:
                out.append(t)
    return out


# ── Categorías ───────────────────────────────────────────────────

# Fragmentos de categoría (normalizados) que nunca son ingredientes de cocina
_NON_FOOD_CATEGORIES = (
    "cuidado personal", "belleza", "mascota", "bebe", "hogar", "ferreteria",
    "limpieza", "farmacia", "salud y belleza", "medicamento", "higiene", "papel", "desechable", "cocina y mesa",
    "electro", "jugueteria", "juguete", "ropa", "textil", "automotr", "vehiculo",
    "jardineria", "ferreteria", "libreria", "oficina", "deporte", "cuidado del hogar",
    "lavanderia", "insecticida", "cuidado capilar", "maquillaje", "perfume",
)

# Pistas de categoría de la tienda para cada categoría del ingrediente
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "frutas_vegetales": ("fruta", "vegetal", "verdura", "hortaliza", "viver", "ajos y cebolla", "hierba"),
    "carnes": ("carne", "pollo", "res", "cerdo", "pavo", "embutido", "chacinado"),
    "pescados_mariscos": ("pescado", "marisco", "mar"),
    "lacteos_huevos": ("lacteo", "leche", "queso", "huevo", "yogur", "mantequilla", "crema", "refrigerado"),
    "granos_arroz_pastas": ("arroz", "habichuela", "grano", "legumbre", "pasta", "harina", "cereal"),
    "condimentos_especias": ("condimento", "especia", "sazon", "sal", "aderezo", "salsa", "vinagre", "caldo", "cubito"),
    "aceites": ("aceite",),
    "enlatados": ("enlatado", "conserva"),
    "panaderia": ("pan", "panaderia", "reposteria", "tortilla"),
    "bebidas": ("bebida", "jugo", "refresco", "vino", "licor", "cerveza", "agua"),
    "congelados": ("congelado",),
    "dulces_reposteria": ("azucar", "endulzante", "reposteria", "chocolate", "dulce", "galleta"),
}

# Palabras que indican una forma procesada del producto. Si el ingrediente no las
# menciona, el producto se descarta (ej: 'platano verde' no debe dar 'chips de platano').
_PROCESSED_FORMS = {
    "chip", "chips", "tostone", "tostones", "polvo", "galleta", "galletas", "salsa", "sopa",
    "jugo", "nectar", "mermelada", "dulce", "snack", "picadera", "papita", "frito", "frita",
    "congelado", "congelada", "precocido", "precocida", "deshidratado", "deshidratada",
    "sazonador", "sazon", "caldo", "cubito", "esencia", "saborizante", "sabor", "aroma",
    "crema", "pure", "compota", "helado", "yogurt", "yogur", "refresco", "bebida", "te",
    "vinagre", "aceite", "pasta", "harina", "concentrado", "enlatado", "lata", "encurtido",
    "ensalada", "sandwich", "bagel", "wrap", "empanada", "pastelito", "rostizado", "nugget",
    "nuggets", "hamburguesa", "salchicha", "croqueta", "tortilla", "pan", "chocolate",
    "cereal", "barra", "gomita", "caramelo", "bizcocho", "aro", "aros",
    "planta", "plantas", "base", "clara", "yema", "pasteurizado", "pasteurizada",
    "liquido", "liquida", "codorniz", "relleno", "rellena", "marinado", "marinada",
    "triturado", "triturada", "troceado", "troceada", "seco", "seca", "asado", "asada",
    "rodaja", "semilla",
}
_PROCESSED_FORMS_STEMMED = {stem(w) for w in _PROCESSED_FORMS}

# Categorías "frescas" donde el producto debe ser el ingrediente en su forma natural
_FRESH_CATEGORIES = {"frutas_vegetales", "carnes", "pescados_mariscos", "lacteos_huevos"}

# Palabras que delatan productos no comestibles aunque estén mal categorizados
_NON_FOOD_WORDS = {stem(w) for w in (
    "shampoo", "champu", "jabon", "locion", "suavizante", "detergente", "capilar", "corporal",
    "ambientador", "whiskas", "gato", "perro", "mascota", "cepillo", "manicure", "esmalte",
    "desodorante", "insecticida", "lubricante", "penetrante", "esencial", "vela", "aromatizante",
    "acondicionador", "tinte", "panal", "toallita", "rizo", "keratina",
)}


def is_food_category(category: str) -> bool:
    cat = fold(category)
    return not any(bad in cat for bad in _NON_FOOD_CATEGORIES)


# ── Tamaños / cantidades ─────────────────────────────────────────

_UNIT_TO_GRAMS = {
    "lb": 453.6, "lbs": 453.6, "libra": 453.6, "libras": 453.6,
    "oz": 28.35, "onz": 28.35, "onza": 28.35, "onzas": 28.35,
    "g": 1.0, "gr": 1.0, "grs": 1.0, "gramo": 1.0, "gramos": 1.0,
    "kg": 1000.0, "kilo": 1000.0, "kilos": 1000.0,
    "ml": 1.0, "lt": 1000.0, "l": 1000.0, "litro": 1000.0, "litros": 1000.0,
    "gl": 3785.0, "galon": 3785.0,
}

_SIZE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(lbs?|libras?|onz(?:as?)?|oz|grs?|gramos?|g|kg|kilos?|ml|lt|litros?|l|gl|galon)\b"
)


def parse_size_grams(text: str) -> float | None:
    """Extrae el tamaño del empaque en gramos (o ml) a partir del nombre."""
    m = _SIZE_RE.search(fold(text))
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    factor = _UNIT_TO_GRAMS.get(m.group(2), None)
    if factor is None or value <= 0:
        return None
    return value * factor


def is_sold_by_weight(name: str, unit: str = "") -> bool:
    """Productos que se venden por libra (precio por lb), ej: 'Cebolla Amarilla Lb'."""
    n = fold(name)
    return bool(re.search(r"(,|\s)(lb|libra)\b", n)) and not _SIZE_RE.search(n) or fold(unit) == "lb"


# ── Matching ─────────────────────────────────────────────────────

def _product_text(p: dict[str, Any]) -> str:
    return f"{p.get('name', '')} {p.get('brand', '')}"


def evaluate_candidate(ingredient: dict[str, Any], product: dict[str, Any]) -> tuple[bool, float, str]:
    """Devuelve (es_valido, puntuacion, motivo)."""
    name = product.get("name", "")
    name_tokens = set(tokens(_product_text(product)))
    category = product.get("category", "") or ""

    if not product.get("available", True):
        return False, 0.0, "agotado"
    if product.get("price", 0) <= 0:
        return False, 0.0, "sin precio"
    if category and not is_food_category(category):
        return False, 0.0, f"categoría no alimenticia ({category})"
    # Ingrediente fresco → no vale la versión en conserva/enlatada
    if (
        ingredient.get("category", "") in _FRESH_CATEGORIES
        and re.search(r"conserva|enlatado", fold(category))
    ):
        return False, 0.0, f"conserva para ingrediente fresco ({category})"

    ingredient_text = " ".join(
        [ingredient.get("name", ""), ingredient.get("search_term", "")]
        + list(ingredient.get("required_words") or [])
    )
    ing_tokens = set(tokens(ingredient_text))

    required = _word_list(ingredient.get("required_words")) or tokens(ingredient.get("search_term", ""))[:1]
    missing = [w for w in required if not _has_word(w, name_tokens)]
    if missing:
        return False, 0.0, f"faltan palabras clave: {', '.join(missing)}"

    excluded = set(_word_list(ingredient.get("excluded_words"))) | _NON_FOOD_WORDS
    # En ingredientes frescos (vegetales, carnes...) se descartan las formas procesadas no pedidas
    if ingredient.get("category", "") in _FRESH_CATEGORIES:
        excluded |= _PROCESSED_FORMS_STEMMED
    excluded -= ing_tokens
    bad = sorted(excluded & name_tokens)
    if bad:
        return False, 0.0, f"forma/tipo incorrecto: {', '.join(bad)}"

    # ── Puntuación ──
    score = 0.0
    search_tokens = tokens(ingredient.get("search_term", "")) or tokens(ingredient.get("name", ""))
    all_wanted = set(search_tokens) | ing_tokens
    score += 10.0 * len(all_wanted & name_tokens)

    # Frase exacta del término en el nombre (ej: 'pasta de tomate')
    term = " ".join(search_tokens)
    if term and term in " ".join(tokens(name)):
        score += 15.0

    # La palabra principal está al INICIO del nombre (producto base, no derivado).
    # Se miran las 2 primeras palabras: 'Pasta Spaguetti Líder' también cuenta.
    head = tokens(name)[:2]
    if head and required and required[0] in head:
        score += 8.0

    # Palabras extra en el nombre que no se pidieron (penalización leve: marca, tamaño...)
    extra = name_tokens - all_wanted - _UNIT_TOKENS
    score -= 0.5 * len(extra)

    # Variedades no pedidas (integral, sin gluten, light, con albóndigas...)
    score -= 6.0 * len((name_tokens & _VARIANT_WORDS) - ing_tokens)

    # Mezclas no pedidas: 'Aceite de Oliva Y Girasol' para 'aceite de oliva'
    if re.search(r"\s(y|&|con)\s", fold(name)) and not re.search(r"\s(y|&|con)\s", fold(ingredient.get("name", ""))):
        score -= 5.0

    # Coherencia con la categoría esperada
    cat_key = ingredient.get("category", "")
    hints = _CATEGORY_HINTS.get(cat_key, ())
    if category and hints:
        if any(h in fold(category) for h in hints):
            score += 6.0
        else:
            score -= 4.0

    return True, score, "ok"


def _size_preference(ingredient: dict[str, Any], product: dict[str, Any]) -> float:
    """Bonus por tamaño: preferir el empaque más pequeño que cubra la cantidad necesaria."""
    needed = ingredient.get("quantity_grams") or 0
    if not needed or needed <= 0:
        return 0.0
    if is_sold_by_weight(product.get("name", ""), product.get("unit", "")):
        return 3.0  # se compra la cantidad exacta
    size = parse_size_grams(product.get("name", ""))
    if not size:
        return 0.0
    if size >= needed:
        # Cubre la necesidad: mientras menos sobre, mejor
        return 4.0 - min(3.0, (size / needed - 1.0))
    # No cubre: penalizar proporcionalmente
    return -3.0 * (1.0 - size / needed)


def rank_candidates(
    ingredient: dict[str, Any],
    products: list[dict[str, Any]],
) -> list[tuple[int, float]]:
    """Devuelve [(índice, puntuación)] de candidatos válidos, mejor primero."""
    ranked: list[tuple[int, float]] = []
    n = max(len(products), 1)
    valid: list[tuple[int, float]] = []
    for i, p in enumerate(products):
        ok, score, _ = evaluate_candidate(ingredient, p)
        if ok:
            valid.append((i, score))

    # Precio: entre productos igual de correctos, el más barato gana (bonus máx. 3 pts,
    # menor que una palabra clave, para que la exactitud siempre domine sobre el precio)
    prices = [products[i]["price"] for i, _ in valid]
    lo, hi = (math.log(min(prices)), math.log(max(prices))) if prices else (0.0, 0.0)

    for i, score in valid:
        p = products[i]
        score += _size_preference(ingredient, p)
        if hi > lo:
            score += 3.0 * (1.0 - (math.log(p["price"]) - lo) / (hi - lo))
        score += 2.0 * (n - i) / n  # orden de relevancia de la tienda como desempate
        ranked.append((i, score))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def match_best_product(ingredient: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    """Selecciona el mejor producto o devuelve match_index=-1 si ninguno corresponde."""
    if not products:
        return {"match_index": -1, "confidence": 0.0, "reasoning": "No hay productos disponibles"}

    ranked = rank_candidates(ingredient, products)
    if not ranked:
        return {
            "match_index": -1,
            "confidence": 0.0,
            "reasoning": "Ningún producto corresponde exactamente al ingrediente",
        }

    best_idx, best_score = ranked[0]
    confidence = max(0.5, min(0.99, 0.5 + best_score / 80.0))
    return {
        "match_index": best_idx,
        "confidence": round(confidence, 2),
        "reasoning": f"Coincidencia exacta: {products[best_idx].get('name')}",
        "ranked": ranked,
    }
