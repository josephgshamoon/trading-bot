"""
Tests for Auto-Trader Engine
"""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.auto_trader import AutoTrader


# -- Fixtures & helpers -------------------------------------------------------

FAKE_MARKET = {
    "id": "12345",
    "question": "Will it rain tomorrow?",
    "active": True,
    "closed": False,
    "resolved": False,
    "volume": 100000,
    "liquidity": 50000,
    "outcomePrices": '["0.60","0.40"]',
    "outcome_prices": [0.60, 0.40],
    "tokens": [
        {"outcome": "Yes", "token_id": "tok_yes_abc123"},
        {"outcome": "No", "token_id": "tok_no_def456"},
    ],
    "outcomes": '["Yes","No"]',
}

RESOLVED_MARKET = {**FAKE_MARKET, "closed": True, "resolved": True, "resolution": "Yes"}


@pytest.fixture
def trader():
    """Create an AutoTrader with sane defaults (no side-effects)."""
    with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfakekey"}):
        t = AutoTrader(market_id="12345", side="yes", amount=1.0, interval=5, stop_loss_pct=50.0)
    return t


@pytest.fixture
def ready_trader(trader):
    """An AutoTrader that has already 'validated' and 'entered'."""
    trader.market = FAKE_MARKET
    trader.token_id = "tok_yes_abc123"
    trader.entry_price = 0.60
    trader.entry_time = datetime(2026, 1, 1, 12, 0, 0)
    trader.running = True
    trader._client = MagicMock()
    trader._trader = MagicMock()
    return trader


# -- Initialization -----------------------------------------------------------

class TestInit:
    def test_side_uppercased(self):
        with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfakekey"}):
            t = AutoTrader("m1", "yes", 2.0)
        assert t.side == "YES"

    def test_defaults(self, trader):
        assert trader.market_id == "12345"
        assert trader.side == "YES"
        assert trader.amount == 1.0
        assert trader.interval == 5
        assert trader.stop_loss_pct == 50.0
        assert trader.running is False
        assert trader.entry_price == 0.0
        assert trader.exit_price is None


# -- _parse_json_field --------------------------------------------------------

class TestParseJsonField:
    def test_none_returns_empty_list(self):
        assert AutoTrader._parse_json_field(None) == []

    def test_none_with_default(self):
        assert AutoTrader._parse_json_field(None, ["a"]) == ["a"]

    def test_list_passthrough(self):
        assert AutoTrader._parse_json_field([1, 2]) == [1, 2]

    def test_json_string(self):
        assert AutoTrader._parse_json_field('["0.6","0.4"]') == ["0.6", "0.4"]

    def test_bad_json_returns_default(self):
        assert AutoTrader._parse_json_field("not json") == []

    def test_non_list_json(self):
        assert AutoTrader._parse_json_field('{"a":1}') == []


# -- _resolve_token_id --------------------------------------------------------

class TestResolveTokenId:
    def test_from_tokens_list(self, trader):
        trader.market = FAKE_MARKET
        trader.side = "YES"
        assert trader._resolve_token_id() == "tok_yes_abc123"

    def test_no_side(self, trader):
        trader.market = FAKE_MARKET
        trader.side = "NO"
        assert trader._resolve_token_id() == "tok_no_def456"

    def test_from_clob_token_ids(self, trader):
        trader.market = {
            "tokens": [],
            "clobTokenIds": '["clob_yes","clob_no"]',
            "outcomes": '["Yes","No"]',
        }
        trader.side = "YES"
        assert trader._resolve_token_id() == "clob_yes"

    def test_missing_tokens(self, trader):
        trader.market = {}
        trader.side = "YES"
        assert trader._resolve_token_id() == ""


# -- P&L calculations --------------------------------------------------------

