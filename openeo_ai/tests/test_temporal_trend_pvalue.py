# ABOUTME: temporal_trend_analysis reports a real OLS t-test p-value (scipy t.sf),
# ABOUTME: replacing an ad-hoc |t|/(|t|+1) closed form that misstated significance.
import asyncio

import numpy as np
from scipy import stats

from openeo_ai.tools.temporal_analysis_tools import create_temporal_analysis_tools

DATES = ["2025-01-01", "2025-02-01", "2025-03-01",
         "2025-04-01", "2025-05-01", "2025-06-01"]
VALUES = [0.30, 0.24, 0.28, 0.22, 0.27, 0.21]


def _expected_p():
    # Replicates the tool's own regression, then applies the Student-t survival
    # function — the definition of the OLS slope p-value (df = n - 2).
    ref = np.datetime64(DATES[0])
    x = np.array([(np.datetime64(d) - ref) / np.timedelta64(1, "D") for d in DATES],
                 dtype=float)
    y = np.array(VALUES)
    slope, intercept = np.polyfit(x, y, 1)
    n = len(y)
    ss_res = np.sum((y - (slope * x + intercept)) ** 2)
    sxx = np.sum((x - x.mean()) ** 2)
    t_stat = slope / (np.sqrt(ss_res / (n - 2)) / np.sqrt(sxx))
    return 2.0 * stats.t.sf(abs(t_stat), n - 2)


def test_trend_p_value_comes_from_t_distribution():
    handler = create_temporal_analysis_tools()["temporal_trend_analysis"]
    out = asyncio.run(handler({"values": VALUES, "dates": DATES,
                               "metric_name": "NDVI"}))
    text = out["content"][0]["text"]
    line = next(ln for ln in text.splitlines() if "P-value" in ln)
    reported = float(line.split(":")[1])
    assert abs(reported - _expected_p()) < 1e-3
    # The label must not hedge with "approx" — the value is exact now.
    assert "approx" not in line.lower()


def test_two_point_series_reports_p_of_one():
    handler = create_temporal_analysis_tools()["temporal_trend_analysis"]
    out = asyncio.run(handler({"values": [0.3, 0.2],
                               "dates": DATES[:2], "metric_name": "NDVI"}))
    text = out["content"][0]["text"]
    line = next(ln for ln in text.splitlines() if "P-value" in ln)
    assert float(line.split(":")[1]) == 1.0
