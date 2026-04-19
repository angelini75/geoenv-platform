"""
External market data APIs for GeoEnv Platform.

Sources:
  BCRA  — Banco Central de la República Argentina
          https://api.bcra.gob.ar/estadisticascambiarias/v1.0/
  datos.gob.ar — Series de tiempo del gobierno argentino
          https://apis.datos.gob.ar/series/api/
  ambito.com  — Cotizaciones de referencia (dólar CCL / MEP)

All responses are cached in-memory for CACHE_TTL seconds.
Failures on any source are logged and returned as null (never raise).
"""
import logging
import threading
from datetime import date, timedelta, datetime

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)

CACHE_TTL = 4 * 3600          # 4 h — market data changes daily
_cache: TTLCache = TTLCache(maxsize=16, ttl=CACHE_TTL)
_lock  = threading.Lock()

BCRA_BASE  = "https://api.bcra.gob.ar/estadisticascambiarias/v1.0"
DATOS_BASE = "https://apis.datos.gob.ar/series/api"
HEADERS    = {"User-Agent": "GeoEnv-Platform/2.1 (indicadores.soildecisions.com)"}
TIMEOUT    = 8.0   # seconds


# ── HTTP helper ────────────────────────────────────────────────────────────

def _get(url: str) -> dict | list | None:
    try:
        r = httpx.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("HTTP error fetching %s: %s", url, exc)
        return None


# ── BCRA — tipos de cambio ──────────────────────────────────────────────────

def _bcra_usd_oficial() -> dict | None:
    """
    Fetch dólar tipo comprador BNA (oficial).
    Tries today first, falls back to last 4 weekdays (holidays/weekends).
    """
    for delta in range(0, 5):
        fecha = (date.today() - timedelta(days=delta)).isoformat()
        data = _get(f"{BCRA_BASE}/cotizaciones?moneda=DOL&fecha={fecha}")
        if not data:
            continue
        results = data.get("results", [])
        if results:
            row = results[-1]
            return {
                "fecha":  row.get("fecha", fecha),
                "compra": row.get("tipoCambioCompra"),
                "venta":  row.get("tipoCambioVenta"),
                "fuente": "BCRA BNA Com. A3500",
            }
    return None


def _bcra_principal_variables() -> dict:
    """
    Fetch key monetary variables from BCRA /estadisticas/v1.0/DatosVariable.
    Variable IDs:
      1  → Reservas internacionales del BCRA (mill. USD)
      4  → Tipo de cambio de referencia ($/USD)
      7  → Tasa de interés de política monetaria (% n.a.)
      27 → Tasa BADLAR bancos privados (% n.a.)
    """
    var_ids = {"usd_ref": 4, "tasa_politica": 7, "badlar": 27}
    result  = {}
    for name, var_id in var_ids.items():
        today = date.today().isoformat()
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        data = _get(
            f"https://api.bcra.gob.ar/estadisticas/v1.0/DatosVariable"
            f"/{var_id}/{week_ago}/{today}"
        )
        if data and data.get("results"):
            latest = data["results"][-1]
            result[name] = {
                "fecha": latest.get("fecha"),
                "valor": latest.get("valor"),
            }
        else:
            result[name] = None
    return result


# ── datos.gob.ar — series de tiempo ───────────────────────────────────────

# Series IDs verified from https://apis.datos.gob.ar/series/api/series/
# FAS teórico MAGyP ($/tn, precio de pizarra)
_SERIES = {
    "soja_fas":   "170.1_DR_1060_0_12",   # Soja FAS teórico
    "maiz_fas":   "170.1_DR_1090_0_12",   # Maíz FAS teórico
    "trigo_fas":  "170.1_DR_1100_0_12",   # Trigo pan FAS teórico
    "ipc_ng":     "148.3_INIVELNAL_DICI_M_26",  # IPC Nivel General (INDEC)
}


def _datos_series(series_ids: dict[str, str], limit: int = 3) -> dict:
    """Fetch last N values for each series from datos.gob.ar."""
    ids_param = ",".join(series_ids.values())
    url = (
        f"{DATOS_BASE}/series/?ids={ids_param}"
        f"&limit={limit}&sort=desc&format=json"
    )
    data = _get(url)
    result = {k: None for k in series_ids}
    if not data:
        return result

    # Response: {"data": [[date, val1, val2, ...], ...], "meta": [{"id": ...}, ...]}
    meta = data.get("meta", [])
    rows = data.get("data", [])
    id_to_key = {m["id"]: k for k, m in zip(series_ids.keys(), meta)}

    for i, meta_item in enumerate(meta):
        key = list(series_ids.keys())[i]
        # Pick last non-null row for this column
        for row in rows:
            val = row[i + 1] if len(row) > i + 1 else None
            if val is not None:
                result[key] = {"fecha": row[0], "valor": round(val, 2)}
                break

    return result


# ── Public entry point ─────────────────────────────────────────────────────

def get_market_data() -> dict:
    """
    Return consolidated market data dict.
    Cached for CACHE_TTL seconds. Never raises — failed sources return None.
    """
    with _lock:
        if "market" in _cache:
            return _cache["market"]

    out = {
        "_timestamp": datetime.now().isoformat(timespec="seconds"),
        "_fuentes":   ["BCRA", "datos.gob.ar/series"],
        "usd_oficial": None,
        "bcra_vars":   {},
        "granos":      {},
        "macro_series": {},
        "_errors":     [],
    }

    # 1. Dólar oficial BNA
    try:
        out["usd_oficial"] = _bcra_usd_oficial()
    except Exception as e:
        out["_errors"].append(f"usd_oficial: {e}")

    # 2. Variables monetarias BCRA
    try:
        out["bcra_vars"] = _bcra_principal_variables()
    except Exception as e:
        out["_errors"].append(f"bcra_vars: {e}")

    # 3. Precios FAS granos + IPC
    try:
        series = _datos_series(_SERIES, limit=2)
        out["granos"] = {
            "soja_fas":  series.get("soja_fas"),
            "maiz_fas":  series.get("maiz_fas"),
            "trigo_fas": series.get("trigo_fas"),
        }
        out["macro_series"] = {
            "ipc_ng": series.get("ipc_ng"),
        }
    except Exception as e:
        out["_errors"].append(f"granos: {e}")

    if out["_errors"]:
        logger.warning("market_apis errors: %s", out["_errors"])

    with _lock:
        _cache["market"] = out
    return out
