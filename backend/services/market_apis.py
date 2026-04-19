"""
External market data APIs for GeoEnv Platform.

Sources:
  dolarapi.com — USD Oficial / CCL / MEP / Blue (no auth required)
  BCRA         — Tasa BADLAR  https://api.bcra.gob.ar/estadisticas/v1.0/
  datos.gob.ar — FAS granos, IPC, CER, avance siembra/cosecha
                 https://apis.datos.gob.ar/series/api/

All responses are cached in-memory for CACHE_TTL seconds.
Failures on any source are logged and returned as null (never raise).
"""
import logging
import threading
from datetime import date, timedelta, datetime

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)

CACHE_TTL  = 4 * 3600          # 4 h — market data changes daily
_cache: TTLCache = TTLCache(maxsize=16, ttl=CACHE_TTL)
_lock  = threading.Lock()

DOLAR_API  = "https://dolarapi.com/v1/dolares"
BCRA_STATS = "https://api.bcra.gob.ar/estadisticas/v1.0/DatosVariable"
DATOS_BASE = "https://apis.datos.gob.ar/series/api"
HEADERS    = {"User-Agent": "GeoEnv-Platform/3.0 (indicadores.soildecisions.com)"}
TIMEOUT    = 8.0   # seconds

# ── datos.gob.ar verified series IDs ──────────────────────────────────────
# FAS teórico MAGyP ($/tn, precio de pizarra al comprador)
_SERIES_GRANOS = {
    "soja_fas":     "170.1_DR_1060_0_12",
    "maiz_fas":     "170.1_DR_1090_0_12",
    "trigo_fas":    "170.1_DR_1100_0_12",
    "girasol_fas":  "170.1_DR_1080_0_12",
}
# Macro series
_SERIES_MACRO = {
    "ipc_ng":       "148.3_INIVELNAL_DICI_M_26",   # IPC Nivel General INDEC (var m/m)
    "cer":          "174.1_CER_0_0_26",              # CER BCRA (índice base 2002=1)
}
# Avance de siembra (% sobre área estimada) — MAGyP
# These series IDs cover the current crop campaign progress
_SERIES_SIEMBRA = {
    "soja_siembra_pct":    "37.3_VN_2800_0_0",
    "maiz_siembra_pct":    "37.3_VN_2810_0_0",
    "trigo_siembra_pct":   "37.3_VN_2820_0_0",
    "girasol_siembra_pct": "37.3_VN_2830_0_0",
}

# BCRA monetary variable IDs
_BCRA_VAR_BADLAR = 27    # Tasa BADLAR bancos privados (% n.a.)


# ── HTTP helper ────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None) -> dict | list | None:
    try:
        r = httpx.get(url, params=params, headers=HEADERS,
                      timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
        return None


# ── Tipos de cambio — dolarapi.com ─────────────────────────────────────────

def _fetch_fx() -> dict:
    """
    Fetch all FX rates from dolarapi.com.
    Returns {oficial, ccl, mep, blue} — each as {compra, venta} or None.
    """
    data = _get(DOLAR_API)
    result = {"oficial": None, "ccl": None, "mep": None, "blue": None}
    if not data or not isinstance(data, list):
        return result

    key_map = {
        "oficial": "oficial",
        "contadoconliqui": "ccl",
        "mep": "mep",
        "blue": "blue",
    }
    for item in data:
        name = item.get("nombre", "").lower().replace(" ", "").replace("ó", "o")
        key = key_map.get(name)
        if key:
            result[key] = {
                "compra": item.get("compra"),
                "venta":  item.get("venta"),
            }
    return result


# ── BCRA — tasa BADLAR ─────────────────────────────────────────────────────

def _fetch_badlar() -> dict | None:
    """Fetch latest BADLAR rate from BCRA estadísticas API."""
    today    = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=10)).isoformat()
    data = _get(f"{BCRA_STATS}/{_BCRA_VAR_BADLAR}/{week_ago}/{today}")
    if data and data.get("results"):
        latest = data["results"][-1]
        return {"fecha": latest.get("fecha"), "valor": latest.get("valor")}
    return None


# ── datos.gob.ar — series de tiempo ───────────────────────────────────────

def _fetch_series_batch(series_ids: dict[str, str], limit: int = 3) -> dict[str, dict | None]:
    """
    Fetch last N data points for a batch of series from datos.gob.ar.
    Returns {alias: {fecha, valor}} or {alias: None} on missing data.
    """
    result = {k: None for k in series_ids}
    ids_param = ",".join(series_ids.values())
    data = _get(
        f"{DATOS_BASE}/series/",
        params={"ids": ids_param, "limit": limit, "sort": "desc", "format": "json"},
    )
    if not data:
        return result

    meta = data.get("meta", [])
    rows = data.get("data", [])
    keys = list(series_ids.keys())

    for i, _meta_item in enumerate(meta):
        if i >= len(keys):
            break
        key = keys[i]
        for row in rows:
            val = row[i + 1] if len(row) > i + 1 else None
            if val is not None:
                result[key] = {"fecha": row[0], "valor": round(float(val), 2)}
                break
    return result


# ── Public entry point ─────────────────────────────────────────────────────

def get_market_data() -> dict:
    """
    Return consolidated market data dict with:
      fx      : {oficial, ccl, mep, blue} — each {compra, venta}
      granos  : {soja_fas, maiz_fas, trigo_fas, girasol_fas} — $/tn
      macro   : {badlar, ipc_ng, cer}
      siembra : {soja_siembra_pct, maiz_siembra_pct, ...} — % avance campaña actual

    Cached for CACHE_TTL seconds. Never raises — failed sources return None.
    """
    with _lock:
        if "market" in _cache:
            return _cache["market"]

    out = {
        "_timestamp": datetime.now().isoformat(timespec="seconds"),
        "_fuentes":   ["dolarapi.com", "BCRA estadísticas", "datos.gob.ar/series"],
        "fx":         {"oficial": None, "ccl": None, "mep": None, "blue": None},
        "granos":     {k: None for k in _SERIES_GRANOS},
        "macro":      {"badlar": None, "ipc_ng": None, "cer": None},
        "siembra":    {k: None for k in _SERIES_SIEMBRA},
        "_errors":    [],
    }

    # 1. FX rates (dolarapi.com — one call for all types)
    try:
        out["fx"] = _fetch_fx()
    except Exception as e:
        out["_errors"].append(f"fx: {e}")

    # 2. BADLAR (BCRA estadísticas)
    try:
        out["macro"]["badlar"] = _fetch_badlar()
    except Exception as e:
        out["_errors"].append(f"badlar: {e}")

    # 3. Granos FAS (datos.gob.ar)
    try:
        granos = _fetch_series_batch(_SERIES_GRANOS, limit=2)
        out["granos"] = granos
    except Exception as e:
        out["_errors"].append(f"granos: {e}")

    # 4. Macro series: IPC + CER (datos.gob.ar)
    try:
        macro_series = _fetch_series_batch(_SERIES_MACRO, limit=2)
        out["macro"]["ipc_ng"] = macro_series.get("ipc_ng")
        out["macro"]["cer"]    = macro_series.get("cer")
    except Exception as e:
        out["_errors"].append(f"macro_series: {e}")

    # 5. Avance de siembra (datos.gob.ar — may fail if outside campaign window)
    try:
        siembra = _fetch_series_batch(_SERIES_SIEMBRA, limit=2)
        out["siembra"] = siembra
    except Exception as e:
        out["_errors"].append(f"siembra: {e}")

    if out["_errors"]:
        logger.warning("market_apis errors: %s", out["_errors"])

    with _lock:
        _cache["market"] = out
    return out
