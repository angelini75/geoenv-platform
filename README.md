# GeoEnv Platform — Argentina

Plataforma de diagnóstico geoespacial y socioeconómico para Argentina.  
Combina índices satelitales MODIS (Google Earth Engine) con análisis narrativo generado por Gemini.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | FastAPI + Google Earth Engine Python API |
| LLM | Google Gemini `gemini-3.1-flash-lite-preview` (google-genai SDK, streaming SSE) |
| Frontend | Leaflet · TradingView Lightweight Charts · Vanilla JS |
| Deploy | Docker Compose + Traefik (indicadores.soildecisions.com) |

---

## Índices calculados

| Grupo | Índice | Fuente | Resolución |
|-------|--------|--------|-----------|
| Vegetación | NDVI, EVI | MOD13Q1 | 250 m / 16 días |
| Óptico | NDWI, MNDWI, SAVI, NBR | MOD09A1 | 500 m / 8 días |
| Sequía | VCI, TCI, VHI | derivados NDVI+LST | — |
| Térmica | LST | MOD11A2 | 1 km / 8 días |
| Precipitación | Lluvia acumulada | CHIRPS | 5.5 km / diario |
| Evapotranspiración *(opcional)* | ET | MOD16A2GF | 500 m / 8 días |
| Humedad de suelo *(opcional)* | SM | NASA SMAP 10 km | 10 km / diario |
| Topografía *(estático)* | HAND, elevación, pendiente | MERIT Hydro + SRTM | 30–90 m |

**Z-scores estacionales**: cada valor se compara contra la media del mismo mes calendario en el baseline 2004–2024 (20 años MODIS). ET y SM tienen baselines más cortos (SMAP desde 2016).

**Curva estacional**: para cada índice se calcula la media histórica de cada mes calendario (ene, feb, … dic) de los últimos 20 años, permitiendo visualizar el ciclo típico del ecosistema y contextualizar la anomalía actual.

---

## Funcionalidades v3

### Multi-punto
- Seleccioná hasta **5 puntos** en el mapa simultáneamente.
- Cada punto se analiza de forma independiente (secuencial, para respetar la cuota GEE).
- Navegá entre puntos con las tabs de colores en el dashboard.

### Contexto de mercado argentino
- **USD oficial** (BCRA cotizaciones)
- **Tasa BADLAR** y tasa de política monetaria (BCRA estadísticas)
- **Precios FAS** soja, maíz, trigo (datos.gob.ar series)
- **IPC** mensual (datos.gob.ar)
- Datos cacheados 4 horas. Endpoint: `GET /market`

### Indicadores opcionales (Hidrología & Suelo)
- **ET** (Evapotranspiración real MODIS MOD16A2GF) — si GEE falla → `null`, nunca 504.
- **SM** (Humedad superficial del suelo SMAP) — igual, degradación graceful.
- **Contexto topográfico estático**: HAND (altura sobre drenaje más cercano), elevación y pendiente.

---

## Endpoints del backend

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/analyze` | Análisis ambiental para `{lat, lon, scale}` |
| `GET` | `/report` | Stream SSE del informe Gemini para `?lat=&lon=&scale=` |
| `GET` | `/market` | Datos de mercado argentino (BCRA + datos.gob.ar) |

---

## Estructura del repositorio

```
├── backend/
│   ├── main.py              # FastAPI app (rate limiting, CORS, endpoints)
│   ├── analysis.py          # Motor de análisis GEE + estadística
│   ├── gee_client.py        # Inicialización GEE + extractores multi-banda
│   ├── reporter.py          # Generador de informe LLM (streaming SSE)
│   ├── services/
│   │   ├── __init__.py
│   │   └── market_apis.py   # BCRA + datos.gob.ar (httpx, TTLCache 4h)
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
└── .gitignore
```

---

## Configuración local (desarrollo)

```bash
# 1. Clonar
git clone https://github.com/angelini75/geoenv-platform.git
cd geoenv-platform

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

# En la VM (vía gcloud compute ssh):
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
# Corre tests de _zscore, _pct_dev, _classify, _monthly_candles,
# _derived_clim, _vhi_clim, _vci, _tci — sin credenciales GEE.
```

---

## Actualizar contexto macroeconómico

Editar `backend/macro_context.json` y actualizar el campo `_updated` con la fecha actual.  
Si el archivo tiene más de 90 días, el backend emite un `WARNING` en los logs.

```json
{
  "_updated": "2026-04-19",
  "_source": "INDEC/BCRA proyecciones 2025-2026",
  "resumen": "Inflación ~70% i.a. ..."
}
```

---

## Informe IA (Gemini)

El informe utiliza `gemini-3.1-flash-lite-preview` con streaming SSE.  
El sistema envía al LLM:
- Definición e interpretación de cada índice (fórmula, rango, estacionalidad, anomalías +/−)
- Curva estacional (media de cada mes calendario, 20 años)
- Últimas 6 velas mensuales OHLC con z-score por candle
- Contexto socioeconómico y topográfico
- Nota metodológica sobre z-scores estacionales

---

## Limitaciones conocidas

- **Baseline estático 2004–2024**: los z-scores no incorporan tendencias climáticas de largo plazo.
- **SMAP baseline 2016–2024**: sólo 9 años de referencia para humedad de suelo (vs 20 años MODIS).
- **ET/SM opcionales**: si GEE falla en estos indicadores, se devuelven como `null` sin abortar el análisis.
- **Caché en memoria**: reiniciar el contenedor invalida el caché. Migrar a Redis si se escala a múltiples réplicas.
- **APIs de mercado**: BCRA y datos.gob.ar pueden tener latencia o cortes. El widget simplemente no aparece si fallan.
- **Regiones por bbox**: la detección de región es aproximada; los bboxes se solapan en algunos puntos de frontera.
- **Datos macro**: `macro_context.json` requiere actualización manual cada ~90 días.
