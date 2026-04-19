"""
GeoEnv Platform — FastAPI backend.

Security:
  - CORS restricted to the production domain + localhost (dev).
  - Rate limiting via slowapi: /analyze 10/min, /report 5/min.
  - IP logged on every analysis request for abuse detection.
  - GEE TimeoutError → HTTP 504 (explicit, not generic 500).
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import gee_client
import analysis
import reporter
from services.market_apis import get_market_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Rate limiter ────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        gee_client.initialize()
        logger.info("GEE initialized at startup")
    except Exception as e:
        logger.warning("GEE startup init failed (will retry on first request): %s", e)
    yield


app = FastAPI(
    title="GeoEnv Platform — Argentina",
    description="Environmental indices + socioeconomic diagnostics via Google Earth Engine",
    version="2.1.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# R-002: CORS restricted — no wildcard origin (GEE quota protection)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://indicadores.soildecisions.com",
        "http://localhost:8000",   # local dev
        "http://localhost:3000",   # local dev alternative
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    lat:   float = Field(..., ge=-55.0, le=-21.0)
    lon:   float = Field(..., ge=-73.5, le=-53.5)
    scale: Literal["1w", "2w", "1m", "2m", "3m", "6m", "1y"] = "1m"

    @field_validator("lat")
    @classmethod
    def lat_ok(cls, v):
        if not -55.0 <= v <= -21.0:
            raise ValueError("Latitud fuera de Argentina")
        return v

    @field_validator("lon")
    @classmethod
    def lon_ok(cls, v):
        if not -73.5 <= v <= -53.5:
            raise ValueError("Longitud fuera de Argentina")
        return v


@app.get("/health")
def health():
    return {"status": "ok", "gee_initialized": gee_client._initialized}


@app.post("/analyze")
@limiter.limit("10/minute")
def analyze(request: Request, req: AnalyzeRequest):
    t0 = time.time()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("POST /analyze ip=%s lat=%.4f lon=%.4f scale=%s",
                client_ip, req.lat, req.lon, req.scale)

    if not gee_client._initialized:
        try:
            gee_client.initialize()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=(
                f"Earth Engine no disponible: {exc}. "
                "Verifique roles/earthengine.writer en el proyecto ee-angelini75."
            ))

    try:
        result = analysis.run_analysis(req.lat, req.lon, req.scale)
    except TimeoutError as exc:
        # R-007: explicit 504 when GEE takes too long
        raise HTTPException(status_code=504, detail=str(exc))
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc))

    # Shallow-copy meta to avoid mutating the cached result dict
    elapsed = round(time.time() - t0, 2)
    result = dict(result)
    result["meta"] = dict(result["meta"])
    result["meta"]["elapsed_seconds"] = elapsed
    logger.info("Analysis done in %.1fs — indicator=%s", elapsed,
                result["situation_indicator"])
    return result


@app.post("/report")
@limiter.limit("5/minute")
async def report(request: Request, data: dict):
    """Streaming SSE endpoint: POST analysis JSON → LLM report chunks."""
    client_ip = request.client.host if request.client else "unknown"
    logger.info("POST /report ip=%s — generating AI report", client_ip)
    return StreamingResponse(
        reporter.stream_report(data),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/market")
def market(request: Request):
    """
    Return Argentine market data (exchange rates, grain FAS prices, IPC).
    Cached 4 h. Never raises — failed sources return null fields.
    """
    data = get_market_data()
    return data


app.mount("/static", StaticFiles(directory="/app/static"), name="static")


@app.get("/")
def root():
    return FileResponse("/app/static/index.html")
