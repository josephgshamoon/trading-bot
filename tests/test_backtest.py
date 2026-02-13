"""Tests for backtesting engine."""

import pytest

from src.engine.backtest import BacktestEngine, BacktestResult, BacktestTrade
from src.strategy.base import BaseStrategy, Signal, TradeSignal
from src.data.indicators import MarketIndicators


class MockStrategy(BaseStrategy):
    """Strategy that always signals BUY_YES for testing."""

    def __init__(self, config, should_signal=True):
        super().__init__(config)
        self.should_signal = should_signal
        self.name = "MockStrategy"

    def evaluate(self, snapshot, indicators):
        if not self.should_signal:
            return None
        return TradeSignal(
            signal=Signal.BUY_YES,
            market_id=snapshot.get("market_id", "test"),
            question=snapshot.get("question", "Test?"),
            confidence=0.7,
            entry_price=snapshot.get("yes_price", 0.55),
            position_size_usdc=5.0,
            edge=0.05,
            reason="Mock signal",
            metadata={"strategy": "mock"},
        )


@pytest.fixture
def config():
    return {
        "backtest": {
            "fee_pct": 2.0,
            "slippage_pct": 1.0,
            "starting_balance_usdc": 1000.0,
        },
        "risk": {
            "min_entry_probability": 0.15,
            "max_entry_probability": 0.85,
        },
    }


@pytest.fixture
def sample_snapshots():
    """Generate sample market snapshots."""
    return [
        {
            "market_id": f"market_{i}",
            "question": f"Will event {i} happen?",
            "yes_price": 0.55,
            "no_price": 0.45,
            "volume": 100000,
            "liquidity": 50000,
            "closed": False,
            "timestamp": f"2024-01-{i+1:02d}T00:00:00Z",
        }
        for i in range(20)
    ]


class TestBacktestEngine:
    def test_run_with_signals(self, config, sample_snapshots):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config)

        result = engine.run(strategy, sample_snapshots, seed=42)

        assert result.total_trades > 0
        assert result.starting_balance == 1000.0
        assert result.strategy_name == "MockStrategy"
        assert len(result.equity_curve) > 1

    def test_run_without_signals(self, config, sample_snapshots):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config, should_signal=False)

        result = engine.run(strategy, sample_snapshots, seed=42)

        assert result.total_trades == 0
        assert result.ending_balance == 1000.0

    def test_run_empty_snapshots(self, config):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config)

        result = engine.run(strategy, [], seed=42)

        assert result.total_trades == 0

    def test_deterministic_with_seed(self, config, sample_snapshots):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config)

        result1 = engine.run(strategy, sample_snapshots, seed=42)
        result2 = engine.run(strategy, sample_snapshots, seed=42)

        assert result1.total_trades == result2.total_trades
        assert result1.wins == result2.wins
        assert result1.net_pnl == result2.net_pnl

    def test_fees_are_applied(self, config, sample_snapshots):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config)

        result = engine.run(strategy, sample_snapshots, seed=42)

        assert result.total_fees > 0

    def test_metrics_calculation(self, config, sample_snapshots):
        engine = BacktestEngine(config)
        strategy = MockStrategy(config)

        result = engine.run(strategy, sample_snapshots, seed=42)

        assert 0 <= result.win_rate <= 1
        assert result.wins + result.losses == result.total_trades
        assert result.ending_balance == pytest.approx(
            result.starting_balance + result.net_pnl, abs=0.01
        )


class TestBacktestResult:
    def test_summary_output(self):
        result = BacktestResult(
            strategy_name="Test",
            total_trades=10,
            wins=6,
            losses=4,
            total_pnl=50.0,
            net_pnl=50.0,
            win_rate=0.6,
            starting_balance=1000.0,
            ending_balance=1050.0,
            roi_pct=5.0,
        )
        summary = result.summary()
        assert "Test" in summary
        assert "60.0%" in summary
        assert "$+50.00" in summary
