"""
Environmental indices computation and diagnostic engine.

Data sources:
  NDVI / EVI : MODIS/061/MOD13Q1  (16-day, 250 m)
  NDWI       : MODIS/061/MOD09A1  (8-day, 500 m)  — Gao 1996 formulation
  LST        : MODIS/061/MOD11A2  (8-day, 1 km)

Historical baseline: same calendar months, 2015-2024 (10 years).
"""
import ee
import math
import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from gee_client import (
    point_buffer,
    scale_modis_ndvi,
    scale_modis_surf_refl,
    scale_modis_lst,
    add_ndwi_band,
    extract_series,
    extract_stats,
)

logger = logging.getLogger(__name__)

SCALE_DAYS = {
    "1w": 7, "2w": 14, "1m": 30, "2m": 60,
    "3m": 90, "6m": 180, "1y": 365,
}

HISTORICAL_START = "2015-01-01"
HISTORICAL_END   = "2024-12-31"

# MODIS native pixel sizes
RES = {"ndvi": 250, "evi": 250, "ndwi": 500, "lst": 1000}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def zscore(value: float, mean: float, std: float) -> float | None:
    if std == 0 or std is None:
        return None
    return round((value - mean) / std, 3)


def pct_deviation(value: float, mean: float) -> float | None:
    if mean == 0 or mean is None:
        return None
    return round((value - mean) / abs(mean) * 100, 2)


def classify_anomaly(z: float | None, index: str) -> str:
    if z is None:
        return "Sin datos"
    az = abs(z)
    if az < 1.0:
        return "Normal"
    if az < 1.5:
        return "Anomalía moderada"
    return "Anomalía extrema"


def candlestick_direction(index: str, open_v: float, close_v: float) -> str:
    """Interpret bullish/bearish direction per index semantics."""
    if close_v > open_v:
        if index in ("ndvi", "evi"):
            return "Alcista (recuperación)"
        if index == "ndwi":
            return "Alcista (recarga hídrica)"
        if index == "lst":
            return "Alcista (estrés térmico)"
    elif close_v < open_v:
        if index in ("ndvi", "evi"):
            return "Bajista (estrés/degradación)"
        if index == "ndwi":
            return "Bajista (déficit hídrico)"
        if index == "lst":
            return "Bajista (enfriamiento)"
    return "Neutro"


def build_candlesticks(series: list[dict], hist_mean: float, hist_std: float, index: str, scale_label: str) -> list[dict]:
    """
    Group time series into monthly (or weekly for ≤2w) periods and compute OHLC.
    """
    if not series:
        return []

    days = SCALE_DAYS.get(scale_label, 30)
    group_by = "week" if days <= 14 else "month"

    def period_key(date_str: str) -> str:
        d = date.fromisoformat(date_str)
        if group_by == "week":
            iso = d.isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        return date_str[:7]  # YYYY-MM

    groups: dict[str, list[float]] = defaultdict(list)
    for pt in series:
        groups[period_key(pt["date"])].append(pt["value"])

    candles = []
    for period in sorted(groups):
        vals = groups[period]
        if not vals:
            continue
        o = vals[0]
        c = vals[-1]
        h = max(vals)
        lo = min(vals)
        z = zscore(c, hist_mean, hist_std)
        candles.append({
            "period": period,
            "open": round(o, 5),
            "close": round(c, 5),
            "high": round(h, 5),
            "low": round(lo, 5),
            "range": round(h - lo, 5),
            "direction": candlestick_direction(index, o, c),
            "z_close": z,
            "anomaly_class": classify_anomaly(z, index),
            "n_observations": len(vals),
        })
    return candles


# ---------------------------------------------------------------------------
# Per-index data fetch
# ---------------------------------------------------------------------------

def _calendar_months(start_date: date, end_date: date) -> tuple[int, int]:
    months = set()
    cur = start_date.replace(day=1)
    while cur <= end_date:
        months.add(cur.month)
        cur += relativedelta(months=1)
    months = sorted(months)
    return months[0], months[-1]


def fetch_ndvi_evi(lat: float, lon: float, start: str, end: str, cal_months: tuple[int, int]) -> dict:
    geom250 = point_buffer(lat, lon, 500)

    col = ee.ImageCollection("MODIS/061/MOD13Q1").map(scale_modis_ndvi)
    cur_col = col.filterDate(start, end)
    hist_col = (
        col.filterDate(HISTORICAL_START, HISTORICAL_END)
        .filter(ee.Filter.calendarRange(cal_months[0], cal_months[1], "month"))
    )

    ndvi_series = extract_series(cur_col, geom250, 250, "NDVI")
    evi_series  = extract_series(cur_col, geom250, 250, "EVI")
    ndvi_hist   = extract_stats(hist_col, geom250, 250, "NDVI")
    evi_hist    = extract_stats(hist_col, geom250, 250, "EVI")

    return {
        "ndvi": {"series": ndvi_series, "hist": ndvi_hist},
        "evi":  {"series": evi_series,  "hist": evi_hist},
    }


