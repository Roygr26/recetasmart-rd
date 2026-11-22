"""Esquemas Pydantic v2 para la API."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Request ────────────────────────────────────────────────────────
class RecipeRequest(BaseModel):
    """Payload que envía el frontend."""
    recipe_text: str = Field(..., min_length=3, description="Texto libre de la receta o lista de platos")
    sucursal: str = Field(default="La Vega", description="Sucursal / ciudad de referencia")


# ── Ingrediente extraído por IA ────────────────────────────────────
class Ingredient(BaseModel):
    """Ingrediente normalizado devuelto por Gemini."""
    name: str = Field(..., description="Nombre del ingrediente en español")
    quantity: str = Field(..., description="Cantidad con unidad (ej: '500 g', '2 unidades')")
    search_term: str = Field(..., description="Término de búsqueda optimizado para supermercado")
    alt_terms: list[str] = Field(default_factory=list, description="Términos de búsqueda alternativos")
    required_words: list[str] = Field(default_factory=list, description="Palabras obligatorias en el producto")
    excluded_words: list[str] = Field(default_factory=list, description="Palabras que indican producto equivocado")
    category: str = Field(default="", description="Categoría esperada del producto")
    quantity_grams: float = Field(default=0.0, ge=0, description="Cantidad necesaria aprox. en g/ml")


# ── Producto encontrado en un supermercado ─────────────────────────
class ProductOption(BaseModel):
    """Un producto concreto de Jumbo o La Sirena."""
    store: str = Field(..., description="jumbo | sirena")
    name: str = Field(..., description="Nombre del producto como aparece en la tienda")
    brand: str = Field(default="", description="Marca del producto")
    price: float = Field(..., ge=0, description="Precio en RD$")
    unit: str = Field(default="", description="Unidad de venta (ej: '1 lb', '500 g')")
    image_url: str = Field(default="", description="URL de la imagen del producto")
    product_url: str = Field(default="", description="URL directa al producto")
    available: bool = Field(default=True, description="Si está disponible")
    category: str = Field(default="", description="Categoría del producto en la tienda")


# ── Comparación por ingrediente ────────────────────────────────────
class IngredientComparison(BaseModel):
    """Comparación de un ingrediente entre ambas tiendas."""
    ingredient: Ingredient
    jumbo_options: list[ProductOption] = Field(default_factory=list)
    sirena_options: list[ProductOption] = Field(default_factory=list)
    best_jumbo: ProductOption | None = None
    best_sirena: ProductOption | None = None
    cheapest_store: str = Field(default="", description="jumbo | sirena | empate")
    jumbo_confidence: float = Field(default=0.0, description="Confianza del match en Jumbo (0-1)")
    sirena_confidence: float = Field(default=0.0, description="Confianza del match en Sirena (0-1)")


# ── Resultado final ───────────────────────────────────────────────
class ComparisonResult(BaseModel):
    """Respuesta completa al frontend."""
    ingredients: list[IngredientComparison] = Field(default_factory=list)
    total_jumbo: float = Field(default=0.0, description="Total comprando todo en Jumbo")
    total_sirena: float = Field(default=0.0, description="Total comprando todo en La Sirena")
    total_mixed: float = Field(default=0.0, description="Total compra mixta inteligente")
    savings_vs_jumbo: float = Field(default=0.0)
    savings_vs_sirena: float = Field(default=0.0)
    recommendation: str = Field(default="", description="Resumen textual de la recomendación")
