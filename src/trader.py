"""
Real Trading Module for Polymarket
===================================
Wraps py-clob-client for order placement on Polymarket's CLOB.

Requires:
  pip install py-clob-client
  Environment variables: POLYMARKET_PRIVATE_KEY (required),
                          POLYMARKET_FUNDER, POLYMARKET_SIGNATURE_TYPE (optional)
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RealTrader:
    """Places real orders on Polymarket via the CLOB API."""

    def __init__(self):
        # Import py-clob-client here so browsing/paper trading never needs it
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs
        except ImportError:
            raise ImportError(
                "py-clob-client is required for real trading.\n"
                "Install it with: pip install py-clob-client"
            )

        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        if not private_key:
            raise ValueError(
                "POLYMARKET_PRIVATE_KEY not set.\n"
                "Copy .env.example to .env and add your wallet private key."
            )

        funder = os.environ.get("POLYMARKET_FUNDER", "")
        sig_type = int(os.environ.get("POLYMARKET_SIGNATURE_TYPE", "0"))

        host = "https://clob.polymarket.com"
        chain_id = 137  # Polygon mainnet

        self.client = ClobClient(
            host,
            key=private_key,
            chain_id=chain_id,
            funder=funder if funder else None,
            signature_type=sig_type,
        )
        self._OrderArgs = OrderArgs

        logger.info("RealTrader initialized (Polygon mainnet)")

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> Dict[str, Any]:
        """Place a limit order.

        Args:
            token_id: The CLOB token ID (YES or NO token).
            side: "BUY" or "SELL".
            price: Limit price (0.01 - 0.99).
            size: Amount in USDC.

        Returns:
            Order response dict from the API.
        """
        order_args = self._OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
        )
        signed = self.client.create_and_post_order(order_args)
        logger.info(f"Limit order placed: {side} {size} @ {price} on {token_id[:12]}...")
        return signed

    def place_market_order(
        self,
        token_id: str,
        side: str,
        size: float,
    ) -> Dict[str, Any]:
        """Place a market order (limit at 0.99 for BUY, 0.01 for SELL).

        Args:
            token_id: The CLOB token ID.
            side: "BUY" or "SELL".
            size: Amount in USDC.

        Returns:
            Order response dict.
        """
        price = 0.99 if side.upper() == "BUY" else 0.01
        return self.place_limit_order(token_id, side, price, size)

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Get all open orders for this account."""
        resp = self.client.get_orders()
        orders = resp if isinstance(resp, list) else resp.get("orders", [])
        return orders

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel a specific order by ID."""
        resp = self.client.cancel(order_id)
        logger.info(f"Cancelled order {order_id}")
        return resp

    def cancel_all(self) -> List[Dict[str, Any]]:
        """Cancel all open orders."""
        resp = self.client.cancel_all()
        logger.info("Cancelled all open orders")
        return resp
