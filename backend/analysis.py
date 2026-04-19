"""
Environmental analysis engine — Argentina.

Data sources:
  MOD13Q1  : NDVI, EVI               (16-day, 250 m)
  MOD09A1  : NDWI, MNDWI, SAVI, NBR  (8-day, 500 m)
  MOD11A2  : LST Day + Night          (8-day, 1 km)
  MOD16A2  : ET                       (8-day, 500 m)
  SMAP     : Soil Moisture            (daily, 10 km)
  CHIRPS   : Precipitation            (daily, 5.5 km)

Derived: VCI, TCI, VHI  (from NDVI + LST historical min/max)

Baseline: 2004-2024 (20 years of MODIS).
Z-scores are SEASONALLY ADJUSTED: each index is compared to the
climatological mean for the SAME calendar month, not to an annual mean.
Visualization: seasonal quantile bands (p10/p25/p50/p75/p90) per calendar month
plus last 24 months of actual monthly values.
GEE fetches run in parallel threads to minimize latency.
"""
import ee
import json as _json
import logging
import math
import os as _os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from cachetools import TTLCache

from gee_client import (
    point_buffer,
    scale_mod13q1, scale_mod09a1, scale_mod11a2,
    scale_mod16a2, scale_smap, extract_static_context,
    extract_series, extract_stats, extract_monthly_climatology,
)

logger = logging.getLogger(__name__)

SCALE_DAYS = {
    "1w": 7, "2w": 14, "1m": 30, "2m": 60,
    "3m": 90, "6m": 180, "1y": 365,
}
HIST_START      = "2004-01-01"   # 20-year MODIS baseline
HIST_END        = "2024-12-31"
SMAP_HIST_START = "2015-04-01"  # SMAP operational since April 2015
SMAP_HIST_END   = "2024-12-31"
RECENT_MONTHS   = 24             # recent trend series spans last N months
GEE_TIMEOUT     = 45             # seconds — main 4 fetches
GEE_OPT_TIMEOUT = 30             # seconds — optional indicators (ET, SM, static)

# ── TTL cache for run_analysis (R-006) ─────────────────────────────
# Key: (round(lat,3), round(lon,3), scale_label) → ~110m spatial tolerance
# TTL: 6h — MODIS composites are 8- or 16-day; no point re-fetching within a day.
_analysis_cache: TTLCache = TTLCache(maxsize=256, ttl=6 * 3600)
_cache_lock = threading.Lock()

# ── Macro context loader (R-012) ────────────────────────────────────
_MACRO_FILE = _os.path.join(_os.path.dirname(__file__), "macro_context.json")

def _load_macro() -> str:
    """Load macro context from external JSON; warn if stale (>90 days)."""
    try:
        with open(_MACRO_FILE) as f:
            data = _json.load(f)
        updated = date.fromisoformat(data.get("_updated", "2000-01-01"))
        age_days = (date.today() - updated).days
        if age_days > 90:
            logger.warning("macro_context.json tiene %d días sin actualizar (>90)", age_days)
        source  = data.get("_source", "")
        resumen = data.get("resumen", "")
        return f"Contexto macro estimado ({source}): {resumen}"
    except Exception as e:
        logger.warning("No se pudo cargar macro_context.json: %s", e)
        return "Contexto macroeconómico no disponible (macro_context.json ausente o inválido)."

MONTH_NAMES = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def _zscore(value, mean, std):
    if None in (value, mean, std) or std == 0:
        return None
    return round((value - mean) / std, 3)


def _pct_dev(value, mean):
    # R-010: guard against near-zero mean (NDWI/MNDWI can be ~0 in arid zones)
    # which produces meaningless thousands-of-percent values.
    if None in (value, mean) or abs(mean) < 0.01:
        return None
    return round((value - mean) / abs(mean) * 100, 2)


def _classify(z) -> str:
    if z is None:
        return "sin_datos"
    if z < -1.5:
        return "muy_bajo"
    if z < -0.5:
        return "bajo"
    if z <= 0.5:
        return "normal"
    if z <= 1.5:
        return "alto"
    return "muy_alto"


def _calendar_months(start: date, end: date) -> tuple[int, int]:
    months = set()
    cur = start.replace(day=1)
    while cur <= end:
        months.add(cur.month)
        cur += relativedelta(months=1)
    months = sorted(months)
    return months[0], months[-1]