def fetch_ndwi(lat: float, lon: float, start: str, end: str, cal_months: tuple[int, int]) -> dict:
    geom500 = point_buffer(lat, lon, 1000)

    col = (
        ee.ImageCollection("MODIS/061/MOD09A1")
        .map(scale_modis_surf_refl)
        .map(add_ndwi_band)
        .select(["NDWI"])
    )
    cur_col  = col.filterDate(start, end)
    hist_col = (
        col.filterDate(HISTORICAL_START, HISTORICAL_END)
        .filter(ee.Filter.calendarRange(cal_months[0], cal_months[1], "month"))
    )

    series = extract_series(cur_col, geom500, 500, "NDWI")
    hist   = extract_stats(hist_col, geom500, 500, "NDWI")

    return {"series": series, "hist": hist}


def fetch_lst(lat: float, lon: float, start: str, end: str, cal_months: tuple[int, int]) -> dict:
    geom1k = point_buffer(lat, lon, 2000)

    col = ee.ImageCollection("MODIS/061/MOD11A2").map(scale_modis_lst).select(["LST"])
    cur_col  = col.filterDate(start, end)
    hist_col = (
        col.filterDate(HISTORICAL_START, HISTORICAL_END)
        .filter(ee.Filter.calendarRange(cal_months[0], cal_months[1], "month"))
    )

    series = extract_series(cur_col, geom1k, 1000, "LST")
    hist   = extract_stats(hist_col, geom1k, 1000, "LST")

    return {"series": series, "hist": hist}


# ---------------------------------------------------------------------------
# Summarize a single index
# ---------------------------------------------------------------------------

def summarize_index(series: list[dict], hist: dict, index: str, scale_label: str) -> dict:
    if not series:
        return {
            "current": None, "hist_mean": hist["mean"], "hist_std": hist["std"],
            "z_score": None, "pct_deviation": None, "anomaly_class": "Sin datos",
            "candlesticks": [], "hist_n_images": hist["count"],
        }

    current_val = series[-1]["value"]
    z = zscore(current_val, hist["mean"], hist["std"])
    pct = pct_deviation(current_val, hist["mean"])
    candles = build_candlesticks(series, hist["mean"], hist["std"], index, scale_label)

    return {
        "current": round(current_val, 5),
        "hist_mean": hist["mean"],
        "hist_std": hist["std"],
        "z_score": z,
        "pct_deviation": pct,
        "anomaly_class": classify_anomaly(z, index),
        "candlesticks": candles,
        "hist_n_images": hist["count"],
        "n_observations": len(series),
    }


# ---------------------------------------------------------------------------
# Situation indicator
# ---------------------------------------------------------------------------

ARGENTINA_REGION_MAP = {
    # (lat_min, lat_max, lon_min, lon_max): region_name
    (-34, -22, -64, -53): "NEA/NOA",
    (-38, -30, -65, -57): "Pampas",
    (-45, -38, -72, -65): "Patagonia Norte",
    (-55, -45, -75, -63): "Patagonia Sur",
    (-34, -28, -70, -64): "Cuyo",
}

def detect_region(lat: float, lon: float) -> str:
    for (lat_min, lat_max, lon_min, lon_max), name in ARGENTINA_REGION_MAP.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return name
    return "Argentina"


def situation_indicator(ndvi_sum: dict, ndwi_sum: dict, evi_sum: dict, lst_sum: dict) -> str:
    zs = [
        ndvi_sum.get("z_score"),
        ndwi_sum.get("z_score"),
        evi_sum.get("z_score"),
        lst_sum.get("z_score"),
    ]
    zs = [z for z in zs if z is not None]
    if not zs:
        return "INDETERMINADO"

    ndvi_z = ndvi_sum.get("z_score") or 0
    ndwi_z = ndwi_sum.get("z_score") or 0
    lst_z  = lst_sum.get("z_score") or 0

    # Critical: severe vegetation collapse, extreme heat+water stress, or extreme cold snap
    if ndvi_z < -2.0 or (ndwi_z < -1.5 and lst_z > 1.5) or lst_z < -2.5:
        return "CRÍTICO"

    # Alert: heat stress, cold anomaly, or vegetation/water stress
    if ndvi_z < -1.5 or ndwi_z < -1.5 or lst_z > 2.0 or lst_z < -2.0:
        return "ALERTA"
    if abs(ndvi_z) < 0.5 and abs(ndwi_z) < 0.5 and abs(lst_z) < 0.5:
        return "FAVORABLE"
    if ndvi_z > 1.0 and ndwi_z > 0.5:
        return "FAVORABLE"

    return "NORMAL"


