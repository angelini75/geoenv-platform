"""
Earth Engine initialization and low-level helpers.
Uses a service account key mounted at GOOGLE_APPLICATION_CREDENTIALS.
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

    creds_path = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS", "/app/secrets/credentials.json"
    )
    project = os.environ.get("GEE_PROJECT", "ee-angelini75")

    with open(creds_path) as f:
        creds_data = json.load(f)

    credentials = ee.ServiceAccountCredentials(
        email=creds_data["client_email"],
        key_file=creds_path,
    )
    ee.Initialize(credentials, project=project)
    _initialized = True
    logger.info("Earth Engine initialized — project=%s", project)


def point_buffer(lat: float, lon: float, meters: int = 500) -> ee.Geometry:
    return ee.Geometry.Point([lon, lat]).buffer(meters)


def scale_modis_ndvi(img: ee.Image) -> ee.Image:
    return img.select(["NDVI", "EVI"]).multiply(0.0001).copyProperties(
        img, ["system:time_start"]
    )


def scale_modis_surf_refl(img: ee.Image) -> ee.Image:
    return img.select(["sur_refl_b02", "sur_refl_b06"]).multiply(0.0001).copyProperties(
        img, ["system:time_start"]
    )


def scale_modis_lst(img: ee.Image) -> ee.Image:
    """Convert LST from raw DN to Celsius: DN * 0.02 - 273.15"""
    lst = (
        img.select("LST_Day_1km")
        .multiply(0.02)
        .subtract(273.15)
        .rename("LST")
        .copyProperties(img, ["system:time_start"])
    )
    return lst


def add_ndwi_band(img: ee.Image) -> ee.Image:
    """NDWI (Gao 1996): (NIR - SWIR1) / (NIR + SWIR1) using MOD09A1 bands"""
    nir = img.select("sur_refl_b02")
    swir = img.select("sur_refl_b06")
    ndwi = nir.subtract(swir).divide(nir.add(swir)).rename("NDWI").copyProperties(
        img, ["system:time_start"]
    )
    return ndwi


def extract_series(collection: ee.ImageCollection, geometry: ee.Geometry, scale: int, band: str) -> list[dict]:
    """
    Returns [{date, value}, ...] sorted ascending.
    Uses a server-side FeatureCollection → single getInfo() call.
    """
    def to_feature(img):
        val = img.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geometry,
            scale=scale,
            maxPixels=1e6,
        ).get(band)
        return ee.Feature(None, {
            "date": img.date().format("YYYY-MM-dd"),
            "value": val,
        })

    fc = collection.map(to_feature)
    raw = fc.getInfo()
    result = []
    for feat in raw.get("features", []):
        props = feat.get("properties", {})
        if props.get("value") is not None:
            result.append({"date": props["date"], "value": round(props["value"], 5)})
    return sorted(result, key=lambda x: x["date"])


def extract_stats(collection: ee.ImageCollection, geometry: ee.Geometry, scale: int, band: str) -> dict:
    """
    Compute mean and stdDev for the entire collection at the geometry.
    Returns {mean, std, count}.
    """
    stats_img = collection.reduce(
        ee.Reducer.mean().combine(
            reducer2=ee.Reducer.stdDev(),
            sharedInputs=True,
        )
    )
    raw = stats_img.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=geometry,
        scale=scale,
        maxPixels=1e6,
    ).getInfo()

    mean_key = f"{band}_mean"
    std_key = f"{band}_stdDev"
    count = collection.size().getInfo()

    return {
        "mean": round(raw.get(mean_key, 0) or 0, 5),
        "std": round(raw.get(std_key, 0) or 0, 5),
        "count": count,
    }