def _monthly_aggregate(series: list[dict]) -> list[dict]:
    """
    Group a raw (8/16-day) series into monthly means.
    Returns [{date: "YYYY-MM-01", value: float}] sorted by date.
    """
    if not series:
        return []
    groups: dict[str, list] = defaultdict(list)
    for pt in series:
        groups[pt["date"][:7]].append(pt["value"])
    return [
        {"date": ym + "-01", "value": round(sum(v) / len(v), 5)}
        for ym, v in sorted(groups.items())
    ]


# ---------------------------------------------------------------------------
# Summarize one index using seasonal (per-month) climatology
# ---------------------------------------------------------------------------

def _summarize(cur_series: list[dict], recent_raw_series: list[dict],
               hist_alltime: dict, monthly_clim: dict,
               index: str,
               current_override=None,
               current_date_override=None) -> dict:
    """
    Build a seasonal-comparison summary for one environmental index.

    cur_series          : raw series for the analysis window (for current value)
    recent_raw_series   : raw series for last RECENT_MONTHS (for trend chart)
    hist_alltime        : {mean, std, min, max, count} — all-time stats (VCI/TCI bounds)
    monthly_clim        : {month_int: {mean, std, p10, p25, p50, p75, p90}}
    current_override    : use this value instead of the last point in cur_series
    current_date_override: use this date instead of the last point's date
    """
    val = (current_override if current_override is not None
           else (cur_series[-1]["value"] if cur_series else None))
    cur_date = (current_date_override
                or (cur_series[-1]["date"] if cur_series else None))

    cur_month = date.today().month
    m_stats   = (monthly_clim or {}).get(cur_month, {})
    # Guard: a mean of 0.0 is falsy but valid (e.g. NDWI in arid zones)
    clim_mean = m_stats["mean"] if m_stats.get("mean") is not None else hist_alltime.get("mean")
    clim_std  = m_stats["std"]  if m_stats.get("std")  is not None else hist_alltime.get("std")

    z = _zscore(val, clim_mean, clim_std)

    # Build 12-month climatology dict with all percentile levels
    climatology = {}
    for m in range(1, 13):
        md = (monthly_clim or {}).get(m, {})
        climatology[m] = {
            "mean": md.get("mean"),
            "p10":  md.get("p10"),
            "p25":  md.get("p25"),
            "p50":  md.get("p50"),
            "p75":  md.get("p75"),
            "p90":  md.get("p90"),
        }

    recent_series = _monthly_aggregate(recent_raw_series)

    return {
        "current":        round(val, 5) if val is not None else None,
        "current_date":   cur_date,
        "hist_mean":      clim_mean,
        "hist_std":       clim_std,
        "hist_min":       hist_alltime.get("min"),
        "hist_max":       hist_alltime.get("max"),
        "z_score":        z,
        "pct_deviation":  _pct_dev(val, clim_mean),
        "anomaly_class":  _classify(z),
        "n_observations": len(cur_series),
        "climatology":    climatology,    # 12-month seasonal quantile bands
        "recent_series":  recent_series,  # last RECENT_MONTHS monthly means
    }


# ---------------------------------------------------------------------------
# Derived index climatology helpers (no extra GEE calls)
# ---------------------------------------------------------------------------

def _vci(ndvi_cur, hmin, hmax):
    d = hmax - hmin
    if d == 0 or ndvi_cur is None:
        return None
    return round(max(0.0, min(1.0, (ndvi_cur - hmin) / d)), 4)


def _tci(lst_cur, hmin, hmax):
    d = hmax - hmin
    if d == 0 or lst_cur is None:
        return None
    return round(max(0.0, min(1.0, (hmax - lst_cur) / d)), 4)


def _derived_clim(base_clim: dict, hmin: float, hmax: float, invert: bool = False) -> dict:
    """
    Propagate monthly climatology (mean + all percentiles) through VCI or TCI formula.
    For invert=True (TCI from LST): low LST → high TCI, so p10 and p90 swap.
    """
    d = hmax - hmin
    if d == 0:
        return {}

    def t(v):
        if v is None:
            return None
        raw = (hmax - v) / d if invert else (v - hmin) / d
        return round(max(0.0, min(1.0, raw)), 4)

    result = {}
    for m, stats in (base_clim or {}).items():
        mu = stats.get("mean")
        sd = stats.get("std")
        result[m] = {
            "mean": t(mu),
            "std":  round(sd / d, 4) if sd is not None else None,
            # When inverting (TCI): percentile order flips
            "p10":  t(stats.get("p90" if invert else "p10")),
            "p25":  t(stats.get("p75" if invert else "p25")),
            "p50":  t(stats.get("p50")),
            "p75":  t(stats.get("p25" if invert else "p75")),
            "p90":  t(stats.get("p10" if invert else "p90")),
        }
    return result


