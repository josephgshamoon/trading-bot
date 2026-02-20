"""
Auto-Trading Engine for Polymarket
====================================
Continuous trading loop: place order, monitor position, enforce risk limits,
report via Telegram.

Usage (via run.py):
    python3 run.py auto <MARKET_ID> --side yes --amount 1 --interval 60
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _setup_file_logger():
    """Add a file handler for auto_trader logs if not already present."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "auto_trader.log")

    for h in logger.handlers:
        if isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_file):
            return  # already attached

    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)


class AutoTrader:
    """Continuous auto-trading engine for a single Polymarket market.

    Validates environment, places an entry order, then monitors in a loop —
    checking price, P&L, stop-loss, and market resolution — until stopped.
    """

    def __init__(
        self,
        market_id: str,
        side: str,
        amount: float,
        interval: int = 60,
        stop_loss_pct: float = 50.0,
    ):
        self.market_id = market_id
        self.side = side.upper()  # "YES" or "NO"
        self.amount = amount
        self.interval = interval
        self.stop_loss_pct = stop_loss_pct

        # Filled during validation / entry
        self.market: Dict[str, Any] = {}
        self.token_id: str = ""
        self.entry_price: float = 0.0
        self.entry_time: Optional[datetime] = None
        self.exit_price: Optional[float] = None
        self.exit_time: Optional[datetime] = None
        self.exit_reason: str = ""
        self.running = False

        # Lazy-loaded helpers
        self._client = None
        self._trader = None

        _setup_file_logger()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def start(self):
        """Validate, place entry order, and run the monitoring loop."""
        self._validate()
        self._place_entry()
        try:
            self._monitor_loop()
        except KeyboardInterrupt:
            self._handle_interrupt()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self):
        """Check env, market exists, market is open, and resolve token IDs."""
        # Private key
        if not os.environ.get("POLYMARKET_PRIVATE_KEY"):
            raise SystemExit(
                "\n  Error: POLYMARKET_PRIVATE_KEY not set.\n"
                "  Copy .env.example to .env and add your wallet private key.\n"
            )

        # Side
        if self.side not in ("YES", "NO"):
            raise SystemExit(f"\n  Error: Invalid side '{self.side}'. Use 'yes' or 'no'.\n")

        # Fetch market
        from src.polymarket_client import PolymarketClient

        self._client = PolymarketClient()
        try:
            self.market = self._client.get_market(self.market_id)
        except Exception as e:
            raise SystemExit(f"\n  Error: Could not fetch market {self.market_id}: {e}\n")

        if not self.market:
            raise SystemExit(f"\n  Error: Market '{self.market_id}' not found.\n")

        # Market must be open
        if self.market.get("closed", False) or not self.market.get("active", True):
            raise SystemExit(
                f"\n  Error: Market is closed or inactive.\n"
                f"  Question: {self.market.get('question', '')}\n"
            )

        # Resolve token ID for chosen side
        self.token_id = self._resolve_token_id()
        if not self.token_id:
            raise SystemExit(
                "\n  Error: Could not find token ID for this market.\n"
                "  Try `python3 run.py market <ID>` to inspect available tokens.\n"
            )

        logger.info(
            "Validation passed: market=%s side=%s amount=%.2f token=%s",
            self.market_id,
            self.side,
            self.amount,
            self.token_id[:16],
        )

    def _resolve_token_id(self) -> str:
        """Extract the correct token ID for self.side from market data."""
        tokens = self._parse_json_field(self.market.get("tokens"))
        clob_tokens = self._parse_json_field(self.market.get("clobTokenIds"))
        outcomes = self._parse_json_field(self.market.get("outcomes"), ["YES", "NO"])

        token_map: Dict[str, str] = {}
        if tokens and isinstance(tokens[0], dict):
            for t in tokens:
                token_map[t.get("outcome", "").upper()] = t.get("token_id", "")
        elif clob_tokens:
            for i, tid in enumerate(clob_tokens):
                label = outcomes[i].upper() if i < len(outcomes) else f"OUTCOME_{i}"
                token_map[label] = tid

        return token_map.get(self.side, "")

    # ------------------------------------------------------------------
    # Entry order
    # ------------------------------------------------------------------

    def _place_entry(self):
        """Place the buy order via RealTrader."""
        from src.trader import RealTrader

        self._trader = RealTrader()

        probs = self._get_outcome_prices()
        yes_price = float(probs[0]) if probs else 0.5
        self.entry_price = yes_price if self.side == "YES" else (1 - yes_price)

        logger.info("Placing entry: BUY %s $%.2f @ %.1f%%", self.side, self.amount, self.entry_price * 100)

        result = self._trader.place_market_order(
            token_id=self.token_id,
            side="BUY",
            size=self.amount,
        )

        self.entry_time = datetime.now()
        self.running = True

        print(f"\n  Order placed: BUY {self.side} ${self.amount:.2f} @ {self.entry_price*100:.1f}%")
        print(f"  Token: {self.token_id[:20]}...")
        print(f"  Response: {result}")

        self._notify(
            f"AUTO-TRADE ENTRY\n"
            f"Market: {self.market.get('question', '')[:60]}\n"
            f"Side: {self.side}  Amount: ${self.amount:.2f}  Price: {self.entry_price*100:.1f}%"
        )

    # ------------------------------------------------------------------
    # Monitor loop
    # ------------------------------------------------------------------

    def _monitor_loop(self):
        """Continuous loop: fetch price, check stops, check resolution."""
        print(f"\n  Monitoring every {self.interval}s (Ctrl+C to stop)...\n")
        logger.info("Monitor loop started (interval=%ds, stop_loss=%.0f%%)", self.interval, self.stop_loss_pct)

        while self.running:
            try:
                # Refresh market data (clear cache to get fresh prices)
                self._client.clear_cache()
                market = self._client.get_market(self.market_id)

                # Check resolution
                if self._check_resolution(market):
                    return

                # Current price
                probs = market.get("outcome_prices") or []
                if not probs:
                    raw = market.get("outcomePrices") or market.get("outcome_prices")
                    probs = self._parse_json_field(raw)

                if probs:
                    yes_price = float(probs[0])
                    current_price = yes_price if self.side == "YES" else (1 - yes_price)
                else:
                    current_price = self.entry_price  # fallback

                self._print_status(current_price)

                # Check stop-loss
                if self._check_stop_loss(current_price):
                    return

            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("Monitor error: %s", e)
                print(f"  [!] Monitor error: {e}")

            time.sleep(self.interval)

    # ------------------------------------------------------------------
    # Stop-loss
    # ------------------------------------------------------------------

    def _check_stop_loss(self, current_price: float) -> bool:
        """Exit if price has dropped below entry by more than stop_loss_pct."""
        if self.entry_price <= 0:
            return False

        loss_pct = ((self.entry_price - current_price) / self.entry_price) * 100
        if loss_pct >= self.stop_loss_pct:
            print(f"\n  STOP-LOSS triggered at {loss_pct:.1f}% loss (limit: {self.stop_loss_pct:.0f}%)")
            logger.warning("Stop-loss triggered: loss=%.1f%% limit=%.0f%%", loss_pct, self.stop_loss_pct)
            self._exit_position(f"stop-loss ({loss_pct:.1f}% loss)")
            return True
        return False

    # ------------------------------------------------------------------
    # Resolution check
    # ------------------------------------------------------------------

    def _check_resolution(self, market: Dict[str, Any]) -> bool:
        """Detect if the market has been closed/resolved."""
        closed = market.get("closed", False)
        resolved = market.get("resolved", False)

        if closed or resolved:
            resolution = market.get("resolution", "unknown")
            print(f"\n  MARKET RESOLVED: {resolution}")
            logger.info("Market resolved: %s", resolution)
            self._exit_position(f"market resolved ({resolution})")
            return True
        return False

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def _exit_position(self, reason: str):
        """Record the exit and attempt to sell the position."""
        self.running = False
        self.exit_reason = reason
        self.exit_time = datetime.now()

        # Get current price for P&L
        try:
            self._client.clear_cache()
            market = self._client.get_market(self.market_id)
            probs = market.get("outcome_prices") or []
            if not probs:
                raw = market.get("outcomePrices") or market.get("outcome_prices")
                probs = self._parse_json_field(raw)
            if probs:
                yes_price = float(probs[0])
                self.exit_price = yes_price if self.side == "YES" else (1 - yes_price)
        except Exception:
            pass

        if self.exit_price is None:
            self.exit_price = 0.0

        # Attempt to sell position
        if reason.startswith("stop-loss"):
            try:
                logger.info("Selling position (reason: %s)", reason)
                self._trader.place_market_order(
                    token_id=self.token_id,
                    side="SELL",
                    size=self.amount,
                )
                print(f"  Sell order placed.")
            except Exception as e:
                logger.error("Failed to sell position: %s", e)
                print(f"  [!] Failed to sell: {e}")

        self._print_summary()

        self._notify(
            f"AUTO-TRADE EXIT\n"
            f"Market: {self.market.get('question', '')[:60]}\n"
            f"Reason: {reason}\n"
            f"Entry: {self.entry_price*100:.1f}%  Exit: {self.exit_price*100:.1f}%\n"
            f"P&L: {self._calc_pnl():+.4f} USDC"
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _print_status(self, current_price: float):
        """Print live P&L line."""
        pnl = self._calc_pnl_at(current_price)
        loss_pct = ((self.entry_price - current_price) / self.entry_price * 100) if self.entry_price > 0 else 0
        elapsed = (datetime.now() - self.entry_time).total_seconds() if self.entry_time else 0
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        print(
            f"  [{mins:02d}:{secs:02d}] "
            f"Price: {current_price*100:.1f}%  "
            f"Entry: {self.entry_price*100:.1f}%  "
            f"P&L: {pnl:+.4f} USDC  "
            f"({loss_pct:+.1f}%)"
        )

    def _print_summary(self):
        """Final P&L summary."""
        pnl = self._calc_pnl()
        duration = ""
        if self.entry_time and self.exit_time:
            delta = self.exit_time - self.entry_time
            mins = int(delta.total_seconds() // 60)
            secs = int(delta.total_seconds() % 60)
            duration = f"{mins}m {secs}s"

        print(f"\n{'='*50}")
        print(f"  AUTO-TRADE SUMMARY")
        print(f"{'='*50}")
        print(f"  Market:    {self.market.get('question', '')[:55]}")
        print(f"  Side:      {self.side}")
        print(f"  Amount:    ${self.amount:.2f}")
        print(f"  Entry:     {self.entry_price*100:.1f}%")
        print(f"  Exit:      {self.exit_price*100:.1f}%" if self.exit_price else "  Exit:      --")
        print(f"  P&L:       {pnl:+.4f} USDC")
        print(f"  Reason:    {self.exit_reason}")
        if duration:
            print(f"  Duration:  {duration}")
        print(f"{'='*50}\n")

        logger.info(
            "Auto-trade complete: side=%s amount=%.2f entry=%.4f exit=%.4f pnl=%.4f reason=%s",
            self.side,
            self.amount,
            self.entry_price,
            self.exit_price or 0,
            pnl,
            self.exit_reason,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calc_pnl(self) -> float:
        """Calculate final P&L based on entry and exit prices."""
        if self.exit_price is None:
            return 0.0
        return self._calc_pnl_at(self.exit_price)

    def _calc_pnl_at(self, current_price: float) -> float:
        """Calculate P&L at a given price.

        For a BUY at entry_price, shares = amount / entry_price.
        Current value = shares * current_price.
        P&L = current_value - amount.
        """
        if self.entry_price <= 0:
            return 0.0
        shares = self.amount / self.entry_price
        return (shares * current_price) - self.amount

    def _get_outcome_prices(self) -> list:
        """Extract outcome prices from self.market."""
        probs = self.market.get("outcomePrices") or self.market.get("outcome_prices")
        return self._parse_json_field(probs)

    def _notify(self, message: str):
        """Send Telegram notification if configured."""
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not bot_token or not chat_id:
            return
        try:
            import urllib.request
            import urllib.parse

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": message,
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            logger.debug("Telegram notification failed (non-fatal)")

    def _handle_interrupt(self):
        """Handle Ctrl+C gracefully."""
        print("\n\n  Interrupted by user.")
        logger.info("User interrupted auto-trade")

        # Get final price for summary
        try:
            self._client.clear_cache()
            market = self._client.get_market(self.market_id)
            probs = market.get("outcome_prices") or []
            if not probs:
                raw = market.get("outcomePrices") or market.get("outcome_prices")
                probs = self._parse_json_field(raw)
            if probs:
                yes_price = float(probs[0])
                self.exit_price = yes_price if self.side == "YES" else (1 - yes_price)
        except Exception:
            self.exit_price = self.entry_price

        self.exit_time = datetime.now()
        self.exit_reason = "user interrupted (Ctrl+C)"
        self.running = False
        self._print_summary()

        self._notify(
            f"AUTO-TRADE STOPPED\n"
            f"Market: {self.market.get('question', '')[:60]}\n"
            f"Reason: user interrupted\n"
            f"P&L: {self._calc_pnl():+.4f} USDC"
        )

    @staticmethod
    def _parse_json_field(value, default=None):
        """Parse a field that may be a JSON string, a list, or None."""
        if value is None:
            return default if default is not None else []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return default if default is not None else []
