"""Live trading engine — LOCKED behind multiple safety checks.

This engine connects to the real Polymarket CLOB API and executes
real trades with real USDC. It is deliberately conservative and
requires explicit confirmation before any trade.

IMPORTANT: Live trading is DISABLED by default. You must:
1. Set trading.mode = "live" in config
2. Set the POLYMARKET_LIVE_ENABLED=true environment variable
3. Have valid CLOB API credentials
"""

import os
import logging
from datetime import datetime, timezone

from ..risk.manager import RiskManager
from ..strategy.base import TradeSignal

logger = logging.getLogger("trading_bot.live")

LIVE_ENV_FLAG = "POLYMARKET_LIVE_ENABLED"


class LiveEngine:
    """Live trading engine with mandatory safety gates."""

    def __init__(self, config: dict, risk_manager: RiskManager):
        self.config = config
        self.risk = risk_manager
        self._enabled = False

        # Safety gate 1: Config check
        mode = config.get("trading", {}).get("mode", "paper")
        if mode != "live":
            logger.info("Live trading disabled — mode is not 'live'")
            return

        # Safety gate 2: Environment variable check
        env_flag = os.environ.get(LIVE_ENV_FLAG, "").lower()
        if env_flag != "true":
            logger.info(
                f"Live trading disabled — set {LIVE_ENV_FLAG}=true to enable"
            )
            return

        # Safety gate 3: API credentials check
        api_key = os.environ.get("POLYMARKET_API_KEY", "")
        api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
        if not api_key or not api_secret:
            logger.error("Live trading disabled — missing API credentials")
            return

        self._enabled = True
        logger.warning(
            "LIVE TRADING ENABLED — real money at risk. "
            "Kill switch available via risk manager."
        )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def execute_trade(self, signal: TradeSignal) -> dict:
        """Execute a live trade on Polymarket.

        For now, this is a placeholder that logs the trade intent.
        Full CLOB integration requires the py-clob-client library
        and wallet signing setup.
        """
        if not self._enabled:
            return {"error": "Live trading is not enabled"}

        # Risk check
        allowed, reason = self.risk.validate_trade(signal)
        if not allowed:
            logger.warning(f"Live trade blocked by risk manager: {reason}")
            return {"error": reason}

        logger.warning(
            f"LIVE TRADE INTENT: {signal.signal.value} "
            f"${signal.position_size_usdc:.2f} @ {signal.entry_price:.3f} | "
            f"{signal.question[:60]}..."
        )

        # TODO: Implement actual CLOB order placement
        # This requires:
        # 1. py-clob-client for Polymarket order signing
        # 2. Wallet private key for transaction signing
        # 3. USDC approval on Polygon
        #
        # For now, return a placeholder indicating the trade was logged
        # but not executed. This is intentional — live trading should
        # only be enabled after thorough paper trading validation.

        return {
            "status": "logged_not_executed",
            "message": (
                "Live CLOB integration pending. Trade intent logged. "
                "Implement CLOB order placement when ready."
            ),
            "signal": signal.signal.value,
            "market_id": signal.market_id,
            "size_usdc": signal.position_size_usdc,
            "entry_price": signal.entry_price,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_status(self) -> dict:
        """Return live engine status."""
        return {
            "enabled": self._enabled,
            "mode": self.config.get("trading", {}).get("mode", "paper"),
            "env_flag": os.environ.get(LIVE_ENV_FLAG, "not set"),
            "has_api_key": bool(os.environ.get("POLYMARKET_API_KEY", "")),
            "risk_status": self.risk.get_status(),
        }
