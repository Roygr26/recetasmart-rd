"""Configuración centralizada desde variables de entorno."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Cargar .env desde la raíz del proyecto si existe
    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_ENV_PATH)
except ImportError:
    pass


class Settings:
    """Configuración de la aplicación."""

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    SUCURSAL_DEFAULT: str = os.getenv("SUCURSAL_DEFAULT", "La Vega")
    CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "12"))

    # Ruta a la base de datos SQLite de caché (en Vercel /tmp es el único directorio escribible)
    _DEFAULT_DB: str = "/tmp/cache.db" if os.getenv("VERCEL") else str(Path(__file__).resolve().parent.parent / "cache.db")
    DB_PATH: str = os.getenv("DB_PATH", _DEFAULT_DB)

    # Modelo Gemini por defecto (Gemini 3.5 Flash Lite con 500 RPD)
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


settings = Settings()
