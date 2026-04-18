# GeoEnv Platform — Argentina

Plataforma de diagnóstico geoespacial y socioeconómico para Argentina.  
Combina índices satelitales MODIS (Google Earth Engine) con análisis narrativo generado por Gemini.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | FastAPI + Google Earth Engine Python API |
| LLM | Google Gemini `gemini-3.1-flash-lite-preview` (google-genai SDK) |
| Frontend | Leaflet · TradingView Lightweight Charts · Vanilla JS |
| Deploy | Docker Compose + Traefik (indicadores.soildecisions.com) |

---

## Índices calculados

| Grupo | Índice | Fuente MODIS | Resolución |
|-------|--------|-------------|-----------|
| Vegetación | NDVI, EVI | MOD13Q1 | 250 m / 16 días |
| Óptico | NDWI, MNDWI, SAVI, NBR | MOD09A1 | 500 m / 8 días |
| Sequía | VCI, TCI, VHI | derivados NDVI+LST | — |
| Térmica | LST | MOD11A2 | 1 km / 8 días |
| Precipitación | Lluvia acumulada | CHIRPS | 5.5 km / diario |

Z-scores **estacionales**: cada valor se compara contra la media del mismo mes calendario en el baseline 2004–2024 (20 años MODIS).

---

## Estructura del repositorio

```
├── backend/
│   ├── main.py              # FastAPI app (rate limiting, CORS, endpoints)
│   ├── analysis.py          # Motor de análisis GEE + estadística
│   ├── gee_client.py        # Inicialización GEE + extractores multi-banda
│   ├── reporter.py          # Generador de informe LLM (streaming SSE)
│   ├── macro_context.json   # Contexto macroeconómico (actualizar c/90 días)
│   ├── test_analysis.py     # Tests unitarios (pytest, sin GEE)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── docker-compose.yml
├── Makefile                 # Comandos de deploy y diagnóstico
├── audit_1.md               # Auditoría técnica Elena v1 (2026-04-18)
└── .gitignore
```

---

## Configuración local (desarrollo)

```bash
# 1. Clonar
git clone https://github.com/angelini75/indicadores.git
cd indicadores

# 2. Variables de entorno (crear .env en la raíz del repo, NUNCA commitear)
GEMINI_API_KEY=your-key-here
GEE_PROJECT=ee-angelini75
GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/credentials.json

# 3. Credenciales GEE (Service Account)
mkdir -p backend/secrets
cp /path/to/credentials.json backend/secrets/

# 4. Levantar con Docker
docker compose up --build
# → http://localhost:8000
```

---

## Deploy a la VM (producción)

```bash
# En la máquina local:
make push           # git push origin main

# En la VM (ssh angel@<IP>):
make doctor         # verifica .env, credentials, symlink, contenedor
make deploy         # git pull + docker compose build + up
make logs           # tail logs del backend
```

### Primera vez en VM nueva

```bash
make setup-git      # git config safe.directory
make setup-symlink  # ln -sf /opt/mi-stack/.env /opt/mi-stack/geoenv/.env
make doctor         # verificar todo antes de levantar
make deploy
```

> **Importante:** la `GEMINI_API_KEY` y las credenciales GEE se inyectan en la VM **directamente** vía `sudo nano /opt/mi-stack/.env` — nunca deben aparecer en el chat, en commits, ni en issues.

---

## Tests

```bash
cd backend
pip install pytest
pytest test_analysis.py -v
# Corre 20+ tests de _zscore, _pct_dev, _classify, _monthly_candles,
# _derived_clim, _vhi_clim, _vci, _tci — sin credenciales GEE.
```

---

## Actualizar contexto macroeconómico

Editar `backend/macro_context.json` y actualizar el campo `_updated` con la fecha actual.  
Si el archivo tiene más de 90 días, el backend emite un `WARNING` en los logs.

```json
{
  "_updated": "2026-04-18",
  "_source": "INDEC/BCRA proyecciones 2025-2026",
  "resumen": "Inflación ~70% i.a. ..."
}
```

---

## Limitaciones conocidas

- **Baseline estático 2004–2024**: los z-scores no incorporan tendencias climáticas de largo plazo (ver [audit_1.md](audit_1.md) R-004).
- **Caché en memoria**: reiniciar el contenedor invalida el caché. Migrar a Redis si se escala a múltiples réplicas.
- **Regiones por bbox**: la detección de región es aproximada; los bboxes se solapan en algunos puntos de frontera.
- **Datos macro**: `macro_context.json` requiere actualización manual cada ~90 días.
