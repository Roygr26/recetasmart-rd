# 🍽️ RecetaSmart RD

**Comparador inteligente de precios de recetas** para supermercados en República Dominicana.

Ingresa una receta en texto libre (o una lista de platos), y la app:
1. **Extrae los ingredientes** automáticamente con IA (Gemini 2.5 Flash)
2. **Busca precios reales** en [Jumbo](https://jumbo.com.do) y [La Sirena](https://sirena.do)
3. **Compara y calcula** el costo total en 3 escenarios: 100% Jumbo, 100% La Sirena y **Compra Mixta Inteligente**

---

## 🚀 Instalación y Ejecución

### Prerrequisitos
- Python 3.11 o superior
- Una API Key de [Google AI Studio](https://aistudio.google.com/apikey) (Gemini)

### Pasos

```bash
# 1. Clona o navega al directorio del proyecto
cd recetasmart-rd

# 2. Crea un entorno virtual
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

# 3. Instala dependencias
pip install -r requirements.txt

# 4. (Opcional) Instala Playwright para el fallback de scraping
playwright install chromium

# 5. Configura tu API Key
copy .env.example .env     # Windows
# cp .env.example .env     # macOS/Linux

# Edita .env y agrega tu GEMINI_API_KEY

# 6. Ejecuta la app
uvicorn backend.main:app --reload --port 8000
```

### 🌐 Accede a la app
Abre tu navegador en **http://localhost:8000**

---

## 📁 Estructura del Proyecto

```
recetasmart-rd/
├── .env.example                 # Variables de entorno (template)
├── requirements.txt             # Dependencias Python
├── README.md                    # Este archivo
├── backend/
│   ├── main.py                  # App FastAPI + endpoints + montaje frontend
│   ├── config.py                # Configuración desde .env
│   ├── database.py              # Caché SQLite (12h TTL)
│   ├── models.py                # Esquemas Pydantic v2
│   ├── services/
│   │   ├── ai_service.py        # Gemini: extracción de ingredientes + matching
│   │   └── comparator.py        # Orquestador del flujo completo
│   └── scrapers/
│       ├── base.py              # Clase base con reintentos + User-Agent rotation
│       ├── jumbo.py             # Scraper para jumbo.com.do (VTEX API)
│       └── sirena.py            # Scraper para sirena.do (Magento API)
└── frontend/
    └── index.html               # UI dashboard (Tailwind CSS + Vanilla JS)
```

---

## 🔧 Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| **Backend** | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 |
| **Scraping** | `httpx` (asíncrono) + `playwright` (fallback headless) |
| **IA** | Google Gemini 2.5 Flash con Structured Outputs |
| **Caché** | SQLite vía `aiosqlite` (TTL: 12 horas) |
| **Frontend** | HTML5, Tailwind CSS (CDN), Vanilla JavaScript |

---

## 🧠 ¿Cómo Funciona?

1. **Entrada**: El usuario pega una receta o lista de platos dominicanos
2. **Extracción IA**: Gemini analiza el texto y extrae ingredientes con cantidades y términos de búsqueda optimizados
3. **Búsqueda Paralela**: Para cada ingrediente, se busca en paralelo en Jumbo y La Sirena (con caché de 12h)
4. **Matching Inteligente**: Gemini selecciona el mejor producto que matchea cada ingrediente
5. **Comparación**: Se calculan 3 totales y se genera una recomendación

---

## ⚙️ Variables de Entorno

| Variable | Descripción | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | API Key de Google Gemini | (requerida) |
| `SUCURSAL_DEFAULT` | Sucursal por defecto | `La Vega` |
| `CACHE_TTL_HOURS` | Horas de vida del caché | `12` |

---

## 📝 Notas

- Los precios se obtienen en tiempo real de las tiendas online
- El caché evita peticiones excesivas y posibles bloqueos
- Si la API HTTP falla, se usa Playwright como fallback automático
- Los precios pueden variar según sucursal y disponibilidad

---

## 📄 Licencia

MIT
