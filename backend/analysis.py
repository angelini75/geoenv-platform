"""
Environmental analysis engine — Argentina.

Data sources:
  MOD13Q1  : NDVI, EVI               (16-day, 250 m)
  MOD09A1  : NDWI, MNDWI, SAVI, NBR  (8-day, 500 m)
  MOD11A2  : LST                      (8-day, 1 km)
  CHIRPS   : Precipitation            (daily, 5.5 km)

Derived: VCI, TCI, VHI  (from NDVI + LST historical min/max)

Baseline: 2004-2024 (20 years of MODIS).
Z-scores are SEASONALLY ADJUSTED: each index is compared to the
climatological mean for the SAME calendar month, not to an annual mean.
Chart series: last 3 years of monthly OHLC candlesticks (per-month z-scores).
GEE fetches run in parallel threads to minimize latency.
"""
import ee
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from collections import defaultdict

from gee_client import (
    point_buffer,
    scale_mod13q1, scale_mod09a1, scale_mod11a2,
    extract_series, extract_stats, extract_monthly_climatology,
)

logger = logging.getLogger(__name__)

SCALE_DAYS = {
    "1w": 7, "2w": 14, "1m": 30, "2m": 60,
    "3m": 90, "6m": 180, "1y": 365,
}
HIST_START  = "2004-01-01"   # 20-year MODIS baseline
HIST_END    = "2024-12-31"
CHART_YEARS = 3              # monthly candle chart spans last N years

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
    if None in (value, mean) or mean == 0:
        return None
    return round((value - mean) / abs(mean) * 100, 2)


def _classify(z) -> str:
    if z is None:
        return "Sin datos"
    a = abs(z)
    if a < 1.0:
        return "Normal"
    if a < 1.5:
        return "Anomalía moderada"
    return "Anomalía extrema"


def _calendar_months(start: date, end: date) -> tuple[int, int]:
    months = set()
    cur = start.replace(day=1)
    while cur <= end:
        months.add(cur.month)
        cur += relativedelta(months=1)
    months = sorted(months)
    return months[0], months[-1]


def _direction(index: str, o: float, c: float) -> str:
    if c > o:
        labels = {
            "ndvi": "Alcista (recuperación)", "evi": "Alcista (recuperación)",
            "savi": "Alcista (recuperación)", "ndwi": "Alcista (recarga hídrica)",
            "mndwi": "Alcista (agua superficial +)", "vci": "Alcista (condición vegetal +)",
            "tci": "Alcista (condición térmica +)", "vhi": "Alcista (salud ecosistema +)",
            "lst": "Alcista (estrés térmico)", "nbr": "Alcista (biomasa +)",
        }
        return labels.get(index, "Alcista")
    elif c < o:
        labels = {
            "ndvi": "Bajista (estrés/degradación)", "evi": "Bajista (estrés/degradación)",
            "savi": "Bajista (suelo expuesto +)", "ndwi": "Bajista (déficit hídrico)",
            "mndwi": "Bajista (agua superficial −)", "vci": "Bajista (sequía agrícola)",
            "tci": "Bajista (estrés térmico +)", "vhi": "Bajista (deterioro ecosistema)",
            "lst": "Bajista (enfriamiento)", "nbr": "Bajista (degradación/fuego)",
        }
        return labels.get(index, "Bajista")
    return "Neutro"


# ---------------------------------------------------------------------------
# Monthly OHLC candlesticks (seasonally adjusted z-scores per candle)
# ---------------------------------------------------------------------------

def _monthly_candles(series: list[dict], monthly_clim: dict,
                     index: str) -> list[dict]:
    """
    Group a time-series into monthly OHLC candles.
    Z-score of each candle close uses THAT month's climatology (seasonal adjustment).
    Period format: YYYY-MM-DD (first day of month) — TradingView compatible.
    """
    if not series:
        return []

    groups: dict[str, list] = defaultdict(list)
    for pt in series:
        groups[pt["date"][:7]].append(pt["value"])

    out = []
    for ym in sorted(groups):
        vals = groups[ym]
        if not vals:
            continue
        month = int(ym[5:7])
        o, c, h, lo = vals[0], vals[-1], max(vals), min(vals)

        # Per-month climatology for seasonal z-score
        m_stats = (monthly_clim or {}).get(month, {})
        z = _zscore(c, m_stats.get("mean"), m_stats.get("std"))

        out.append({
            "period":         ym + "-01",
            "open":           round(o,  5),
            "close":          round(c,  5),
            "high":           round(h,  5),
            "low":            round(lo, 5),
            "range":          round(h - lo, 5),
            "direction":      _direction(index, o, c),
            "z_close":        z,
            "anomaly_class":  _classify(z),
            "n_observations": len(vals),
        })
    return out