def _safe_vhi_pct(v: dict, t: dict, pct: str):
    """VHI = 0.5*VCI + 0.5*TCI — propagate one percentile level."""
    vp = v.get(pct)
    tp = t.get(pct)
    if vp is None or tp is None:
        return None
    return round(max(0.0, min(1.0, 0.5 * vp + 0.5 * tp)), 4)


def _vci_series(ndvi_series, hmin, hmax):
    d = hmax - hmin
    if d == 0:
        return []
    return [{"date": pt["date"],
             "value": round(max(0.0, min(1.0, (pt["value"] - hmin) / d)), 4)}
            for pt in ndvi_series]


def _tci_series(lst_series, hmin, hmax):
    d = hmax - hmin
    if d == 0:
        return []
    return [{"date": pt["date"],
             "value": round(max(0.0, min(1.0, (hmax - pt["value"]) / d)), 4)}
            for pt in lst_series]


def _vhi_series(vci_s, tci_s):
    vci_map = {pt["date"][:7]: pt["value"] for pt in vci_s}
    tci_map = {pt["date"][:7]: pt["value"] for pt in tci_s}
    return [{"date": ym + "-01",
             "value": round(0.5 * vci_map[ym] + 0.5 * tci_map[ym], 4)}
            for ym in sorted(set(vci_map) & set(tci_map))]


def _vhi_clim(vci_clim, tci_clim):
    result = {}
    for m in range(1, 13):
        v = (vci_clim or {}).get(m, {})
        t = (tci_clim or {}).get(m, {})
        if v.get("mean") is not None and t.get("mean") is not None:
            std_v = v.get("std") or 0.0
            std_t = t.get("std") or 0.0
            result[m] = {
                "mean": round(0.5 * v["mean"] + 0.5 * t["mean"], 4),
                # R-009: correct variance propagation (sqrt of sum of variances)
                "std":  round(math.sqrt(0.25 * std_v**2 + 0.25 * std_t**2), 4),
                "p10":  _safe_vhi_pct(v, t, "p10"),
                "p25":  _safe_vhi_pct(v, t, "p25"),
                "p50":  _safe_vhi_pct(v, t, "p50"),
                "p75":  _safe_vhi_pct(v, t, "p75"),
                "p90":  _safe_vhi_pct(v, t, "p90"),
            }
    return result


# ---------------------------------------------------------------------------
# GEE fetch functions (parallel threads)
# ---------------------------------------------------------------------------

def _recent_range():
    """Last RECENT_MONTHS months for the trend series."""
    end = date.today()
    return (end - relativedelta(months=RECENT_MONTHS)).isoformat(), end.isoformat()


def _fetch_vegetation(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 500)
    col   = ee.ImageCollection("MODIS/061/MOD13Q1").map(scale_mod13q1)
    hist  = col.filterDate(HIST_START, HIST_END)
    rs, re = _recent_range()
    bands = ["NDVI", "EVI"]
    return {
        "cur_series":    extract_series(col.filterDate(start, end), geom, 250, bands),
        "hist_stats":    extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                       geom, 250, bands),
        "recent_series": extract_series(col.filterDate(rs, re), geom, 250, bands),
        "monthly_clim":  extract_monthly_climatology(hist, geom, 250, bands),
    }


def _fetch_optical(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 1000)
    col   = ee.ImageCollection("MODIS/061/MOD09A1").map(scale_mod09a1)
    hist  = col.filterDate(HIST_START, HIST_END)
    rs, re = _recent_range()
    bands = ["NDWI", "MNDWI", "SAVI", "NBR"]
    return {
        "cur_series":    extract_series(col.filterDate(start, end), geom, 500, bands),
        "hist_stats":    extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                       geom, 500, bands),
        "recent_series": extract_series(col.filterDate(rs, re), geom, 500, bands),
        "monthly_clim":  extract_monthly_climatology(hist, geom, 500, bands),
    }


