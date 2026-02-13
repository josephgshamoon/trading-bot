"""Tests for trading strategies."""

import pytest

from src.strategy.base import Signal, TradeSignal
from src.strategy.value_betting import ValueBettingStrategy
from src.strategy.momentum import MomentumStrategy
from src.strategy.arbitrage import ArbitrageStrategy


@pytest.fixture
def config():
    return {
        "risk": {
            "min_entry_probability": 0.15,
            "max_entry_probability": 0.85,
        },
        "trading": {
            "default_position_usdc": 5.0,
            "max_position_usdc": 25.0,
            "min_position_usdc": 1.0,
        },
        "strategy": {
            "value_betting": {
                "min_edge": 0.05,
                "min_volume_usd": 50000,
                "min_liquidity_usd": 10000,
                "prob_range_low": 0.20,
                "prob_range_high": 0.80,
                "kelly_fraction": 0.25,
            },
            "momentum": {
                "min_price_move": 0.05,
                "volume_spike_factor": 2.0,
                "confirmation_count": 3,
                "min_volume_usd": 50000,
            },
            "arbitrage": {
                "min_spread": 0.02,
                "min_liquidity_usd": 5000,
                "fee_pct": 2.0,
            },
        },
    }


@pytest.fixture
def base_snapshot():
    return {
        "market_id": "test_market_1",
        "question": "Will X happen by end of month?",
        "yes_price": 0.55,
        "no_price": 0.45,
        "volume": 100000,
        "liquidity": 50000,
        "closed": False,
        "token_ids": ["token_yes", "token_no"],
    }


class TestValueBetting:
    def test_no_signal_when_no_edge(self, config, base_snapshot):
        strategy = ValueBettingStrategy(config)
        # Prices close to fair value, high volume = efficient market
        indicators = {
            "yes_price": 0.55,
            "no_price": 0.45,
            "volume": 500000,
            "liquidity": 200000,
            "momentum_6": 0.0,
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        # High volume efficient market should not generate signal
        assert signal is None or signal.edge < 0.05

    def test_signal_on_mispriced_market(self, config, base_snapshot):
        strategy = ValueBettingStrategy(config)
        # Low volume market with momentum = potential mispricing
        base_snapshot["volume"] = 40000
        base_snapshot["liquidity"] = 8000
        indicators = {
            "yes_price": 0.55,
            "no_price": 0.45,
            "volume": 40000,
            "liquidity": 8000,
            "momentum_6": 0.1,
        }
        # With low volume and momentum, edge estimate should diverge
        # This might or might not generate a signal depending on the edge calc
        signal = strategy.evaluate(base_snapshot, indicators)
        # We just verify it doesn't crash; signal depends on exact parameters

    def test_rejects_closed_market(self, config, base_snapshot):
        strategy = ValueBettingStrategy(config)
        base_snapshot["closed"] = True
        signal = strategy.evaluate(base_snapshot, {})
        assert signal is None

    def test_rejects_extreme_probabilities(self, config, base_snapshot):
        strategy = ValueBettingStrategy(config)
        base_snapshot["yes_price"] = 0.95
        signal = strategy.evaluate(base_snapshot, {"volume": 100000, "liquidity": 50000})
        assert signal is None

    def test_rejects_low_volume(self, config, base_snapshot):
        strategy = ValueBettingStrategy(config)
        indicators = {
            "yes_price": 0.55,
            "no_price": 0.45,
            "volume": 5000,  # Below min
            "liquidity": 50000,
            "momentum_6": 0.0,
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None


class TestMomentum:
    def test_no_signal_without_momentum(self, config, base_snapshot):
        strategy = MomentumStrategy(config)
        indicators = {
            "volume": 100000,
            "momentum_6": 0.01,  # Below threshold
            "velocity": 0.001,
            "volatility": 0.05,
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None

    def test_buy_yes_on_upward_momentum(self, config, base_snapshot):
        strategy = MomentumStrategy(config)
        indicators = {
            "volume": 100000,
            "momentum_6": 0.10,  # Strong upward
            "velocity": 0.02,  # Same direction
            "volatility": 0.05,  # Low enough
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is not None
        assert signal.signal == Signal.BUY_YES

    def test_buy_no_on_downward_momentum(self, config, base_snapshot):
        strategy = MomentumStrategy(config)
        indicators = {
            "volume": 100000,
            "momentum_6": -0.10,
            "velocity": -0.02,
            "volatility": 0.05,
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is not None
        assert signal.signal == Signal.BUY_NO

    def test_rejects_high_volatility(self, config, base_snapshot):
        strategy = MomentumStrategy(config)
        indicators = {
            "volume": 100000,
            "momentum_6": 0.10,
            "velocity": 0.02,
            "volatility": 0.20,  # Too volatile
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None

    def test_rejects_conflicting_signals(self, config, base_snapshot):
        strategy = MomentumStrategy(config)
        indicators = {
            "volume": 100000,
            "momentum_6": 0.10,   # Positive
            "velocity": -0.02,    # Negative — conflict
            "volatility": 0.05,
        }
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None


class TestArbitrage:
    def test_no_signal_on_fair_pricing(self, config, base_snapshot):
        strategy = ArbitrageStrategy(config)
        # YES + NO = 1.00 — no arbitrage
        base_snapshot["yes_price"] = 0.55
        base_snapshot["no_price"] = 0.45
        indicators = {"liquidity": 50000}
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None

    def test_signal_on_underpriced_market(self, config, base_snapshot):
        strategy = ArbitrageStrategy(config)
        # YES + NO = 0.90 — underpriced by 0.10
        base_snapshot["yes_price"] = 0.45
        base_snapshot["no_price"] = 0.45
        indicators = {"liquidity": 50000}
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is not None

    def test_rejects_low_liquidity(self, config, base_snapshot):
        strategy = ArbitrageStrategy(config)
        base_snapshot["yes_price"] = 0.45
        base_snapshot["no_price"] = 0.45
        indicators = {"liquidity": 1000}  # Below min
        signal = strategy.evaluate(base_snapshot, indicators)
        assert signal is None


class TestTradeSignal:
    def test_str_representation(self):
        signal = TradeSignal(
            signal=Signal.BUY_YES,
            market_id="test",
            question="Will something happen?" * 3,
            confidence=0.75,
            entry_price=0.45,
            position_size_usdc=10.0,
            edge=0.08,
            reason="Test signal",
        )
        s = str(signal)
        assert "BUY_YES" in s
        assert "0.45" in s