# ---------------------------------------------------------------------------
# Socioeconomic context
# ---------------------------------------------------------------------------

def socioeconomic_context(lat: float, lon: float, scale_label: str,
                          ndvi_sum: dict, ndwi_sum: dict, lst_sum: dict) -> dict:
    region = detect_region(lat, lon)
    today  = date.today()
    month  = today.month

    # Season (Southern Hemisphere)
    season = ("Verano" if month in (12, 1, 2)
               else "Otoño" if month in (3, 4, 5)
               else "Invierno" if month in (6, 7, 8)
               else "Primavera")

    ndvi_z = ndvi_sum.get("z_score") or 0
    ndwi_z = ndwi_sum.get("z_score") or 0
    lst_z  = lst_sum.get("z_score") or 0

    # --- Agriculture ---
    if ndvi_z < -1.0:
        ag_assessment = (
            f"Condición vegetal deteriorada (z={ndvi_z:.2f}). Riesgo de reducción "
            f"de rendimientos en cultivos de la región {region}. "
            f"{'Estrés hídrico adicional detectado.' if ndwi_z < -1.0 else ''}"
        )
        yield_impact = "Negativo"
    elif ndvi_z > 1.0:
        ag_assessment = (
            f"Biomasa superior al promedio histórico (z={ndvi_z:.2f}). "
            f"Condiciones favorables para cultivos en {region}."
        )
        yield_impact = "Positivo"
    else:
        ag_assessment = f"Condición vegetal dentro del rango histórico normal en {region}."
        yield_impact = "Neutro"

    # --- Water stress ---
    if ndwi_z < -1.5:
        water_note = "Déficit hídrico severo. Posible necesidad de riego suplementario."
    elif ndwi_z < -0.5:
        water_note = "Estrés hídrico moderado. Monitoreo de reservas recomendado."
    else:
        water_note = "Disponibilidad hídrica dentro de parámetros normales."

    # --- Thermal context ---
    if lst_z > 1.5:
        thermal_note = f"Temperatura superficial anómala (+{lst_z:.1f}σ). Riesgo de estrés térmico en ganado y cultivos."
    elif lst_z < -2.5:
        thermal_note = f"Temperatura superficial extremadamente baja ({lst_z:.1f}σ). Riesgo elevado de heladas y daño en cultivos."
    elif lst_z < -1.5:
        thermal_note = f"Temperatura superficial significativamente baja ({lst_z:.1f}σ). Posible riesgo de heladas y estrés frío en cultivos."
    else:
        thermal_note = "Temperatura superficial dentro del rango estacional esperado."

    # --- Macro context (proxies for Argentina 2024-2026) ---
    macro_notes = (
        "Contexto macro (proxy estimado): inflación ~70% i.a. (estimado 2025), "
        "tipo de cambio oficial ~$1,100/USD, poder adquisitivo rural bajo presión. "
        "Costos de insumos agrícolas dolarizados generan margen de maniobra reducido."
    )

    return {
        "region": region,
        "season": season,
        "agriculture": {
            "assessment": ag_assessment,
            "yield_impact": yield_impact,
            "crops_at_risk": _crops_at_risk(region, season, ndvi_z, ndwi_z),
        },
        "water": water_note,
        "thermal": thermal_note,
        "macro": macro_notes,
        "causality_chain": _causality_chain(ndvi_z, ndwi_z, lst_z, region),
        "assumptions": [
            "Datos macroeconómicos son proxies estimados — fuente: INDEC / BCRA (proyecciones 2025).",
            "Correlaciones agropecuarias basadas en literatura regional (INTA, FAO).",
            "El índice NDWI utiliza formulación Gao 1996 (NIR-SWIR), sensible a humedad de canopeo.",
        ],
    }


