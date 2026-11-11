# ABOUTME: Temporal analysis tools for time-series EO data analysis.
# Provides anomaly detection, trend analysis, and phenology extraction.

"""
Temporal analysis tools for Claude SDK.

Provides tools for detecting anomalies, analyzing trends, and extracting
phenological metrics from Earth Observation time series data.
"""

from typing import Any, Dict, List


TEMPORAL_ANALYSIS_TOOL_DEFINITIONS = [
    {
        "name": "temporal_anomaly_detect",
        "description": "Detect anomalies in a temporal time series of Earth Observation values. Computes a baseline (mean or median) and flags values exceeding a configurable number of standard deviations from the baseline.",
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "List of numeric values in the time series"
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of date strings (ISO format) corresponding to the values"
                },
                "baseline_method": {
                    "type": "string",
                    "description": "Method for computing the baseline: 'mean' or 'median' (default: 'mean')",
                    "enum": ["mean", "median"],
                    "default": "mean"
                },
                "threshold_std": {
                    "type": "number",
                    "description": "Number of standard deviations from baseline to flag as anomaly (default: 2.0)",
                    "default": 2.0
                }
            },
            "required": ["values", "dates"]
        }
    },
    {
        "name": "temporal_trend_analysis",
        "description": "Perform linear trend analysis on a time series. Fits a linear regression, computes slope per year, R-squared, and classifies the trend as increasing, decreasing, or stable.",
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "List of numeric values in the time series"
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of date strings (ISO format) corresponding to the values"
                },
                "metric_name": {
                    "type": "string",
                    "description": "Name of the metric being analyzed (default: 'Value')",
                    "default": "Value"
                }
            },
            "required": ["values", "dates"]
        }
    },
    {
        "name": "temporal_phenology",
        "description": "Extract phenological metrics from an NDVI-like time series (0-1 range). Computes Start of Season (SOS), Peak of Season (POS), End of Season (EOS), Growing Season Length, peak value, amplitude, and mean growing season value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "List of NDVI-like values (0-1 range) in the time series"
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of date strings (ISO format) corresponding to the values"
                }
            },
            "required": ["values", "dates"]
        }
    }
]


