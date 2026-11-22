"""RecetaSmart RD — API FastAPI principal."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.database import clear_expired
from backend.models import ComparisonResult, RecipeRequest
from backend.services.comparator import compare_recipe

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
)
logger = logging.getLogger("recetasmart")


# ── Lifespan ──────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Acciones al iniciar y cerrar la app."""
    logger.info("🚀 RecetaSmart RD iniciando...")
    logger.info("   Sucursal por defecto: %s", settings.SUCURSAL_DEFAULT)
    logger.info("   Gemini API Key: %s", "✅ configurada" if settings.GEMINI_API_KEY else "❌ FALTA")
    # Limpiar caché expirado al arrancar
    try:
        removed = await clear_expired()
        if removed:
            logger.info("   🗑️  Cache expirado limpiado: %d entradas", removed)
    except Exception as e:
        logger.warning("   ⚠️  No se pudo limpiar el caché al arrancar: %s", e)
    yield
    logger.info("👋 RecetaSmart RD cerrando...")


# ── App FastAPI ───────────────────────────────────────────────────
app = FastAPI(
    title="RecetaSmart RD",
    description="Compara precios de ingredientes de recetas entre Jumbo y La Sirena en RD",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints API ────────────────────────────────────────────────
@app.post("/api/compare", response_model=ComparisonResult)
@app.post("/compare", response_model=ComparisonResult)
@app.post("/", response_model=ComparisonResult)
async def api_compare(request: RecipeRequest):
    """Recibe una receta, extrae ingredientes y compara precios."""
    if not settings.GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY no configurada en las variables de entorno de Vercel.",
        )

    try:
        result = await compare_recipe(
            recipe_text=request.recipe_text,
            sucursal=request.sucursal,
        )
        return result
    except Exception as e:
        logger.exception("Error procesando receta")
        raise HTTPException(status_code=500, detail=f"Error procesando receta: {str(e)}")


@app.get("/api/health")
@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "gemini_configured": bool(settings.GEMINI_API_KEY),
        "sucursal_default": settings.SUCURSAL_DEFAULT,
    }


# ── Frontend estático ────────────────────────────────────────────
_PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_STATIC_DIR = _PUBLIC_DIR if _PUBLIC_DIR.exists() else _FRONTEND_DIR

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    async def serve_frontend():
        """Sirve el index.html."""
        index = _STATIC_DIR / "index.html"
        if index.exists():
            return FileResponse(str(index))
        raise HTTPException(status_code=404, detail="Frontend no encontrado")