def _fetch_lst(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 2000)
    col   = ee.ImageCollection("MODIS/061/MOD11A2").map(scale_mod11a2)
    hist  = col.filterDate(HIST_START, HIST_END)
    rs, re = _recent_range()
    bands = ["LST", "LST_Night"]
    return {
        "cur_series":    extract_series(col.filterDate(start, end), geom, 1000, bands),
        "hist_stats":    extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                       geom, 1000, bands),
        "recent_series": extract_series(col.filterDate(rs, re), geom, 1000, bands),
        "monthly_clim":  extract_monthly_climatology(hist, geom, 1000, bands),
    }


def _fetch_precip(lat, lon, start, end, cal):
    geom = point_buffer(lat, lon, 5500)
    col  = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").select("precipitation")

    cur_mm = round(
        col.filterDate(start, end).sum()
        .reduceRegion(ee.Reducer.mean(), geom, 5500, maxPixels=1e6)
        .getInfo().get("precipitation", 0) or 0, 1
    )

    days     = (date.fromisoformat(end) - date.fromisoformat(start)).days or 1
    hist_col = col.filterDate(HIST_START, HIST_END).filter(
        ee.Filter.calendarRange(cal[0], cal[1], "month"))
    hist_img = hist_col.reduce(
        ee.Reducer.mean().combine(ee.Reducer.stdDev(), sharedInputs=True))
    hr = hist_img.reduceRegion(ee.Reducer.mean(), geom, 5500, maxPixels=1e6).getInfo()

    hist_mm     = round((hr.get("precipitation_mean") or 0) * days, 1)
    hist_std_mm = round((hr.get("precipitation_stdDev") or 0) * days, 1)
    z = _zscore(cur_mm, hist_mm, hist_std_mm)

    return {
        "current_mm":    cur_mm,
        "hist_mean_mm":  hist_mm,
        "hist_std_mm":   hist_std_mm,
        "z_score":       z,
        "pct_deviation": _pct_dev(cur_mm, hist_mm),
        "anomaly_class": _classify(z),
        "analysis_days": days,
    }


# ---------------------------------------------------------------------------
# Optional GEE fetch functions (ET, Soil Moisture, static context)
# ---------------------------------------------------------------------------

def _fetch_et(lat, lon, start, end, cal):
    """Evapotranspiración real — MODIS MOD16A2GF, 500 m, 8-day."""
    geom  = point_buffer(lat, lon, 1000)
    col   = ee.ImageCollection("MODIS/061/MOD16A2GF").map(scale_mod16a2)
    hist  = col.filterDate(HIST_START, HIST_END)
    rs, re = _recent_range()
    return {
        "cur_series":    extract_series(col.filterDate(start, end), geom, 500, ["ET"]),
        "hist_stats":    extract_stats(
            hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
            geom, 500, ["ET"]),
        "recent_series": extract_series(col.filterDate(rs, re), geom, 500, ["ET"]),
        "monthly_clim":  extract_monthly_climatology(hist, geom, 500, ["ET"]),
    }


def _fetch_soil_moisture(lat, lon, start, end, cal):
    """Humedad superficial del suelo — NASA SMAP 10 km, diario."""
    geom  = point_buffer(lat, lon, 10000)
    col   = ee.ImageCollection("NASA_USDA/HSL/SMAP10KM_soil_moisture").map(scale_smap)
    hist  = col.filterDate(SMAP_HIST_START, SMAP_HIST_END)
    rs, re = _recent_range()
    return {
        "cur_series":    extract_series(col.filterDate(start, end), geom, 10000, ["ssm"]),
        "hist_stats":    extract_stats(
            hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
            geom, 10000, ["ssm"]),
        "recent_series": extract_series(col.filterDate(rs, re), geom, 10000, ["ssm"]),
        "monthly_clim":  extract_monthly_climatology(hist, geom, 10000, ["ssm"]),
    }


# ---------------------------------------------------------------------------
# Region / season / situation helpers
# ---------------------------------------------------------------------------

