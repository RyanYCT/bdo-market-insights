"""Tests for bdo_common.analytics.

Definitions follow ``.kiro/specs/v3/domain-model.md``: sample stddev
(n-1), CV = sigma/mean, liquidity = mean daily volume, anomaly when
|z| > 3 over the trailing window, and < 7 points -> insufficient_data.
"""

from __future__ import annotations

import math

import pytest

from bdo_common import analytics

# mean 5.0; sample variance 32/7; sample stdev sqrt(32/7).
SAMPLE = [2, 4, 4, 4, 5, 5, 7, 9]
SAMPLE_MEAN = 5.0
SAMPLE_SIGMA = math.sqrt(32 / 7)  # ~= 2.1380899


# --------------------------------------------------------------------------- #
# Volatility
# --------------------------------------------------------------------------- #
def test_daily_volatility_sample_stddev_and_cv() -> None:
    result = analytics.daily_volatility(SAMPLE)
    assert result["mean"] == pytest.approx(SAMPLE_MEAN)
    assert result["sigma"] == pytest.approx(SAMPLE_SIGMA)
    assert result["cv"] == pytest.approx(SAMPLE_SIGMA / SAMPLE_MEAN)


def test_daily_volatility_flat_series_is_zero() -> None:
    result = analytics.daily_volatility([100.0] * 7)
    assert result["sigma"] == pytest.approx(0.0)
    assert result["cv"] == pytest.approx(0.0)


def test_daily_volatility_requires_two_points() -> None:
    with pytest.raises(ValueError, match="2 data points"):
        analytics.daily_volatility([100.0])


# --------------------------------------------------------------------------- #
# Liquidity
# --------------------------------------------------------------------------- #
def test_daily_liquidity_is_mean_volume() -> None:
    assert analytics.daily_liquidity([10, 20, 30]) == pytest.approx(20.0)


def test_daily_liquidity_empty_is_zero() -> None:
    assert analytics.daily_liquidity([]) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# Anomaly detection
# --------------------------------------------------------------------------- #
def test_detect_anomaly_flags_outlier() -> None:
    # 13 days flat at 100, then a spike to 200.
    result = analytics.detect_anomaly([100.0] * 13 + [200.0])
    assert result["is_anomalous"] is True
    assert result["z_score"] > analytics.ANOMALY_Z


def test_detect_anomaly_normal_series_not_flagged() -> None:
    series = [100, 101, 99, 100, 102, 98, 101, 99, 100, 101, 99, 100, 102, 100]
    result = analytics.detect_anomaly(series)
    assert result["is_anomalous"] is False
    assert abs(result["z_score"]) <= analytics.ANOMALY_Z


def test_detect_anomaly_zero_sigma_is_not_anomalous() -> None:
    result = analytics.detect_anomaly([100.0] * 7)
    assert result["z_score"] == pytest.approx(0.0)
    assert result["is_anomalous"] is False


# --------------------------------------------------------------------------- #
# Combined analytics + insufficient-data gate
# --------------------------------------------------------------------------- #
def test_market_analytics_insufficient_data_below_min_points() -> None:
    result = analytics.market_analytics([100.0] * 6, [10.0] * 6)
    assert result["insufficient_data"] is True
    assert result["points"] == 6


def test_market_analytics_happy_path() -> None:
    closes = [100.0 + i for i in range(14)]
    volumes = [10.0 * (i + 1) for i in range(14)]
    result = analytics.market_analytics(closes, volumes)
    assert result["insufficient_data"] is False
    assert result["points"] == 14
    assert set(result) >= {"volatility", "liquidity", "anomaly"}
    assert result["liquidity"] == pytest.approx(analytics.daily_liquidity(volumes))


def test_market_analytics_uses_only_trailing_window() -> None:
    # 20 points but window is 14 -> only the most recent 14 are used.
    closes = [float(i) for i in range(20)]
    result = analytics.market_analytics(closes, closes, window_days=14)
    assert result["points"] == 14
    assert result["volatility"]["mean"] == pytest.approx(statistics_mean_last_14())


def statistics_mean_last_14() -> float:
    return sum(range(6, 20)) / 14  # closes[-14:] == 6..19
