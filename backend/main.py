"""
GeoEnv Platform — FastAPI backend.
Serves the environmental analysis API and the static frontend.
"""
import logging
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

import gee_client
import analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    gee_client.initialize()
    yield


app = FastAPI(
    title="GeoEnv Platform — Argentina",
    description="Environmental indices + socioeconomic diagnostics via Google Earth Engine",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    lat: float = Field(..., ge=-55.0, le=-21.0, description="Latitude (Argentina bounds)")
    lon: float = Field(..., ge=-73.5, le=-53.5, description="Longitude (Argentina bounds)")
    scale: Literal["1w", "2w", "1m", "2m", "3m", "6m", "1y"] = Field(
        default="1m", description="Temporal analysis window"
    )

    @field_validator("lat")
    @classmethod
    def lat_in_argentina(cls, v: float) -> float:
        if not -55.0 <= v <= -21.0:
            raise ValueError("Latitude must be within Argentina (-55 to -21)")
        return v

    @field_validator("lon")
    @classmethod
    def lon_in_argentina(cls, v: float) -> float:
        if not -73.5 <= v <= -53.5:
            raise ValueError("Longitude must be within Argentina (-73.5 to -53.5)")
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "gee_initialized": gee_client._initialized}


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    t0 = time.time()
    logger.info("POST /analyze lat=%.4f lon=%.4f scale=%s", req.lat, req.lon, req.scale)
    try:
        result = analysis.run_analysis(req.lat, req.lon, req.scale)
    except Exception as exc:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=str(exc))
    elapsed = round(time.time() - t0, 2)
    result["meta"]["elapsed_seconds"] = elapsed
    logger.info("Analysis complete in %.1fs — indicator=%s", elapsed, result["situation_indicator"])
    return result


# ---------------------------------------------------------------------------
# Static frontend (served last to avoid route conflicts)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="/app/static"), name="static")


@app.get("/")
def root():
    return FileResponse("/app/static/index.html")