# R-008: converted from dict to ordered list so the lookup is deterministic
# (dict was CPython insertion-order by accident, not by design).
# Cuyo checked before Pampas because its bbox is more specific (western longitudes).
# Tuple format: (lat_min, lat_max, lon_min, lon_max)
_REGIONS = [
    ((-34, -22, -64, -53), "NEA/NOA"),
    ((-34, -28, -70, -64), "Cuyo"),           # checked before Pampas
    ((-38, -30, -65, -57), "Pampas"),
    ((-45, -38, -72, -65), "Patagonia Norte"),
    ((-55, -45, -75, -63), "Patagonia Sur"),
]


def _region(lat, lon):
    for (la, lb, lo, lob), name in _REGIONS:
        if la <= lat <= lb and lo <= lon <= lob:
            return name
    return "Argentina"


def _season(month):
    return ("Verano" if month in (12, 1, 2)
            else "Otoño" if month in (3, 4, 5)
            else "Invierno" if month in (6, 7, 8)
            else "Primavera")


def _situation(idx: dict) -> str:
    ndvi_z = idx["ndvi"]["z_score"] or 0
    ndwi_z = idx["ndwi"]["z_score"] or 0
    lst_z  = idx["lst"]["z_score"]  or 0
    vhi    = idx["vhi"]["current"]

    if ndvi_z < -2.0 or (ndwi_z < -1.5 and lst_z > 1.5) or lst_z < -2.5 or lst_z > 3.0:
        return "CRÍTICO"
    if ndvi_z < -1.5 or ndwi_z < -1.5 or lst_z > 2.0 or lst_z < -2.0 or (vhi is not None and vhi < 0.2):
        return "ALERTA"
    if abs(ndvi_z) < 0.5 and abs(ndwi_z) < 0.5 and abs(lst_z) < 0.5:
        return "FAVORABLE"
    if ndvi_z > 1.0 and ndwi_z > 0.5:
        return "FAVORABLE"
    return "NORMAL"


# ---------------------------------------------------------------------------
# Socioeconomic context
# ---------------------------------------------------------------------------

def _socio(lat, lon, scale, idx: dict) -> dict:
    region = _region(lat, lon)
    season = _season(date.today().month)
    ndvi_z = idx["ndvi"]["z_score"] or 0
    ndwi_z = idx["ndwi"]["z_score"] or 0
    lst_z  = idx["lst"]["z_score"]  or 0
    precip = idx["precipitation"]

    ag = (
        f"Condición vegetal deteriorada (NDVI z={ndvi_z:.2f}). Riesgo de reducción de rendimientos en {region}."
        + (" Déficit hídrico adicional." if ndwi_z < -1.0 else "")
        if ndvi_z < -1.0
        else f"Biomasa superior al promedio histórico estacional (NDVI z=+{ndvi_z:.2f}). Condiciones favorables en {region}."
        if ndvi_z > 1.0
        else f"Condición vegetal dentro del rango estacional normal en {region}."
    )
    yield_impact = "Negativo" if ndvi_z < -1.0 else "Positivo" if ndvi_z > 1.0 else "Neutro"

    water = (
        "Déficit hídrico severo. Probable necesidad de riego suplementario." if ndwi_z < -1.5
        else "Estrés hídrico moderado. Monitoreo de reservas recomendado." if ndwi_z < -0.5
        else "Disponibilidad hídrica dentro de parámetros normales."
    )

    if precip["current_mm"] is not None and precip["hist_mean_mm"] > 0:
        p_pct = round((precip["current_mm"] - precip["hist_mean_mm"]) / precip["hist_mean_mm"] * 100, 1)
        precip_note = (f"Precipitación acumulada: {precip['current_mm']} mm vs "
                       f"media histórica {precip['hist_mean_mm']} mm ({'+' if p_pct >= 0 else ''}{p_pct}%).")
    else:
        precip_note = "Datos de precipitación no disponibles."

    thermal = (
        f"Temperatura superficial anómala alta (+{lst_z:.1f}σ). Riesgo de estrés térmico." if lst_z > 1.5
        else f"Temperatura superficial extremadamente baja ({lst_z:.1f}σ). Riesgo elevado de heladas." if lst_z < -2.5
        else f"Temperatura superficial significativamente baja ({lst_z:.1f}σ). Posible riesgo de heladas." if lst_z < -1.5
        else "Temperatura superficial dentro del rango estacional esperado."
    )

    if lst_z < -2.0:
        causality = (f"Anomalía térmica fría (LST {lst_z:.2f}σ estacional) → riesgo de heladas en {region} "
                     f"→ posible daño foliar → reducción de rendimiento → presión sobre ingresos rurales.")
    elif ndvi_z < -1.0 and ndwi_z < -1.0:
        causality = (f"Déficit hídrico (NDWI {ndwi_z:.2f}σ) → estrés vegetal (NDVI {ndvi_z:.2f}σ) "
                     f"→ reducción de biomasa → menor rendimiento potencial → impacto en cadenas agroindustriales de {region}.")
    elif lst_z > 1.5 and ndvi_z < -0.5:
        causality = (f"Anomalía térmica caliente (LST +{lst_z:.2f}σ) → estrés calórico "
                     f"→ reducción fotosintética (NDVI {ndvi_z:.2f}σ) → riesgo de merma productiva en {region}.")
    elif ndvi_z > 1.0:
        causality = (f"Condición vegetal superior al promedio estacional (NDVI +{ndvi_z:.2f}σ) → mayor biomasa disponible "
                     f"→ potencial mejora de rindes → efecto positivo sobre ingresos agropecuarios de {region}.")
    else:
        causality = (f"Variables ambientales dentro de rangos estacionales históricos en {region}. "
                     "Sin cadena causal de impacto significativa identificada.")

    crops = _crops(region, season, ndvi_z, ndwi_z)

    return {
        "region": region, "season": season,
        "agriculture": {"assessment": ag, "yield_impact": yield_impact, "crops_at_risk": crops},
        "water": water, "precipitation": precip_note, "thermal": thermal,
        "causality_chain": causality,
        "assumptions": [
            "Baseline histórico MODIS 2004-2024 (20 años). Z-scores ajustados estacionalmente por mes calendario.",
            "LIMITACIÓN (R-004): el baseline es estático 2004-2024. Si una variable tiene tendencia climática "
            "(ej.: LST en ascenso +0.5°C/décad), el z-score incorpora ese sesgo. "
            "Los valores no reflejan si la anomalía es relativa a la nueva normalidad climática.",
            "VCI/TCI/VHI climatologías derivadas de las curvas estacionales de NDVI y LST.",
            "NDWI formula Gao 1996 (NIR-SWIR): sensible a humedad de canopeo.",
            "Precipitación acumulada fuente CHIRPS (resolución 5.5 km, ~5 días latencia).",
        ],
    }


