"""Market-data analytics: volatility, liquidity, anomaly detection.

Pure functions over daily series, Python standard library only
(``statistics``) — no AWS imports. Definitions are normative in
``.kiro/specs/v3/domain-model.md``; edge-case behaviour is asserted by
``tests/unit/test_analytics.py``.

Inputs are daily values: ``close_prices`` are daily closes and ``volumes``
are daily trade counts (``market_daily.total_trades_delta`` — already the
end-of-day minus start-of-day difference of the lifetime ``total_trades``).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

#: Default trailing window for analytics, in days.
WINDOW_DAYS = 14
#: Minimum daily points required before analytics are meaningful.
MIN_POINTS = 7
#: |z-score| above this flags an anomaly.
ANOMALY_Z = 3.0


def daily_volatility(close_prices: Sequence[float]) -> dict[str, float]:
    """Mean, sample standard deviation (n-1) and coefficient of variation.

    Requires at least two points (``statistics.stdev`` is the sample
    estimator). ``cv`` is ``sigma / mean`` (0.0 when the mean is 0).
    """
    if len(close_prices) < 2:
        raise ValueError("daily_volatility requires at least 2 data points")
    mean = statistics.fmean(close_prices)
    sigma = statistics.stdev(close_prices)
    cv = sigma / mean if mean else 0.0
    return {"mean": mean, "sigma": sigma, "cv": cv}


def daily_liquidity(volumes: Sequence[float]) -> float:
    """Mean daily trade volume over the window (0.0 if empty)."""
    return statistics.fmean(volumes) if volumes else 0.0


def detect_anomaly(close_prices: Sequence[float]) -> dict[str, Any]:
    """Flag the latest close as anomalous via its z-score over the window.

        z = (latest_close - window_mean) / window_sigma

    ``is_anomalous`` is ``|z| > ANOMALY_Z``. A zero (or degenerate) sigma
    yields ``z = 0.0`` and ``is_anomalous = False`` rather than dividing by
    zero.
    """
    if len(close_prices) < 2:
        raise ValueError("detect_anomaly requires at least 2 data points")
    mean = statistics.fmean(close_prices)
    sigma = statistics.stdev(close_prices)
    if sigma == 0:
        return {"z_score": 0.0, "is_anomalous": False}
    z = (close_prices[-1] - mean) / sigma
    return {"z_score": z, "is_anomalous": abs(z) > ANOMALY_Z}


def market_analytics(
    close_prices: Sequence[float],
    volumes: Sequence[float],
    *,
    window_days: int = WINDOW_DAYS,
    min_points: int = MIN_POINTS,
) -> dict[str, Any]:
    """Combined analytics over the trailing ``window_days`` of daily data.

    Uses only the most recent ``window_days`` points of each series. If
    fewer than ``min_points`` daily closes are available, returns
    ``{"insufficient_data": True, ...}`` and computes nothing else.
    """
    closes = list(close_prices)[-window_days:]
    vols = list(volumes)[-window_days:]

    if len(closes) < min_points:
        return {"insufficient_data": True, "points": len(closes)}

    return {
        "insufficient_data": False,
        "points": len(closes),
        "window_days": window_days,
        "volatility": daily_volatility(closes),
        "liquidity": daily_liquidity(vols),
        "anomaly": detect_anomaly(closes),
    }
