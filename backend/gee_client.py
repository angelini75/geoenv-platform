"""
Earth Engine initialization and low-level extraction helpers.
All multi-band operations use a single getInfo() call per collection.
"""
import ee
import json
import os
import logging

logger = logging.getLogger(__name__)
_initialized = False


def initialize():
    global _initialized
    if _initialized:
        return
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/app/secrets/credentials.json")
    project    = os.environ.get("GEE_PROJECT", "ee-angelini75")
    with open(creds_path) as f:
        creds_data = json.load(f)
    credentials = ee.ServiceAccountCredentials(email=creds_data["client_email"], key_file=creds_path)
    ee.Initialize(credentials, project=project)
    _initialized = True
    logger.info("Earth Engine initialized — project=%s", project)


def point_buffer(lat: float, lon: float, meters: int) -> ee.Geometry:
    return ee.Geometry.Point([lon, lat]).buffer(meters)


# ---------------------------------------------------------------------------
# MODIS scaling helpers
# ---------------------------------------------------------------------------

def scale_mod13q1(img: ee.Image) -> ee.Image:
    """MOD13Q1: NDVI + EVI × 0.0001"""
    return img.select(["NDVI", "EVI"]).multiply(0.0001).copyProperties(img, ["system:time_start"])


def scale_mod09a1(img: ee.Image) -> ee.Image:
    """MOD09A1: select all optical bands needed for NDWI/MNDWI/SAVI/NBR × 0.0001"""
    scaled = img.select(
        ["sur_refl_b01", "sur_refl_b02", "sur_refl_b04", "sur_refl_b06", "sur_refl_b07"]
    ).multiply(0.0001)

    red   = scaled.select("sur_refl_b01")
    nir   = scaled.select("sur_refl_b02")
    green = scaled.select("sur_refl_b04")
    swir1 = scaled.select("sur_refl_b06")
    swir2 = scaled.select("sur_refl_b07")

    ndwi  = nir.subtract(swir1).divide(nir.add(swir1)).rename("NDWI")
    mndwi = green.subtract(swir1).divide(green.add(swir1)).rename("MNDWI")
    savi  = nir.subtract(red).divide(nir.add(red).add(0.5)).multiply(1.5).rename("SAVI")
    nbr   = nir.subtract(swir2).divide(nir.add(swir2)).rename("NBR")

    return ee.Image([ndwi, mndwi, savi, nbr]).copyProperties(img, ["system:time_start"])


def scale_mod11a2(img: ee.Image) -> ee.Image:
    """MOD11A2: LST DN → Celsius"""
    return (
        img.select("LST_Day_1km")
        .multiply(0.02).subtract(273.15)
        .rename("LST")
        .copyProperties(img, ["system:time_start"])
    )


# ---------------------------------------------------------------------------
# Multi-band extraction (single getInfo() per call)
# ---------------------------------------------------------------------------

def extract_series(collection: ee.ImageCollection, geometry: ee.Geometry,
                   scale: int, bands: list[str]) -> dict[str, list[dict]]:
    """
    Returns {band: [{date, value}, ...]} for all bands in one getInfo() call.
    """
    def to_feature(img):
        values = img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=geometry,
            scale=scale, maxPixels=1e6
        )
        return ee.Feature(None, values.set("date", img.date().format("YYYY-MM-dd")))

    raw = collection.map(to_feature).getInfo()
    result = {b: [] for b in bands}
    for feat in raw.get("features", []):
        props = feat.get("properties", {})
        date  = props.get("date")
        if not date:
            continue
        for b in bands:
            val = props.get(b)
            if val is not None:
                result[b].append({"date": date, "value": round(val, 5)})
    for b in bands:
        result[b] = sorted(result[b], key=lambda x: x["date"])
    return result