def _crops(region, season, ndvi_z, ndwi_z):
    if ndvi_z > -0.5 and ndwi_z > -0.5:
        return []
    table = {
        "Pampas":          {"Primavera": ["Soja (siembra)", "Maíz (siembra tardía)"],
                            "Verano":    ["Soja (llenado)", "Maíz (polinización)"],
                            "Otoño":     ["Trigo (siembra)", "Girasol (cosecha)"],
                            "Invierno":  ["Trigo (macollaje)", "Cebada"]},
        "NEA/NOA":         {"Primavera": ["Algodón", "Caña de azúcar (rebrote)"],
                            "Verano":    ["Soja", "Caña de azúcar"],
                            "Otoño":     ["Yerba mate", "Tabaco"],
                            "Invierno":  ["Cítricos", "Porotos"]},
        "Cuyo":            {"Primavera": ["Vid (brotación)", "Olivo"],
                            "Verano":    ["Vid (envero)"],
                            "Otoño":     ["Vid (cosecha)", "Ajo"],
                            "Invierno":  ["Vid (dormancia)"]},
    }
    return table.get(region, {}).get(season, ["Cultivos regionales"])


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_analysis(lat: float, lon: float, scale_label: str) -> dict:
    # R-006: check cache before hitting GEE
    cache_key = (round(lat, 3), round(lon, 3), scale_label)
    with _cache_lock:
        if cache_key in _analysis_cache:
            logger.info("Cache HIT — (%.3f, %.3f) scale=%s", lat, lon, scale_label)
            return _analysis_cache[cache_key]

    days       = SCALE_DAYS[scale_label]
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    start      = start_date.isoformat()
    end        = end_date.isoformat()
    cal        = _calendar_months(start_date, end_date)

    logger.info("Analysis: (%.4f, %.4f) scale=%s %s→%s", lat, lon, scale_label, start, end)

    with ThreadPoolExecutor(max_workers=7) as pool:
        # Core indicators (mandatory)
        f_veg    = pool.submit(_fetch_vegetation,   lat, lon, start, end, cal)
        f_opt    = pool.submit(_fetch_optical,      lat, lon, start, end, cal)
        f_lst    = pool.submit(_fetch_lst,          lat, lon, start, end, cal)
        f_precip = pool.submit(_fetch_precip,       lat, lon, start, end, cal)
        # Optional indicators (run in parallel; failures → null, not 504)
        f_et     = pool.submit(_fetch_et,           lat, lon, start, end, cal)
        f_sm     = pool.submit(_fetch_soil_moisture, lat, lon, start, end, cal)
        f_static = pool.submit(extract_static_context, lat, lon)

        # R-007: bound the wait so a hung GEE call never freezes the uvicorn worker.
        try:
            veg_data    = f_veg.result(timeout=GEE_TIMEOUT)
            opt_data    = f_opt.result(timeout=GEE_TIMEOUT)
            lst_data    = f_lst.result(timeout=GEE_TIMEOUT)
            precip_data = f_precip.result(timeout=GEE_TIMEOUT)
        except FutureTimeoutError:
            for f in [f_veg, f_opt, f_lst, f_precip, f_et, f_sm, f_static]:
                f.cancel()
            raise TimeoutError(
                f"Earth Engine no respondió en {GEE_TIMEOUT}s. "
                "El servicio puede estar saturado; reintente en un momento."
            )

        # Optional — collect with shorter timeout; log but don't fail
        def _safe_result(future, name):
            try:
                return future.result(timeout=GEE_OPT_TIMEOUT)
            except Exception as exc:
                logger.warning("Optional fetch '%s' failed: %s", name, exc)
                return None

        et_data     = _safe_result(f_et,     "et")
        sm_data     = _safe_result(f_sm,     "soil_moisture")
        static_data = _safe_result(f_static, "static_context")

    # --- Measured indices ---
    ndvi_hist  = veg_data["hist_stats"]["NDVI"]
    lst_hist   = lst_data["hist_stats"]["LST"]

    # Helper: date of the most recent observation in a series
    def _cur_date(series_dict, key):
        s = series_dict.get(key, [])
        return s[-1]["date"] if s else None

    ndvi  = _summarize(veg_data["cur_series"]["NDVI"],  veg_data["recent_series"]["NDVI"],
                       ndvi_hist, veg_data["monthly_clim"]["NDVI"],  "ndvi")
    evi   = _summarize(veg_data["cur_series"]["EVI"],   veg_data["recent_series"]["EVI"],
                       veg_data["hist_stats"]["EVI"],  veg_data["monthly_clim"]["EVI"],   "evi")
    savi  = _summarize(opt_data["cur_series"]["SAVI"],  opt_data["recent_series"]["SAVI"],
                       opt_data["hist_stats"]["SAVI"],  opt_data["monthly_clim"]["SAVI"],  "savi")
    ndwi  = _summarize(opt_data["cur_series"]["NDWI"],  opt_data["recent_series"]["NDWI"],
                       opt_data["hist_stats"]["NDWI"],  opt_data["monthly_clim"]["NDWI"],  "ndwi")
    mndwi = _summarize(opt_data["cur_series"]["MNDWI"], opt_data["recent_series"]["MNDWI"],
                       opt_data["hist_stats"]["MNDWI"], opt_data["monthly_clim"]["MNDWI"], "mndwi")
    nbr   = _summarize(opt_data["cur_series"]["NBR"],   opt_data["recent_series"]["NBR"],
                       opt_data["hist_stats"]["NBR"],   opt_data["monthly_clim"]["NBR"],   "nbr")
    lst   = _summarize(lst_data["cur_series"]["LST"],   lst_data["recent_series"]["LST"],
                       lst_hist,                        lst_data["monthly_clim"]["LST"],   "lst")
    lst_night = _summarize(lst_data["cur_series"]["LST_Night"],   lst_data["recent_series"]["LST_Night"],
                           lst_data["hist_stats"]["LST_Night"],   lst_data["monthly_clim"]["LST_Night"],
                           "lst_night")

    # --- Derived indices ---
    vci_val = _vci(ndvi["current"], ndvi_hist["min"], ndvi_hist["max"])
    tci_val = _tci(lst["current"],  lst_hist["min"],  lst_hist["max"])
    vhi_val = round(0.5 * vci_val + 0.5 * tci_val, 4) if (vci_val and tci_val) else None

    vci_clim = _derived_clim(veg_data["monthly_clim"]["NDVI"], ndvi_hist["min"], ndvi_hist["max"], invert=False)
    tci_clim = _derived_clim(lst_data["monthly_clim"]["LST"],  lst_hist["min"],  lst_hist["max"],  invert=True)
    vhi_clim = _vhi_clim(vci_clim, tci_clim)

    vci_recent = _vci_series(veg_data["recent_series"]["NDVI"], ndvi_hist["min"], ndvi_hist["max"])
    tci_recent = _tci_series(lst_data["recent_series"]["LST"],  lst_hist["min"],  lst_hist["max"])
    vhi_recent = _vhi_series(vci_recent, tci_recent)

    # R-005: use mean=None/std=None so that when monthly_clim is unavailable
    # (e.g. hmax==hmin pixel → _derived_clim returns {}), _summarize falls back
    # to None → z_score=None → anomaly_class="sin_datos" instead of a phantom z-score.
    vci_alltime = {"mean": None, "std": None, "min": 0.0, "max": 1.0, "count": 0}
    tci_alltime = {"mean": None, "std": None, "min": 0.0, "max": 1.0, "count": 0}
    vhi_alltime = {"mean": None, "std": None, "min": 0.0, "max": 1.0, "count": 0}

    ndvi_cur_date = _cur_date(veg_data["cur_series"], "NDVI")
    lst_cur_date  = _cur_date(lst_data["cur_series"], "LST")

    vci = _summarize([], vci_recent, vci_alltime, vci_clim, "vci",
                     current_override=vci_val, current_date_override=ndvi_cur_date)
    tci = _summarize([], tci_recent, tci_alltime, tci_clim, "tci",
                     current_override=tci_val, current_date_override=lst_cur_date)
    vhi = _summarize([], vhi_recent, vhi_alltime, vhi_clim, "vhi",
                     current_override=vhi_val, current_date_override=ndvi_cur_date)

    # --- Optional: ET (Evapotranspiración) ---
    et = None
    if et_data:
        try:
            et = _summarize(
                et_data["cur_series"]["ET"],  et_data["recent_series"]["ET"],
                et_data["hist_stats"]["ET"],  et_data["monthly_clim"]["ET"],
                "et",
            )
        except Exception as exc:
            logger.warning("ET summarize failed: %s", exc)

    # --- Optional: Soil Moisture (SMAP) ---
    sm = None
    if sm_data:
        try:
            sm_hist = sm_data["hist_stats"]["ssm"]
            sm = _summarize(
                sm_data["cur_series"]["ssm"],  sm_data["recent_series"]["ssm"],
                sm_hist,                        sm_data["monthly_clim"]["ssm"],
                "sm",
            )
        except Exception as exc:
            logger.warning("SM summarize failed: %s", exc)

    indices = {
        "ndvi": ndvi, "evi": evi, "savi": savi,
        "ndwi": ndwi, "mndwi": mndwi, "vci": vci, "vhi": vhi,
        "lst": lst, "lst_night": lst_night, "tci": tci, "nbr": nbr,
        "precipitation": precip_data,
        # Optional — may be None if GEE fetch failed
        "et":  et,
        "sm":  sm,
    }

    indicator = _situation(indices)
    socio     = _socio(lat, lon, scale_label, indices)

    result = {
        "meta": {
            "lat": lat, "lon": lon, "scale": scale_label,
            "period_start": start, "period_end": end,
            "calendar_months": list(cal),
            "region": socio["region"], "season": socio["season"],
            "hist_baseline": f"{HIST_START[:4]}–{HIST_END[:4]} (20 años MODIS)",
            "recent_months": RECENT_MONTHS,
            "current_month": date.today().month,
            "current_month_name": MONTH_NAMES[date.today().month],
        },
        "indices":             indices,
        "situation_indicator": indicator,
        "socioeconomic":       socio,
        "static_context":      static_data,   # {hand_m, elevation_m, slope_deg} or None
    }

    # R-006: store in cache (elapsed_seconds is added by main.py after this call)
    with _cache_lock:
        _analysis_cache[cache_key] = result
    return result
