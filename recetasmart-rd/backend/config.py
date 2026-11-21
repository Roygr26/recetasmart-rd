"""Configuración centralizada desde variables de entorno."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env desde la raíz del proyecto
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


class Settings:
    """Configuración de la aplicación."""

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    SUCURSAL_DEFAULT: str = os.getenv("SUCURSAL_DEFAULT", "La Vega")
    CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "12"))

    # Ruta a la base de datos SQLite de caché
    DB_PATH: str = str(Path(__file__).resolve().parent.parent / "cache.db")

    # Modelo Gemini
    GEMINI_MODEL: str = "gemini-2.5-flash"


settings = Settings()