# ---------------------------------------------------------------------------
# Summarize one index using seasonal (per-month) climatology
# ---------------------------------------------------------------------------

def _summarize(cur_series: list[dict], chart_series: list[dict],
               hist_alltime: dict, monthly_clim: dict,
               index: str, scale: str,
               current_override=None) -> dict:
    """
    hist_alltime  : {mean, std, min, max, count} — used for min/max (VCI/TCI bounds)
    monthly_clim  : {month_int: {"mean": x, "std": y}} — seasonally adjusted z-score
    """
    val = (current_override if current_override is not None
           else (cur_series[-1]["value"] if cur_series else None))

    cur_month = date.today().month
    m_stats   = (monthly_clim or {}).get(cur_month, {})
    clim_mean = m_stats.get("mean") or hist_alltime.get("mean")
    clim_std  = m_stats.get("std")  or hist_alltime.get("std")

    z = _zscore(val, clim_mean, clim_std)

    candles = _monthly_candles(chart_series, monthly_clim, index)

    # Compact seasonal curve: {month_int: mean} for LLM + frontend
    seasonal_means = {
        m: (monthly_clim[m]["mean"] if monthly_clim and m in monthly_clim else None)
        for m in range(1, 13)
    }

    return {
        "current":         round(val, 5) if val is not None else None,
        "hist_mean":       clim_mean,          # current-month seasonal mean
        "hist_std":        clim_std,
        "hist_min":        hist_alltime.get("min"),
        "hist_max":        hist_alltime.get("max"),
        "z_score":         z,
        "pct_deviation":   _pct_dev(val, clim_mean),
        "anomaly_class":   _classify(z),
        "candlesticks":    candles,
        "seasonal_curve":  seasonal_means,      # 12-month climatology means
        "monthly_clim":    monthly_clim,        # full mean+std per month (for reporter)
        "n_observations":  len(cur_series),
        "hist_n_images":   hist_alltime.get("count", 0),
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
    """Propagate monthly climatology through VCI or TCI formula."""
    d = hmax - hmin
    if d == 0:
        return {}
    result = {}
    for m, stats in (base_clim or {}).items():
        mu = stats.get("mean")
        sd = stats.get("std")
        if mu is None:
            result[m] = {"mean": None, "std": None}
        else:
            vci_mu = (hmax - mu) / d if invert else (mu - hmin) / d
            result[m] = {
                "mean": round(max(0.0, min(1.0, vci_mu)), 4),
                "std":  round(sd / d, 4) if sd else None,
            }
    return result


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
            result[m] = {
                "mean": round(0.5 * v["mean"] + 0.5 * t["mean"], 4),
                "std":  round(0.5 * (v.get("std") or 0) + 0.5 * (t.get("std") or 0), 4),
            }
    return result


# ---------------------------------------------------------------------------
# GEE fetch functions (parallel threads)
# ---------------------------------------------------------------------------

def _chart_range():
    end = date.today()
    return (end - relativedelta(years=CHART_YEARS)).isoformat(), end.isoformat()


def _fetch_vegetation(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 500)
    col   = ee.ImageCollection("MODIS/061/MOD13Q1").map(scale_mod13q1)
    hist  = col.filterDate(HIST_START, HIST_END)
    cs, ce = _chart_range()
    bands = ["NDVI", "EVI"]
    return {
        "cur_series":   extract_series(col.filterDate(start, end), geom, 250, bands),
        "hist_stats":   extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                      geom, 250, bands),
        "chart_series": extract_series(col.filterDate(cs, ce), geom, 250, bands),
        "monthly_clim": extract_monthly_climatology(hist, geom, 250, bands),
    }


