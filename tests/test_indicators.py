"""Tests for prediction market indicators."""

import numpy as np
import pandas as pd
import pytest

from src.data.indicators import MarketIndicators


@pytest.fixture
def price_series():
    """Generate a sample price series for testing."""
    np.random.seed(42)
    prices = 0.5 + np.cumsum(np.random.randn(50) * 0.02)
    prices = np.clip(prices, 0.01, 0.99)
    return pd.Series(prices, name="price")


@pytest.fixture
def sample_snapshot():
    return {
        "yes_price": 0.65,
        "no_price": 0.35,
        "spread": 0.0,
        "volume": 75000,
        "liquidity": 25000,
    }


class TestMarketIndicators:
    def test_price_momentum(self, price_series):
        result = MarketIndicators.price_momentum(price_series, window=6)
        assert len(result) == len(price_series)
        assert pd.isna(result.iloc[0])  # First values should be NaN
        assert not pd.isna(result.iloc[-1])

    def test_price_velocity(self, price_series):
        result = MarketIndicators.price_velocity(price_series, window=3)
        assert len(result) == len(price_series)

    def test_price_acceleration(self, price_series):
        result = MarketIndicators.price_acceleration(price_series, window=3)
        assert len(result) == len(price_series)

    def test_volatility(self, price_series):
        result = MarketIndicators.volatility(price_series, window=12)
        assert len(result) == len(price_series)
        # Volatility should be non-negative where defined
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_mean_reversion_score(self, price_series):
        result = MarketIndicators.mean_reversion_score(price_series, window=20)
        assert len(result) == len(price_series)

    def test_volume_momentum(self):
        volumes = pd.Series([100, 120, 80, 200, 150, 300, 100])
        result = MarketIndicators.volume_momentum(volumes, window=3)
        assert len(result) == 7

    def test_spread_efficiency(self):
        yes = pd.Series([0.60, 0.55, 0.48, 0.70])
        no = pd.Series([0.40, 0.45, 0.48, 0.25])
        result = MarketIndicators.spread_efficiency(yes, no)
        assert abs(result.iloc[0]) < 0.01  # 0.60 + 0.40 = 1.00
        assert abs(result.iloc[2] - (-0.04)) < 0.01  # 0.48 + 0.48 = 0.96
        assert abs(result.iloc[3] - (-0.05)) < 0.01  # 0.70 + 0.25 = 0.95

    def test_kelly_criterion_positive_edge(self):
        # True prob = 0.7, market price = 0.5 => strong positive edge
        kelly = MarketIndicators.kelly_criterion(0.7, 0.5, fraction=1.0)
        assert kelly > 0

    def test_kelly_criterion_no_edge(self):
        # True prob = 0.4, market price = 0.5 => negative edge
        kelly = MarketIndicators.kelly_criterion(0.4, 0.5, fraction=1.0)
        assert kelly == 0.0

    def test_kelly_criterion_fractional(self):
        full = MarketIndicators.kelly_criterion(0.7, 0.5, fraction=1.0)
        quarter = MarketIndicators.kelly_criterion(0.7, 0.5, fraction=0.25)
        assert abs(quarter - full * 0.25) < 1e-10

    def test_kelly_criterion_boundary_values(self):
        assert MarketIndicators.kelly_criterion(0.0, 0.5) == 0.0
        assert MarketIndicators.kelly_criterion(1.0, 0.5) == 0.0
        assert MarketIndicators.kelly_criterion(0.5, 0.0) == 0.0
        assert MarketIndicators.kelly_criterion(0.5, 1.0) == 0.0

    def test_edge_estimate(self):
        # High volume, high liquidity = close to market price
        result = MarketIndicators.edge_estimate(0.60, 500000, 100000)
        assert 0.55 <= result <= 0.65

        # Low volume, low liquidity = further from market price
        result_low = MarketIndicators.edge_estimate(0.60, 30000, 5000)
        assert result_low != result  # Should differ from high-vol estimate

    def test_compute_all_without_history(self, sample_snapshot):
        result = MarketIndicators.compute_all(sample_snapshot)
        assert result["yes_price"] == 0.65
        assert result["momentum_6"] == 0.0
        assert result["velocity"] == 0.0

    def test_compute_all_with_history(self, sample_snapshot, price_series):
        history = pd.DataFrame({"price": price_series})
        history.index = pd.date_range("2024-01-01", periods=len(price_series), freq="h")

        result = MarketIndicators.compute_all(sample_snapshot, history)
        assert "momentum_6" in result
        assert "velocity" in result
        assert "volatility" in result
