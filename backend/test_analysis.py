"""
Unit tests for analysis.py statistical helpers.

Run with:  pytest backend/test_analysis.py -v
(No GEE credentials needed — these test pure-Python functions only.)
"""
import math
import sys
import os

# Allow import without full GEE initialisation
sys.path.insert(0, os.path.dirname(__file__))

from analysis import (
    _zscore, _pct_dev, _classify,
    _monthly_candles, _derived_clim, _vhi_clim,
    _vci, _tci,
)


# ── _zscore ───────────────────────────────────────────────────────────────────

def test_zscore_positive():
    assert _zscore(10, 8, 2) == 1.0

def test_zscore_negative():
    assert _zscore(6, 8, 2) == -1.0

def test_zscore_zero_std_returns_none():
    assert _zscore(10, 8, 0) is None

def test_zscore_none_value_returns_none():
    assert _zscore(None, 8, 2) is None

def test_zscore_none_mean_returns_none():
    assert _zscore(10, None, 2) is None

def test_zscore_none_std_returns_none():
    assert _zscore(10, 8, None) is None


# ── _pct_dev ──────────────────────────────────────────────────────────────────

def test_pct_dev_normal():
    assert _pct_dev(12, 10) == 20.0

def test_pct_dev_negative():
    assert _pct_dev(8, 10) == -20.0

def test_pct_dev_none_value():
    assert _pct_dev(None, 10) is None

def test_pct_dev_none_mean():
    assert _pct_dev(10, None) is None

def test_pct_dev_zero_mean():
    # R-010: mean == 0 → None (would divide by zero)
    assert _pct_dev(5, 0) is None

def test_pct_dev_near_zero_mean():
    # R-010: |mean| < 0.01 → None (prevents explosion: e.g. NDWI ~ 0.001 in arid zones)
    assert _pct_dev(0.05, 0.005) is None
    assert _pct_dev(0.05, -0.005) is None

def test_pct_dev_at_threshold():
    # |mean| == 0.01 is still below threshold → None
    assert _pct_dev(0.05, 0.01) is None
    # |mean| == 0.011 is above threshold → returns a value
    result = _pct_dev(0.05, 0.011)
    assert result is not None


# ── _classify ─────────────────────────────────────────────────────────────────

def test_classify_normal():
    assert _classify(0.5) == "Normal"
    assert _classify(-0.9) == "Normal"

def test_classify_moderate():
    assert _classify(1.2) == "Anomalía moderada"
    assert _classify(-1.3) == "Anomalía moderada"

def test_classify_extreme():
    assert _classify(2.0) == "Anomalía extrema"
    assert _classify(-3.5) == "Anomalía extrema"

def test_classify_none():
    assert _classify(None) == "Sin datos"


# ── _monthly_candles ──────────────────────────────────────────────────────────

def test_monthly_candles_empty():
    assert _monthly_candles([], {}, "ndvi") == []

def test_monthly_candles_basic_ohlc():
    series = [
        {"date": "2024-01-03", "value": 0.3},
        {"date": "2024-01-10", "value": 0.5},
        {"date": "2024-01-20", "value": 0.4},
        {"date": "2024-02-05", "value": 0.6},
    ]
    clim = {
        1: {"mean": 0.4, "std": 0.05},
        2: {"mean": 0.55, "std": 0.05},
    }
    candles = _monthly_candles(series, clim, "ndvi")
    assert len(candles) == 2

    jan = candles[0]
    assert jan["period"] == "2024-01-01"
    assert jan["open"]  == 0.3          # first value in month
    assert jan["close"] == 0.4          # last value in month
    assert jan["high"]  == 0.5
    assert jan["low"]   == 0.3
    assert jan["n_observations"] == 3

def test_monthly_candles_zscore_uses_month_climatology():
    series = [{"date": "2024-07-15", "value": 0.8}]
    clim   = {7: {"mean": 0.6, "std": 0.1}}
    candles = _monthly_candles(series, clim, "ndvi")
    assert len(candles) == 1
    # z = (0.8 - 0.6) / 0.1 = 2.0
    assert candles[0]["z_close"] == pytest.approx(2.0, abs=1e-4)