def extract_monthly_climatology(collection: ee.ImageCollection, geometry: ee.Geometry,
                               scale: int, bands: list[str]) -> dict[str, dict]:
    """
    Compute mean and std for each calendar month (1–12) in a single getInfo() call.
    Returns {band: {month_int: {"mean": float, "std": float}}}

    All 12 monthly reductions are combined into one multi-band image so only
    one network round-trip is needed regardless of the number of months or bands.
    """
    monthly_imgs = []
    for month in range(1, 13):
        m_col  = collection.filter(ee.Filter.calendarRange(month, month, "month"))
        m_stat = m_col.reduce(
            ee.Reducer.mean().combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
        )
        old_names = []
        new_names = []
        for b in bands:
            old_names += [f"{b}_mean", f"{b}_stdDev"]
            new_names += [f"{b}_m{month:02d}_mu", f"{b}_m{month:02d}_sd"]
        monthly_imgs.append(m_stat.select(old_names, new_names))

    combined = ee.Image.cat(monthly_imgs)
    raw = combined.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geometry,
        scale=scale, maxPixels=1e6
    ).getInfo()

    result = {}
    for b in bands:
        result[b] = {}
        for month in range(1, 13):
            mu = raw.get(f"{b}_m{month:02d}_mu")
            sd = raw.get(f"{b}_m{month:02d}_sd")
            result[b][month] = {
                "mean": round(mu, 5) if mu is not None else None,
                "std":  round(sd, 5) if sd is not None else None,
            }
    return result


def scale_mod16a2(img: ee.Image) -> ee.Image:
    """MOD16A2GF: ET × 0.1 → kg/m²/8day (≈ mm per 8-day period). Fill -28672 → mask."""
    return (
        img.select("ET")
        .updateMask(img.select("ET").gt(-28000))   # mask fill value
        .multiply(0.1)
        .copyProperties(img, ["system:time_start"])
    )


def scale_smap(img: ee.Image) -> ee.Image:
    """SMAP 10KM: surface soil moisture (ssm) already in mm — just select + mask."""
    return (
        img.select("ssm")
        .updateMask(img.select("ssm").gt(0))
        .copyProperties(img, ["system:time_start"])
    )


def extract_static_context(lat: float, lon: float) -> dict:
    """
    Extract time-invariant topographic values at a point:
      - HAND   : Height Above Nearest Drainage (MERIT Hydro, 90 m)
      - elevation: SRTM 30 m elevation (m)
      - slope  : terrain slope in degrees (from SRTM)
    Returns {hand_m, elevation_m, slope_deg} — any value may be None on error.
    """
    geom = ee.Geometry.Point([lon, lat])

    hand_img = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")
    dem_img  = ee.Image("USGS/SRTMGL1_003")
    slp_img  = ee.Terrain.slope(dem_img)

    combined = hand_img.rename("hand").addBands(
        dem_img.rename("elev")
    ).addBands(
        slp_img.rename("slope")
    )

    raw = combined.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geom.buffer(500),
        scale=90,
        maxPixels=1e6,
    ).getInfo()

    def _r(v, decimals=1):
        return round(v, decimals) if v is not None else None

    return {
        "hand_m":       _r(raw.get("hand"), 1),
        "elevation_m":  _r(raw.get("elev"), 0),
        "slope_deg":    _r(raw.get("slope"), 2),
    }


def extract_stats(collection: ee.ImageCollection, geometry: ee.Geometry,
                  scale: int, bands: list[str]) -> dict[str, dict]:
    """
    Returns {band: {mean, std, min, max, count}} for all bands in one getInfo() call.
    """
    stats_img = collection.reduce(
        ee.Reducer.mean()
        .combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True)
        .combine(reducer2=ee.Reducer.min(),    sharedInputs=True)
        .combine(reducer2=ee.Reducer.max(),    sharedInputs=True)
    )
    raw   = stats_img.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=geometry,
        scale=scale, maxPixels=1e6
    ).getInfo()
    count = collection.size().getInfo()

    result = {}
    for b in bands:
        result[b] = {
            "mean":  round(raw.get(f"{b}_mean",   0) or 0, 5),
            "std":   round(raw.get(f"{b}_stdDev", 0) or 0, 5),
            "min":   round(raw.get(f"{b}_min",    0) or 0, 5),
            "max":   round(raw.get(f"{b}_max",    0) or 0, 5),
            "count": count,
        }
    return result