def _fetch_optical(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 1000)
    col   = ee.ImageCollection("MODIS/061/MOD09A1").map(scale_mod09a1)
    hist  = col.filterDate(HIST_START, HIST_END)
    cs, ce = _chart_range()
    bands = ["NDWI", "MNDWI", "SAVI", "NBR"]
    return {
        "cur_series":   extract_series(col.filterDate(start, end), geom, 500, bands),
        "hist_stats":   extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                      geom, 500, bands),
        "chart_series": extract_series(col.filterDate(cs, ce), geom, 500, bands),
        "monthly_clim": extract_monthly_climatology(hist, geom, 500, bands),
    }


def _fetch_lst(lat, lon, start, end, cal):
    geom  = point_buffer(lat, lon, 2000)
    col   = ee.ImageCollection("MODIS/061/MOD11A2").map(scale_mod11a2)
    hist  = col.filterDate(HIST_START, HIST_END)
    cs, ce = _chart_range()
    return {
        "cur_series":   extract_series(col.filterDate(start, end), geom, 1000, ["LST"]),
        "hist_stats":   extract_stats(hist.filter(ee.Filter.calendarRange(cal[0], cal[1], "month")),
                                      geom, 1000, ["LST"]),
        "chart_series": extract_series(col.filterDate(cs, ce), geom, 1000, ["LST"]),
        "monthly_clim": extract_monthly_climatology(hist, geom, 1000, ["LST"]),
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
# Region / season / situation helpers
# ---------------------------------------------------------------------------

_REGIONS = {
    (-34, -22, -64, -53): "NEA/NOA",
    (-38, -30, -65, -57): "Pampas",
    (-45, -38, -72, -65): "Patagonia Norte",
    (-55, -45, -75, -63): "Patagonia Sur",
    (-34, -28, -70, -64): "Cuyo",
}


def _region(lat, lon):
    for (la, lb, lo, lob), name in _REGIONS.items():
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

    macro = ("Contexto macro estimado (Argentina 2025-2026): inflación ~70% i.a. · "
             "tipo de cambio oficial ~$1,100/USD · costos de insumos agrícolas dolarizados · "
             "poder adquisitivo rural bajo presión estructural.")

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
        "water": water, "precipitation": precip_note, "thermal": thermal, "macro": macro,
        "causality_chain": causality,
        "assumptions": [
            "Baseline histórico MODIS 2004-2024 (20 años). Z-scores ajustados estacionalmente por mes calendario.",
            "Datos macroeconómicos son proxies estimados (INDEC/BCRA proyecciones 2025-2026).",
            "NDWI formula Gao 1996 (NIR-SWIR): sensible a humedad de canopeo.",
            "VCI/TCI/VHI climatologías derivadas de las curvas estacionales de NDVI y LST.",
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
    days       = SCALE_DAYS[scale_label]
    end_date   = date.today()
    start_date = end_date - timedelta(days=days)
    start      = start_date.isoformat()
    end        = end_date.isoformat()
    cal        = _calendar_months(start_date, end_date)

    logger.info("Analysis: (%.4f, %.4f) scale=%s %s→%s", lat, lon, scale_label, start, end)

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_veg    = pool.submit(_fetch_vegetation, lat, lon, start, end, cal)
        f_opt    = pool.submit(_fetch_optical,    lat, lon, start, end, cal)
        f_lst    = pool.submit(_fetch_lst,        lat, lon, start, end, cal)
        f_precip = pool.submit(_fetch_precip,     lat, lon, start, end, cal)
        veg_data, opt_data, lst_data, precip_data = (
            f_veg.result(), f_opt.result(), f_lst.result(), f_precip.result()
        )

    # --- Measured indices ---
    ndvi_hist  = veg_data["hist_stats"]["NDVI"]
    lst_hist   = lst_data["hist_stats"]["LST"]

    ndvi  = _summarize(veg_data["cur_series"]["NDVI"],  veg_data["chart_series"]["NDVI"],
                       ndvi_hist, veg_data["monthly_clim"]["NDVI"],  "ndvi",  scale_label)
    evi   = _summarize(veg_data["cur_series"]["EVI"],   veg_data["chart_series"]["EVI"],
                       veg_data["hist_stats"]["EVI"],  veg_data["monthly_clim"]["EVI"],   "evi",   scale_label)
    savi  = _summarize(opt_data["cur_series"]["SAVI"],  opt_data["chart_series"]["SAVI"],
                       opt_data["hist_stats"]["SAVI"],  opt_data["monthly_clim"]["SAVI"],  "savi",  scale_label)
    ndwi  = _summarize(opt_data["cur_series"]["NDWI"],  opt_data["chart_series"]["NDWI"],
                       opt_data["hist_stats"]["NDWI"],  opt_data["monthly_clim"]["NDWI"],  "ndwi",  scale_label)
    mndwi = _summarize(opt_data["cur_series"]["MNDWI"], opt_data["chart_series"]["MNDWI"],
                       opt_data["hist_stats"]["MNDWI"], opt_data["monthly_clim"]["MNDWI"], "mndwi", scale_label)
    nbr   = _summarize(opt_data["cur_series"]["NBR"],   opt_data["chart_series"]["NBR"],
                       opt_data["hist_stats"]["NBR"],   opt_data["monthly_clim"]["NBR"],   "nbr",   scale_label)
    lst   = _summarize(lst_data["cur_series"]["LST"],   lst_data["chart_series"]["LST"],
                       lst_hist,                        lst_data["monthly_clim"]["LST"],   "lst",   scale_label)

    # --- Derived indices ---
    vci_val = _vci(ndvi["current"], ndvi_hist["min"], ndvi_hist["max"])
    tci_val = _tci(lst["current"],  lst_hist["min"],  lst_hist["max"])
    vhi_val = round(0.5 * vci_val + 0.5 * tci_val, 4) if (vci_val and tci_val) else None

    vci_clim = _derived_clim(veg_data["monthly_clim"]["NDVI"], ndvi_hist["min"], ndvi_hist["max"], invert=False)
    tci_clim = _derived_clim(lst_data["monthly_clim"]["LST"],  lst_hist["min"],  lst_hist["max"],  invert=True)
    vhi_clim = _vhi_clim(vci_clim, tci_clim)

    vci_chart = _vci_series(veg_data["chart_series"]["NDVI"], ndvi_hist["min"], ndvi_hist["max"])
    tci_chart = _tci_series(lst_data["chart_series"]["LST"],  lst_hist["min"],  lst_hist["max"])
    vhi_chart = _vhi_series(vci_chart, tci_chart)

    vci_alltime = {"mean": 0.5, "std": 0.25, "min": 0.0, "max": 1.0, "count": 0}
    tci_alltime = {"mean": 0.5, "std": 0.25, "min": 0.0, "max": 1.0, "count": 0}
    vhi_alltime = {"mean": 0.5, "std": 0.20, "min": 0.0, "max": 1.0, "count": 0}

    vci = _summarize([], vci_chart, vci_alltime, vci_clim, "vci", scale_label, current_override=vci_val)
    tci = _summarize([], tci_chart, tci_alltime, tci_clim, "tci", scale_label, current_override=tci_val)
    vhi = _summarize([], vhi_chart, vhi_alltime, vhi_clim, "vhi", scale_label, current_override=vhi_val)

    indices = {
        "ndvi": ndvi, "evi": evi, "savi": savi,
        "ndwi": ndwi, "mndwi": mndwi, "vci": vci, "vhi": vhi,
        "lst": lst, "tci": tci, "nbr": nbr,
        "precipitation": precip_data,
    }

    indicator = _situation(indices)
    socio     = _socio(lat, lon, scale_label, indices)

    return {
        "meta": {
            "lat": lat, "lon": lon, "scale": scale_label,
            "period_start": start, "period_end": end,
            "calendar_months": list(cal),
            "region": socio["region"], "season": socio["season"],
            "hist_baseline": f"{HIST_START[:4]}–{HIST_END[:4]} (20 años MODIS)",
            "chart_years": CHART_YEARS,
            "current_month": date.today().month,
            "current_month_name": MONTH_NAMES[date.today().month],
        },
        "indices":             indices,
        "situation_indicator": indicator,
        "socioeconomic":       socio,
    }