def test_monthly_candles_zscore_none_when_no_clim():
    series = [{"date": "2024-03-01", "value": 0.4}]
    # clim is empty for month 3
    candles = _monthly_candles(series, {}, "ndvi")
    assert candles[0]["z_close"] is None
    assert candles[0]["anomaly_class"] == "Sin datos"


# ── _derived_clim ─────────────────────────────────────────────────────────────

def test_derived_clim_hmax_equals_hmin_returns_empty():
    # R-005: degenerate pixel (water body, urban) — should return {} not crash
    result = _derived_clim({1: {"mean": 0.5, "std": 0.1}}, hmin=0.5, hmax=0.5)
    assert result == {}

def test_derived_clim_vci_formula():
    # VCI = (NDVI - NDVI_min) / (NDVI_max - NDVI_min)
    clim   = {1: {"mean": 0.4, "std": 0.1}}
    result = _derived_clim(clim, hmin=0.2, hmax=0.8)
    assert 1 in result
    expected_mu = (0.4 - 0.2) / (0.8 - 0.2)   # 0.3333…
    assert abs(result[1]["mean"] - expected_mu) < 1e-3
    expected_sd = 0.1 / (0.8 - 0.2)            # 0.1666…
    assert abs(result[1]["std"] - expected_sd) < 1e-3

def test_derived_clim_clamped_to_01():
    # VCI must be in [0, 1]
    clim   = {1: {"mean": 0.1, "std": 0.0}}    # NDVI below hmin
    result = _derived_clim(clim, hmin=0.2, hmax=0.8)
    assert result[1]["mean"] == 0.0             # clamped to 0

def test_derived_clim_invert_for_tci():
    # TCI = (NDVI_max - NDVI) / (NDVI_max - NDVI_min)  [invert=True]
    clim   = {1: {"mean": 0.4, "std": 0.1}}
    result = _derived_clim(clim, hmin=0.2, hmax=0.8, invert=True)
    expected_mu = (0.8 - 0.4) / (0.8 - 0.2)   # 0.6666…
    assert abs(result[1]["mean"] - expected_mu) < 1e-3


# ── _vhi_clim std propagation ─────────────────────────────────────────────────

def test_vhi_clim_std_is_sqrt_not_linear_average():
    # R-009: std should be sqrt(0.25*var_v + 0.25*var_t), not 0.5*(sd_v+sd_t)
    vci_clim = {1: {"mean": 0.6, "std": 0.1}}
    tci_clim = {1: {"mean": 0.4, "std": 0.2}}
    result   = _vhi_clim(vci_clim, tci_clim)

    expected_std = math.sqrt(0.25 * 0.1**2 + 0.25 * 0.2**2)
    wrong_std    = 0.5 * 0.1 + 0.5 * 0.2       # linear average (the old bug)

    assert abs(result[1]["std"] - expected_std) < 1e-5
    assert result[1]["std"] != pytest.approx(wrong_std, abs=1e-5)


# ── _vci / _tci ───────────────────────────────────────────────────────────────

def test_vci_midpoint():
    assert _vci(0.5, 0.0, 1.0) == 0.5

def test_vci_clamped_below():
    assert _vci(-0.1, 0.0, 1.0) == 0.0

def test_vci_clamped_above():
    assert _vci(1.1, 0.0, 1.0) == 1.0

def test_vci_none_ndvi():
    assert _vci(None, 0.0, 1.0) is None

def test_tci_midpoint():
    # TCI = (hmax - lst) / (hmax - hmin) = (40 - 30) / (40 - 20) = 0.5
    assert _tci(30, 20, 40) == 0.5

def test_tci_extreme_heat():
    # LST at hmax → TCI = 0 (worst thermal condition)
    assert _tci(40, 20, 40) == 0.0

def test_tci_cool():
    # LST at hmin → TCI = 1 (best thermal condition)
    assert _tci(20, 20, 40) == 1.0


# ── pytest import (only needed inside the test file) ─────────────────────────
import pytest