def _crops_at_risk(region: str, season: str, ndvi_z: float, ndwi_z: float) -> list[str]:
    crops = {
        "Pampas": {
            "Primavera": ["Soja (siembra)", "Maíz (siembra tardía)"],
            "Verano": ["Soja (llenado de grano)", "Maíz (polinización)"],
            "Otoño": ["Trigo (siembra)", "Girasol (cosecha)"],
            "Invierno": ["Trigo (macollaje)", "Cebada"],
        },
        "NEA/NOA": {
            "Primavera": ["Algodón", "Caña de azúcar (rebrote)"],
            "Verano": ["Soja", "Caña de azúcar"],
            "Otoño": ["Yerba mate", "Tabaco"],
            "Invierno": ["Cítricos", "Porotos"],
        },
        "Cuyo": {
            "Primavera": ["Vid (brotación)", "Olivo"],
            "Verano": ["Vid (envero)"],
            "Otoño": ["Vid (cosecha)", "Ajo"],
            "Invierno": ["Vid (dormancia)", "Ajo"],
        },
    }.get(region, {}).get(season, ["Cultivos regionales"])

    if ndvi_z > -0.5 and ndwi_z > -0.5:
        return []
    return crops


def _causality_chain(ndvi_z: float, ndwi_z: float, lst_z: float, region: str) -> str:
    if lst_z < -2.0:
        return (
            f"Anomalía térmica negativa severa (LST {lst_z:.2f}σ) → riesgo de heladas "
            f"y estrés frío en cultivos de {region} → posible daño foliar y reducción de rendimiento "
            f"→ impacto en costos de producción y volumen cosechado."
        )
    if ndvi_z < -1.0 and ndwi_z < -1.0:
        return (
            f"Déficit hídrico ({ndwi_z:.2f}σ) → estrés vegetal ({ndvi_z:.2f}σ) "
            f"→ reducción de biomasa productiva → menor rendimiento potencial en {region} "
            f"→ impacto en ingresos rurales y cadenas agroindustriales."
        )
    if lst_z > 1.5 and ndvi_z < -0.5:
        return (
            f"Anomalía térmica positiva (LST {lst_z:.2f}σ) → estrés calórico sobre canopeo "
            f"→ reducción de eficiencia fotosintética ({ndvi_z:.2f}σ) "
            f"→ riesgo de merma productiva en {region}."
        )
    if ndvi_z > 1.0:
        return (
            f"Condición vegetal superior al promedio ({ndvi_z:.2f}σ) en {region} "
            f"→ mayor biomasa disponible → potencial mejora de rindes "
            f"→ efecto positivo sobre ingresos agropecuarios regionales."
        )
    return (
        f"Variables ambientales dentro de rangos históricos normales en {region}. "
        f"Sin cadena causal de impacto significativa identificada."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_analysis(lat: float, lon: float, scale_label: str) -> dict:
    days = SCALE_DAYS[scale_label]
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    start_str  = start_date.isoformat()
    end_str    = end_date.isoformat()

    cal_months = _calendar_months(start_date, end_date)
    logger.info("Analysis: lat=%.4f lon=%.4f scale=%s period=%s→%s cal_months=%s",
                lat, lon, scale_label, start_str, end_str, cal_months)

    # Fetch all indices (GEE calls)
    ndvi_evi_data = fetch_ndvi_evi(lat, lon, start_str, end_str, cal_months)
    ndwi_data     = fetch_ndwi(lat, lon, start_str, end_str, cal_months)
    lst_data      = fetch_lst(lat, lon, start_str, end_str, cal_months)

    # Summarize
    ndvi_sum = summarize_index(ndvi_evi_data["ndvi"]["series"], ndvi_evi_data["ndvi"]["hist"], "ndvi", scale_label)
    evi_sum  = summarize_index(ndvi_evi_data["evi"]["series"],  ndvi_evi_data["evi"]["hist"],  "evi",  scale_label)
    ndwi_sum = summarize_index(ndwi_data["series"], ndwi_data["hist"], "ndwi", scale_label)
    lst_sum  = summarize_index(lst_data["series"],  lst_data["hist"],  "lst",  scale_label)

    indicator = situation_indicator(ndvi_sum, ndwi_sum, evi_sum, lst_sum)
    socio     = socioeconomic_context(lat, lon, scale_label, ndvi_sum, ndwi_sum, lst_sum)

    return {
        "meta": {
            "lat": lat, "lon": lon,
            "scale": scale_label,
            "period_start": start_str,
            "period_end": end_str,
            "calendar_months": list(cal_months),
            "region": socio["region"],
            "season": socio["season"],
        },
        "indices": {
            "ndvi": ndvi_sum,
            "evi":  evi_sum,
            "ndwi": ndwi_sum,
            "lst":  lst_sum,
        },
        "situation_indicator": indicator,
        "socioeconomic": socio,
    }
