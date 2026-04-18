"""
GeoEnv Platform — FastAPI backend.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import gee_client
import analysis
import reporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


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
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
def analyze(req: AnalyzeRequest):
    t0 = time.time()
    logger.info("POST /analyze lat=%.4f lon=%.4f scale=%s", req.lat, req.lon, req.scale)

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
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc))

    result["meta"]["elapsed_seconds"] = round(time.time() - t0, 2)
    logger.info("Analysis done in %.1fs — indicator=%s", result["meta"]["elapsed_seconds"],
                result["situation_indicator"])
    return result


@app.post("/report")
async def report(data: dict):
    """Streaming SSE endpoint: POST analysis JSON → LLM report chunks."""
    logger.info("POST /report — generating AI report")
    return StreamingResponse(
        reporter.stream_report(data),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.mount("/static", StaticFiles(directory="/app/static"), name="static")


@app.get("/")
def root():
    return FileResponse("/app/static/index.html")
