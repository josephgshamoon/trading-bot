"""Tests for risk management system."""

import pytest
from unittest.mock import MagicMock

from src.risk.manager import RiskManager, DailyStats, Portfolio
from src.strategy.base import Signal, TradeSignal


@pytest.fixture
def config():
    return {
        "risk": {
            "max_daily_loss_usdc": 20.0,
            "max_drawdown_pct": 20.0,
            "max_trades_per_day": 5,
            "circuit_breaker_losses": 3,
            "cooldown_minutes": 60,
        },
        "trading": {
            "max_open_positions": 3,
            "max_position_usdc": 25.0,
            "min_position_usdc": 1.0,
        },
    }


@pytest.fixture
def risk_manager(config):
    rm = RiskManager(config)
    rm.initialize_portfolio(1000.0)
    return rm


@pytest.fixture
def sample_signal():
    return TradeSignal(
        signal=Signal.BUY_YES,
        market_id="test_market",
        question="Test question?",
        confidence=0.7,
        entry_price=0.55,
        position_size_usdc=10.0,
        edge=0.05,
        reason="Test",
    )


class TestRiskManager:
    def test_initialize_portfolio(self, risk_manager):
        assert risk_manager.portfolio.balance == 1000.0
        assert risk_manager.portfolio.starting_balance == 1000.0
        assert risk_manager.portfolio.peak_balance == 1000.0

    def test_can_trade_initially(self, risk_manager):
        allowed, reason = risk_manager.can_trade()
        assert allowed
        assert reason == "OK"

    def test_kill_switch(self, risk_manager):
        risk_manager.activate_kill_switch()
        allowed, reason = risk_manager.can_trade()
        assert not allowed
        assert "Kill switch" in reason

        risk_manager.deactivate_kill_switch()
        allowed, reason = risk_manager.can_trade()
        assert allowed

    def test_daily_trade_limit(self, risk_manager, sample_signal):
        for i in range(5):
            sig = TradeSignal(
                signal=Signal.BUY_YES,
                market_id=f"market_{i}",
                question=f"Question {i}?",
                confidence=0.7,
                entry_price=0.55,
                position_size_usdc=5.0,
                edge=0.05,
                reason="Test",
            )
            risk_manager.record_trade_entry(sig, f"trade_{i}")

        allowed, reason = risk_manager.can_trade()
        assert not allowed
        assert "trade limit" in reason.lower()

    def test_validate_trade_too_large(self, risk_manager):
        big_signal = TradeSignal(
            signal=Signal.BUY_YES,
            market_id="test",
            question="Test?",
            confidence=0.7,
            entry_price=0.55,
            position_size_usdc=50.0,  # Above max
            edge=0.05,
            reason="Test",
        )
        allowed, reason = risk_manager.validate_trade(big_signal)
        assert not allowed
        assert "too large" in reason.lower()

    def test_validate_trade_too_small(self, risk_manager):
        tiny_signal = TradeSignal(
            signal=Signal.BUY_YES,
            market_id="test",
            question="Test?",
            confidence=0.7,
            entry_price=0.55,
            position_size_usdc=0.50,  # Below min
            edge=0.05,
            reason="Test",
        )
        allowed, reason = risk_manager.validate_trade(tiny_signal)
        assert not allowed
        assert "too small" in reason.lower()

    def test_no_duplicate_positions(self, risk_manager, sample_signal):
        risk_manager.record_trade_entry(sample_signal, "trade_1")

        allowed, reason = risk_manager.validate_trade(sample_signal)
        assert not allowed
        assert "already have position" in reason.lower()

    def test_record_trade_exit_win(self, risk_manager, sample_signal):
        risk_manager.record_trade_entry(sample_signal, "trade_1")
        risk_manager.record_trade_exit("test_market", 5.0)

        assert risk_manager.daily.daily_pnl == 5.0
        assert risk_manager.daily.consecutive_losses == 0
        assert len(risk_manager.portfolio.open_positions) == 0

    def test_record_trade_exit_loss(self, risk_manager, sample_signal):
        risk_manager.record_trade_entry(sample_signal, "trade_1")
        risk_manager.record_trade_exit("test_market", -10.0)

        assert risk_manager.daily.daily_pnl == -10.0
        assert risk_manager.daily.consecutive_losses == 1

    def test_circuit_breaker(self, risk_manager):
        for i in range(3):
            sig = TradeSignal(
                signal=Signal.BUY_YES,
                market_id=f"market_{i}",
                question=f"Q{i}?",
                confidence=0.7,
                entry_price=0.55,
                position_size_usdc=5.0,
                edge=0.05,
                reason="Test",
            )
            risk_manager.record_trade_entry(sig, f"trade_{i}")
            risk_manager.record_trade_exit(f"market_{i}", -5.0)

        allowed, reason = risk_manager.can_trade()
        assert not allowed
        assert "cooldown" in reason.lower()

    def test_drawdown_limit(self, risk_manager):
        # Simulate 25% drawdown (should trigger at 20%)
        risk_manager.portfolio.balance = 750.0
        risk_manager.portfolio.peak_balance = 1000.0

        allowed, reason = risk_manager.can_trade()
        assert not allowed
        assert "drawdown" in reason.lower()

    def test_get_status(self, risk_manager):
        status = risk_manager.get_status()
        assert status["can_trade"] is True
        assert status["balance"] == 1000.0
        assert status["kill_switch"] is False
        assert status["trades_today"] == 0


class TestPortfolio:
    def test_drawdown_calculation(self):
        p = Portfolio(balance=800.0, starting_balance=1000.0, peak_balance=1000.0)
        assert p.drawdown == 20.0

    def test_drawdown_zero_peak(self):
        p = Portfolio(balance=0, starting_balance=0, peak_balance=0)
        assert p.drawdown == 0.0

    def test_open_position_count(self):
        p = Portfolio()
        assert p.open_position_count == 0
        p.open_positions.append({"market_id": "test"})
        assert p.open_position_count == 1