class TestPnl:
    def test_calc_pnl_at_breakeven(self, ready_trader):
        assert ready_trader._calc_pnl_at(0.60) == pytest.approx(0.0)

    def test_calc_pnl_at_profit(self, ready_trader):
        # Bought 1/0.6 ≈ 1.6667 shares; at 0.80 → value 1.3333; P&L +0.3333
        pnl = ready_trader._calc_pnl_at(0.80)
        assert pnl == pytest.approx(0.3333, abs=0.001)

    def test_calc_pnl_at_loss(self, ready_trader):
        # At 0.30 → value 0.50; P&L -0.50
        pnl = ready_trader._calc_pnl_at(0.30)
        assert pnl == pytest.approx(-0.50, abs=0.001)

    def test_calc_pnl_zero_entry(self, ready_trader):
        ready_trader.entry_price = 0.0
        assert ready_trader._calc_pnl_at(0.50) == 0.0

    def test_calc_pnl_no_exit(self, ready_trader):
        ready_trader.exit_price = None
        assert ready_trader._calc_pnl() == 0.0

    def test_calc_pnl_with_exit(self, ready_trader):
        ready_trader.exit_price = 0.80
        assert ready_trader._calc_pnl() == pytest.approx(0.3333, abs=0.001)


# -- Stop-loss ----------------------------------------------------------------

class TestStopLoss:
    def test_no_trigger_at_entry(self, ready_trader):
        assert ready_trader._check_stop_loss(0.60) is False

    def test_no_trigger_above_entry(self, ready_trader):
        assert ready_trader._check_stop_loss(0.70) is False

    def test_triggers_at_threshold(self, ready_trader):
        # 50% loss from 0.60 → price 0.30
        assert ready_trader._check_stop_loss(0.30) is True
        assert ready_trader.running is False

    def test_triggers_below_threshold(self, ready_trader):
        assert ready_trader._check_stop_loss(0.20) is True

    def test_no_trigger_just_above(self, ready_trader):
        # 49% loss → price 0.306
        assert ready_trader._check_stop_loss(0.306) is False

    def test_zero_entry_price(self, ready_trader):
        ready_trader.entry_price = 0.0
        assert ready_trader._check_stop_loss(0.10) is False

    def test_sells_on_stop_loss(self, ready_trader):
        ready_trader._check_stop_loss(0.20)
        ready_trader._trader.place_market_order.assert_called_once_with(
            token_id="tok_yes_abc123",
            side="SELL",
            size=1.0,
        )


# -- Resolution ---------------------------------------------------------------

class TestResolution:
    def test_open_market(self, ready_trader):
        assert ready_trader._check_resolution(FAKE_MARKET) is False
        assert ready_trader.running is True

    def test_closed_market(self, ready_trader):
        assert ready_trader._check_resolution(RESOLVED_MARKET) is True
        assert ready_trader.running is False
        assert "resolved" in ready_trader.exit_reason

    def test_resolved_only(self, ready_trader):
        m = {**FAKE_MARKET, "resolved": True, "resolution": "No"}
        assert ready_trader._check_resolution(m) is True

    def test_closed_only(self, ready_trader):
        m = {**FAKE_MARKET, "closed": True}
        assert ready_trader._check_resolution(m) is True


# -- Validation ---------------------------------------------------------------