def create_temporal_analysis_tools(config=None) -> Dict[str, Any]:
    """
    Create temporal analysis tools dict for Claude SDK.

    Args:
        config: Optional configuration object (unused currently)

    Returns:
        Dict mapping tool names to async handler functions
    """

    async def _temporal_anomaly_detect(args: Dict[str, Any]) -> Dict[str, Any]:
        """Detect anomalies in a temporal time series."""
        try:
            import numpy as np
            from datetime import datetime

            values = args["values"]
            dates = args["dates"]
            baseline_method = args.get("baseline_method", "mean")
            threshold_std = args.get("threshold_std", 2.0)

            if len(values) != len(dates):
                return {
                    "content": [{"type": "text", "text": "Error: 'values' and 'dates' must have the same length."}]
                }

            if len(values) < 3:
                return {
                    "content": [{"type": "text", "text": "Error: At least 3 data points are required for anomaly detection."}]
                }

            values_arr = np.array(values, dtype=float)

            if baseline_method == "median":
                baseline = float(np.median(values_arr))
            else:
                baseline = float(np.mean(values_arr))

            std_dev = float(np.std(values_arr))
            threshold = threshold_std * std_dev

            anomalies = []
            for i, (v, d) in enumerate(zip(values, dates)):
                deviation = abs(v - baseline)
                if deviation > threshold:
                    severity = deviation / std_dev if std_dev > 0 else 0
                    direction = "above" if v > baseline else "below"
                    anomalies.append({
                        "date": d,
                        "value": v,
                        "deviation": round(deviation, 4),
                        "severity_std": round(severity, 2),
                        "direction": direction
                    })

            summary_lines = [
                f"Anomaly Detection Results ({baseline_method} baseline)",
                f"  Baseline: {baseline:.4f}",
                f"  Std Dev: {std_dev:.4f}",
                f"  Threshold: {threshold_std} std deviations ({threshold:.4f})",
                f"  Total points: {len(values)}",
                f"  Anomalies found: {len(anomalies)}",
            ]

            if anomalies:
                summary_lines.append("\nFlagged anomalies:")
                for a in anomalies:
                    summary_lines.append(
                        f"  - {a['date']}: {a['value']:.4f} ({a['direction']} baseline by {a['severity_std']:.1f} std)"
                    )

            summary_text = "\n".join(summary_lines)

            return {
                "content": [
                    {"type": "text", "text": summary_text},
                    {"type": "visualization", "component": {
                        "type": "chart",
                        "chartType": "line",
                        "title": "Temporal Anomaly Detection",
                        "xAxis": {"label": "Date", "values": dates},
                        "yAxis": {"label": "Value"},
                        "series": [
                            {"name": "Values", "values": values},
                            {"name": "Baseline", "values": [baseline] * len(values)},
                            {"name": "Upper Threshold", "values": [baseline + threshold] * len(values)},
                            {"name": "Lower Threshold", "values": [baseline - threshold] * len(values)}
                        ]
                    }}
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error in anomaly detection: {str(e)}"}]
            }

    async def _temporal_trend_analysis(args: Dict[str, Any]) -> Dict[str, Any]:
        """Perform linear trend analysis on a time series."""
        try:
            import numpy as np
            from datetime import datetime

            values = args["values"]
            dates = args["dates"]
            metric_name = args.get("metric_name", "Value")

            if len(values) != len(dates):
                return {
                    "content": [{"type": "text", "text": "Error: 'values' and 'dates' must have the same length."}]
                }

            if len(values) < 2:
                return {
                    "content": [{"type": "text", "text": "Error: At least 2 data points are required for trend analysis."}]
                }

            parsed_dates = []
            for d in dates:
                try:
                    parsed_dates.append(datetime.fromisoformat(d.replace('Z', '+00:00')))
                except (ValueError, AttributeError):
                    parsed_dates.append(datetime.strptime(d[:10], '%Y-%m-%d'))

            ref_date = parsed_dates[0]
            x_days = np.array([(dt - ref_date).total_seconds() / 86400.0 for dt in parsed_dates])

            values_arr = np.array(values, dtype=float)

            slope, intercept = np.polyfit(x_days, values_arr, 1)

            trend_line = (slope * x_days + intercept).tolist()

            slope_per_year = slope * 365.25

            y_mean = np.mean(values_arr)
            ss_res = np.sum((values_arr - (slope * x_days + intercept)) ** 2)
            ss_tot = np.sum((values_arr - y_mean) ** 2)
            r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

            n = len(values)
            sxx = np.sum((x_days - np.mean(x_days)) ** 2)
            if n > 2 and ss_tot > 0 and sxx > 0:
                t_stat = slope / (np.sqrt(ss_res / (n - 2)) / np.sqrt(sxx))
                # Real OLS slope p-value: Student-t survival function, df = n - 2.
                # The previous |t|/(|t|+1) closed form was not any statistical
                # distribution and misstated significance.
                from scipy import stats as scipy_stats
                p_value = float(2.0 * scipy_stats.t.sf(abs(t_stat), n - 2))
            else:
                t_stat = 0
                p_value = 1.0

            total_days = x_days[-1] - x_days[0] if len(x_days) > 1 else 1
            overall_change = slope * total_days

            if abs(t_stat) > 2.0:
                trend_class = "increasing" if slope > 0 else "decreasing"
            else:
                trend_class = "stable"

            summary_lines = [
                f"Trend Analysis: {metric_name}",
                f"  Trend: {trend_class}",
                f"  Slope per year: {slope_per_year:.6f}",
                f"  R-squared: {r_squared:.4f}",
                f"  P-value (OLS t-test): {p_value:.4f}",
                f"  Overall change: {overall_change:.4f}",
                f"  Time span: {total_days:.0f} days",
                f"  Data points: {n}",
            ]

            summary_text = "\n".join(summary_lines)

            return {
                "content": [
                    {"type": "text", "text": summary_text},
                    {"type": "visualization", "component": {
                        "type": "chart",
                        "chartType": "line",
                        "title": f"Trend Analysis: {metric_name}",
                        "xAxis": {"label": "Date", "values": dates},
                        "yAxis": {"label": metric_name},
                        "series": [
                            {"name": metric_name, "values": values},
                            {"name": "Trend", "values": trend_line}
                        ]
                    }}
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error in trend analysis: {str(e)}"}]
            }

    async def _temporal_phenology(args: Dict[str, Any]) -> Dict[str, Any]:
        """Extract phenological metrics from an NDVI-like time series."""
        try:
            import numpy as np
            from datetime import datetime

            values = args["values"]
            dates = args["dates"]

            if len(values) != len(dates):
                return {
                    "content": [{"type": "text", "text": "Error: 'values' and 'dates' must have the same length."}]
                }

            if len(values) < 3:
                return {
                    "content": [{"type": "text", "text": "Error: At least 3 data points are required for phenology extraction."}]
                }

            parsed_dates = []
            for d in dates:
                try:
                    parsed_dates.append(datetime.fromisoformat(d.replace('Z', '+00:00')))
                except (ValueError, AttributeError):
                    parsed_dates.append(datetime.strptime(d[:10], '%Y-%m-%d'))

            values_arr = np.array(values, dtype=float)

            min_val = float(np.min(values_arr))
            max_val = float(np.max(values_arr))
            amplitude = max_val - min_val

            peak_idx = int(np.argmax(values_arr))
            pos_date = dates[peak_idx]

            threshold_val = min_val + 0.2 * amplitude

            sos_idx = None
            for i in range(len(values)):
                if values_arr[i] >= threshold_val:
                    sos_idx = i
                    break

            eos_idx = None
            for i in range(len(values) - 1, -1, -1):
                if values_arr[i] >= threshold_val:
                    eos_idx = i
                    break

            sos_date = dates[sos_idx] if sos_idx is not None else "N/A"
            eos_date = dates[eos_idx] if eos_idx is not None else "N/A"

            if sos_idx is not None and eos_idx is not None:
                gsl_days = (parsed_dates[eos_idx] - parsed_dates[sos_idx]).days
                growing_values = values_arr[sos_idx:eos_idx + 1]
                mean_growing = float(np.mean(growing_values)) if len(growing_values) > 0 else 0.0
            else:
                gsl_days = 0
                mean_growing = 0.0

            summary_lines = [
                "Phenological Metrics",
                f"  Start of Season (SOS): {sos_date}",
                f"  Peak of Season (POS): {pos_date}",
                f"  End of Season (EOS): {eos_date}",
                f"  Growing Season Length (GSL): {gsl_days} days",
                f"  Peak Value: {max_val:.4f}",
                f"  Amplitude: {amplitude:.4f}",
                f"  Mean Growing Season Value: {mean_growing:.4f}",
            ]

            summary_text = "\n".join(summary_lines)

            sos_values = [None] * len(values)
            pos_values = [None] * len(values)
            eos_values_series = [None] * len(values)

            if sos_idx is not None:
                sos_values[sos_idx] = values[sos_idx]
            pos_values[peak_idx] = values[peak_idx]
            if eos_idx is not None:
                eos_values_series[eos_idx] = values[eos_idx]

            return {
                "content": [
                    {"type": "text", "text": summary_text},
                    {"type": "visualization", "component": {
                        "type": "chart",
                        "chartType": "line",
                        "title": "Phenology: NDVI Time Series",
                        "xAxis": {"label": "Date", "values": dates},
                        "yAxis": {"label": "NDVI"},
                        "series": [
                            {"name": "NDVI", "values": values},
                            {"name": "SOS", "values": sos_values},
                            {"name": "POS", "values": pos_values},
                            {"name": "EOS", "values": eos_values_series}
                        ]
                    }}
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error in phenology extraction: {str(e)}"}]
            }

    return {
        "temporal_anomaly_detect": _temporal_anomaly_detect,
        "temporal_trend_analysis": _temporal_trend_analysis,
        "temporal_phenology": _temporal_phenology,
    }