class TestValidation:
    def test_missing_private_key(self):
        with patch.dict(os.environ, {}, clear=True):
            t = AutoTrader.__new__(AutoTrader)
            t.side = "YES"
            t.market_id = "123"
            with pytest.raises(SystemExit, match="POLYMARKET_PRIVATE_KEY"):
                t._validate()

    def test_invalid_side(self):
        with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfake"}):
            t = AutoTrader.__new__(AutoTrader)
            t.side = "MAYBE"
            t.market_id = "123"
            with pytest.raises(SystemExit, match="Invalid side"):
                t._validate()

    @patch("src.polymarket_client.PolymarketClient.get_market")
    def test_market_not_found(self, mock_get_market):
        mock_get_market.return_value = {}
        with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfake"}):
            t = AutoTrader.__new__(AutoTrader)
            t.side = "YES"
            t.market_id = "999"
            t._client = None
            with pytest.raises(SystemExit, match="not found"):
                t._validate()

    @patch("src.polymarket_client.PolymarketClient.get_market")
    def test_closed_market_rejected(self, mock_get_market):
        mock_get_market.return_value = {
            "id": "1", "question": "Q", "active": True, "closed": True,
        }
        with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfake"}):
            t = AutoTrader.__new__(AutoTrader)
            t.side = "YES"
            t.market_id = "1"
            t._client = None
            with pytest.raises(SystemExit, match="closed or inactive"):
                t._validate()

    @patch("src.polymarket_client.PolymarketClient.get_market")
    def test_no_token_id(self, mock_get_market):
        mock_get_market.return_value = {
            "id": "1", "question": "Q", "active": True, "closed": False,
        }
        with patch.dict(os.environ, {"POLYMARKET_PRIVATE_KEY": "0xfake"}):
            t = AutoTrader.__new__(AutoTrader)
            t.side = "YES"
            t.market_id = "1"
            t._client = None
            with pytest.raises(SystemExit, match="Could not find token ID"):
                t._validate()


# -- Notify (Telegram) -------------------------------------------------------

class TestNotify:
    def test_skips_without_config(self, ready_trader):
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise
            ready_trader._notify("test message")

    @patch("urllib.request.urlopen")
    def test_sends_when_configured(self, mock_urlopen, ready_trader):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            ready_trader._notify("hello")
            mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen", side_effect=Exception("network error"))
    def test_does_not_raise_on_failure(self, mock_urlopen, ready_trader):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            ready_trader._notify("hello")  # should not raise


# -- Handle interrupt ---------------------------------------------------------

class TestHandleInterrupt:
    def test_sets_state(self, ready_trader):
        ready_trader._client.get_market.return_value = FAKE_MARKET
        ready_trader._handle_interrupt()
        assert ready_trader.running is False
        assert ready_trader.exit_reason == "user interrupted (Ctrl+C)"
        assert ready_trader.exit_time is not None

    def test_survives_api_failure(self, ready_trader):
        ready_trader._client.get_market.side_effect = Exception("timeout")
        ready_trader._handle_interrupt()
        assert ready_trader.running is False
        assert ready_trader.exit_price == ready_trader.entry_price


# -- Monitor loop (single iteration) -----------------------------------------

class TestMonitorLoop:
    def test_stops_on_resolution(self, ready_trader):
        ready_trader._client.get_market.return_value = RESOLVED_MARKET
        ready_trader._monitor_loop()
        assert ready_trader.running is False
        assert "resolved" in ready_trader.exit_reason

    def test_stops_on_stop_loss(self, ready_trader):
        crashed_market = {
            **FAKE_MARKET,
            "outcome_prices": [0.20, 0.80],  # YES dropped from 0.60 to 0.20
        }
        ready_trader._client.get_market.return_value = crashed_market
        ready_trader._monitor_loop()
        assert ready_trader.running is False
        assert "stop-loss" in ready_trader.exit_reason

    @patch("time.sleep", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_propagates(self, mock_sleep, ready_trader):
        ready_trader._client.get_market.return_value = FAKE_MARKET
        with pytest.raises(KeyboardInterrupt):
            ready_trader._monitor_loop()


# -- get_outcome_prices -------------------------------------------------------

class TestGetOutcomePrices:
    def test_from_outcome_prices(self, trader):
        trader.market = {"outcome_prices": [0.7, 0.3]}
        assert trader._get_outcome_prices() == [0.7, 0.3]

    def test_from_outcomePrices_json(self, trader):
        trader.market = {"outcomePrices": '["0.7","0.3"]'}
        assert trader._get_outcome_prices() == ["0.7", "0.3"]

    def test_empty_market(self, trader):
        trader.market = {}
        assert trader._get_outcome_prices() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
