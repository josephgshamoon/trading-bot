"""Live trading engine — executes real orders on Polymarket CLOB.

LOCKED behind multiple safety checks:
1. trading.mode must be "live" in config
2. POLYMARKET_LIVE_ENABLED=true environment variable
3. Valid CLOB API credentials (private key + derived API key/secret/passphrase)

Uses py-clob-client for order signing and placement on Polygon.

Proxy support:
    Set POLYMARKET_PROXY_URL in .env to route CLOB API requests through
    a proxy in a non-restricted country. This is required when the server
    is in a geo-blocked region (UK, US, France, etc.).
    Example: POLYMARKET_PROXY_URL=socks5://your-proxy-host:1080
"""

import json
import os
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    OrderArgs,
    MarketOrderArgs,
    OrderType,
    BalanceAllowanceParams,
    AssetType,
)

from ..data.feed import DataFeed
from ..data.indicators import MarketIndicators
from ..data.categorizer import MarketCategorizer
from ..risk.manager import RiskManager
from ..exchange.proxy import configure_clob_proxy, get_proxy_status
from ..notifications.telegram import TelegramNotifier
from ..strategy.base import TradeSignal, Signal
from ..strategy.news_enhanced import NewsEnhancedStrategy

logger = logging.getLogger("trading_bot.live")

LIVE_ENV_FLAG = "POLYMARKET_LIVE_ENABLED"
DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class LivePosition:
    """A live trading position."""
    trade_id: str
    market_id: str
    question: str
    signal: str
    token_id: str
    entry_price: float
    size_usdc: float
    shares: float
    order_id: str
    entry_time: str
    status: str = "open"  # open, won, lost, sold
    exit_time: str = ""
    pnl: float = 0.0


@dataclass
class LiveSession:
    """A live trading session."""
    session_id: str
    started: str
    strategy: str
    starting_balance: float
    current_balance: float = 0.0
    positions: list = field(default_factory=list)
    closed_trades: list = field(default_factory=list)
    total_pnl: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades else 0.0


class LiveEngine:
    """Live trading engine with CLOB order execution."""

    def __init__(self, config: dict, risk_manager: RiskManager):
        self.config = config
        self.risk = risk_manager
        self._enabled = False
        self._clob: ClobClient | None = None
        self.session: LiveSession | None = None
        self._session_path = DATA_DIR / "live_session.json"

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
        private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        api_key = os.environ.get("POLYMARKET_API_KEY", "")
        api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
        api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "")

        if not all([private_key, api_key, api_secret, api_passphrase]):
            logger.error("Live trading disabled — missing API credentials")
            return

        # Configure proxy BEFORE creating the CLOB client.
        # This replaces the py-clob-client internal httpx singleton
        # with one that routes through the proxy. Required when the
        # server is in a geo-blocked region (UK, US, France, etc.).
        try:
            proxy_configured = configure_clob_proxy()
            if proxy_configured:
                logger.info("CLOB proxy configured — orders will be routed through proxy")
            else:
                logger.debug(
                    "No proxy configured (set POLYMARKET_PROXY_URL if geo-blocked)"
                )
        except ImportError as e:
            logger.error(f"Proxy configuration failed: {e}")
            return
        except Exception as e:
            logger.error(f"Proxy configuration failed: {e}")
            return

        # Initialize CLOB client
        sig_type = int(os.environ.get("POLYMARKET_SIG_TYPE", "1"))
        funder = os.environ.get("POLYMARKET_FUNDER", "")
        try:
            clob_kwargs = dict(
                host=config.get("exchange", {}).get(
                    "clob_api_url", "https://clob.polymarket.com"
                ),
                chain_id=137,
                key=private_key,
                creds=ApiCreds(
                    api_key=api_key,
                    api_secret=api_secret,
                    api_passphrase=api_passphrase,
                ),
                signature_type=sig_type,
            )
            if funder:
                clob_kwargs["funder"] = funder
                logger.info(f"Using funder (proxy wallet): {funder}")
            self._clob = ClobClient(**clob_kwargs)
            # Verify connectivity
            ok = self._clob.get_ok()
            if ok != "OK":
                logger.error(f"CLOB health check failed: {ok}")
                return
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            return

        self._enabled = True
        self._sig_type = sig_type
        self._notifier = TelegramNotifier()
        logger.warning(
            "LIVE TRADING ENABLED — real money at risk. "
            "Kill switch available via risk manager."
        )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def get_balance(self) -> float:
        """Fetch current USDC balance from CLOB."""
        if not self._clob:
            return 0.0
        try:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self._sig_type,
            )
            bal = self._clob.get_balance_allowance(params)
            return int(bal["balance"]) / 1_000_000
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def start_session(self, strategy_name: str, balance: float):
        """Initialize a new live trading session."""
        self.risk.initialize_portfolio(balance)

        self.session = LiveSession(
            session_id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            started=datetime.now(timezone.utc).isoformat(),
            strategy=strategy_name,
            starting_balance=balance,
            current_balance=balance,
        )

        logger.info(
            f"Live trading session started: {self.session.session_id}, "
            f"strategy={strategy_name}, balance=${balance:.2f}"
        )

    def scan_markets(
        self,
        strategy,
        data_feed: DataFeed,
        news_context: dict[str, dict] | None = None,
    ) -> list[TradeSignal]:
        """Scan markets for trade signals (same as paper engine)."""
        if not self.session:
            raise RuntimeError("No active session. Call start_session() first.")

        snapshots = data_feed.get_all_snapshots(self.config)
        signals = []
        is_news_strategy = isinstance(strategy, NewsEnhancedStrategy)

        # Category filter from config
        allowed_categories = self.config.get("filters", {}).get("categories", [])

        for snap in snapshots:
            category = MarketCategorizer.categorize(
                snap.get("question", ""), snap.get("category", "")
            )
            snap["_category"] = category

            # Skip if not in allowed categories
            if allowed_categories and category not in allowed_categories:
                continue

            token_ids = snap.get("token_ids", [])
            if token_ids:
                try:
                    orderbook = data_feed.client.get_orderbook(token_ids[0])
                    snap["_orderbook_imbalance"] = MarketIndicators.orderbook_imbalance(orderbook)
                except Exception:
                    snap["_orderbook_imbalance"] = 0.0

            indicators = MarketIndicators.compute_all(snap)

            if token_ids:
                try:
                    hist = data_feed.get_price_history_df(token_ids[0])
                    if not hist.empty:
                        indicators = MarketIndicators.compute_all(snap, hist)
                except Exception:
                    pass

            if is_news_strategy and news_context:
                news_analysis = news_context.get(snap["market_id"])
                signal = strategy.evaluate(snap, indicators, news_analysis)
            else:
                signal = strategy.evaluate(snap, indicators)

            if signal is None:
                continue

            # Inject token_ids into signal metadata for order placement
            signal.metadata["token_ids"] = snap.get("token_ids", [])

            allowed, reason = self.risk.validate_trade(signal)
            if not allowed:
                logger.debug(f"Trade rejected by risk manager: {reason}")
                continue

            signals.append(signal)

        cat_label = ", ".join(allowed_categories) if allowed_categories else "all"
        logger.info(
            f"Market scan complete: {len(signals)} signals from "
            f"{len(snapshots)} markets (categories: {cat_label})"
        )
        return signals

    def execute_trade(self, signal: TradeSignal) -> dict:
        """Execute a real trade on the Polymarket CLOB.

        Places a limit order (GTC) at the signal's entry price.
        Uses market order as fallback if limit order doesn't fill.
        """
        if not self._enabled or not self._clob:
            return {"error": "Live trading is not enabled"}

        # Risk check
        allowed, reason = self.risk.validate_trade(signal)
        if not allowed:
            logger.warning(f"Live trade blocked by risk manager: {reason}")
            return {"error": reason}

        # Determine token ID and side
        # Short-term strategies set target_token_id directly
        target_token = signal.metadata.get("target_token_id", "")
        token_ids = signal.metadata.get("token_ids", [])

        if target_token:
            token_id = target_token
            side = "BUY"
            market_price = signal.metadata.get("market_price", signal.entry_price)
            price = market_price
        elif not token_ids or len(token_ids) < 2:
            logger.error(f"No token_ids in signal metadata for {signal.market_id}")
            return {"error": "Missing token_ids in signal metadata"}
        elif signal.signal == Signal.BUY_YES:
            token_id = token_ids[0]  # YES token
            side = "BUY"
            price = signal.entry_price
        else:  # BUY_NO
            token_id = token_ids[1]  # NO token
            side = "BUY"
            price = 1.0 - signal.entry_price  # NO price

        # Get tick size for this market
        try:
            tick_size = float(self._clob.get_tick_size(token_id))
        except Exception:
            tick_size = 0.01

        # Round price to valid tick
        price = round(round(price / tick_size) * tick_size, 4)
        price = max(tick_size, min(1.0 - tick_size, price))

        # Calculate shares: size_usdc / price
        shares = signal.position_size_usdc / price
        shares = round(shares, 2)

        if shares < 1:
            logger.warning(f"Order too small: {shares} shares at ${price}")
            return {"error": f"Order too small: {shares} shares"}

        # Get fee rate
        try:
            fee_rate_bps = self._clob.get_fee_rate_bps(token_id)
        except Exception:
            fee_rate_bps = 0

        logger.warning(
            f"PLACING LIVE ORDER: {side} {shares:.2f} shares of "
            f"{'YES' if signal.signal == Signal.BUY_YES else 'NO'} "
            f"@ ${price:.4f} (${signal.position_size_usdc:.2f}) | "
            f"{signal.question[:50]}..."
        )

        # Place limit order (GTC — stays on book until filled or cancelled)
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=shares,
                side=side,
                fee_rate_bps=fee_rate_bps,
            )

            signed_order = self._clob.create_order(order_args)
            response = self._clob.post_order(signed_order, orderType=OrderType.GTC)

            if isinstance(response, dict) and response.get("errorMsg"):
                logger.error(f"Order rejected: {response['errorMsg']}")
                return {"error": response["errorMsg"]}

            order_id = ""
            if isinstance(response, dict):
                order_id = response.get("orderID", response.get("id", ""))

            logger.info(f"Order placed: {order_id} | {response}")

        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return {"error": str(e)}

        # Record position
        trade_id = f"LT_{self.session.session_id}_{self.session.total_trades + 1}"

        position = {
            "trade_id": trade_id,
            "market_id": signal.market_id,
            "question": signal.question,
            "signal": signal.signal.value,
            "token_id": token_id,
            "entry_price": price,
            "size_usdc": signal.position_size_usdc,
            "shares": shares,
            "order_id": order_id,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "status": "open",
            "metadata": signal.metadata,
            "confidence": signal.confidence,
            "edge": signal.edge,
            "reason": signal.reason,
        }

        self.risk.record_trade_entry(signal, trade_id)
        self.session.positions.append(position)
        self.session.total_trades += 1
        self.session.current_balance = self.risk.portfolio.balance
        self._save_session()

        logger.info(
            f"Live trade recorded: {trade_id} | {signal.signal.value} "
            f"${signal.position_size_usdc:.2f} @ {price:.4f} | "
            f"order_id={order_id}"
        )

        # Send Telegram notification — only for first entry into a slot,
        # not for scale-ins (same market_id already open)
        is_scale_in = any(
            p.get("market_id") == signal.market_id and p.get("trade_id") != trade_id
            for p in self.session.positions if p.get("status") == "open"
        )
        if self._notifier.is_configured() and not is_scale_in:
            self._notifier.send_trade_alert({
                "signal": signal.signal.value,
                "question": signal.question,
                "entry_price": price,
                "position_size_usdc": signal.position_size_usdc,
                "shares": shares,
                "edge": signal.edge,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "trade_id": trade_id,
            })

        return {
            "status": "executed",
            "trade_id": trade_id,
            "order_id": order_id,
            "signal": signal.signal.value,
            "market_id": signal.market_id,
            "token_id": token_id,
            "price": price,
            "shares": shares,
            "size_usdc": signal.position_size_usdc,
            "response": response,
        }

    def check_and_resolve(self, gamma_client, journal=None) -> list[dict]:
        """Check open positions and resolve any closed markets.

        For resolved markets, the CLOB auto-settles — winning shares
        are redeemed for $1.00 each, losing shares become worthless.

        If *journal* is provided (a ``TradeJournal`` instance), each
        resolution is also logged with prediction accuracy data.
        """
        if not self.session:
            return []

        resolved = []
        still_open = []

        for pos in self.session.positions:
            if pos.get("status") != "open":
                still_open.append(pos)
                continue

            try:
                # Skip hex conditionId markets — Gamma API returns wrong
                # markets for these. They must be resolved via CLOB instead.
                if pos["market_id"].startswith("0x"):
                    still_open.append(pos)
                    continue

                market = gamma_client.get_market(pos["market_id"])
                if not market.get("closed", False):
                    still_open.append(pos)
                    continue

                # Market resolved
                prices = gamma_client.get_market_prices(market)
                final_yes = prices["yes_price"]

                if pos["signal"] == "BUY_YES":
                    won = final_yes > 0.5
                else:
                    won = final_yes < 0.5

                if won:
                    pnl = pos["shares"] * 1.0 - pos["size_usdc"]
                    pos["status"] = "won"
                    self.session.wins += 1
                else:
                    pnl = -pos["size_usdc"]
                    pos["status"] = "lost"
                    self.session.losses += 1

                pos["pnl"] = round(pnl, 4)
                pos["exit_time"] = datetime.now(timezone.utc).isoformat()

                self.risk.record_trade_exit(pos.get("trade_id", pos["market_id"]), pnl)
                self.session.total_pnl += pnl
                self.session.current_balance = self.risk.portfolio.balance
                self.session.closed_trades.append(pos)
                resolved.append(pos)

                logger.info(
                    f"Position resolved: {pos['trade_id']} "
                    f"{'WON' if won else 'LOST'} pnl=${pnl:+.2f}"
                )

                # Log to journal
                if journal is not None:
                    meta = pos.get("metadata", {})
                    entry_price = pos.get("entry_price", 0)
                    # Predicted probability = entry_price for BUY_YES,
                    # 1 - entry_price for BUY_NO
                    if pos["signal"] == "BUY_YES":
                        predicted_prob = entry_price
                    else:
                        predicted_prob = 1.0 - entry_price
                    journal.log_resolution(
                        trade_id=pos.get("trade_id", ""),
                        market_id=pos.get("market_id", ""),
                        question=pos.get("question", ""),
                        strategy=meta.get("strategy", self.session.strategy),
                        signal=pos["signal"],
                        entry_price=entry_price,
                        predicted_prob=predicted_prob,
                        predicted_edge=pos.get("edge", 0),
                        outcome=pos["status"],
                        pnl=pnl,
                        size_usdc=pos.get("size_usdc", 0),
                        metadata=meta,
                    )

                # Send Telegram notification for resolved position
                if self._notifier.is_configured():
                    emoji = "\U0001f389" if won else "\U0001f4a5"
                    q = pos.get('question', '')[:45]
                    self._notifier.send_message(
                        f"{emoji} {'WON' if won else 'LOST'} <b>${pnl:+.2f}</b> | {q}"
                    )
            except Exception as e:
                logger.error(f"Error checking position {pos.get('trade_id')}: {e}")
                still_open.append(pos)

        self.session.positions = still_open
        self._save_session()
        return resolved

    def sell_position(self, pos: dict, sell_price: float) -> dict:
        """Sell (close) an open position on the CLOB.

        Places a SELL limit order for the token we hold.
        Returns dict with status, pnl, etc.
        """
        if not self._enabled or not self._clob:
            return {"error": "Live trading is not enabled"}

        token_id = pos.get("token_id", "")
        shares = pos.get("shares", 0)
        if not token_id or shares <= 0:
            return {"error": "Invalid position — no token_id or shares"}

        try:
            tick_size = float(self._clob.get_tick_size(token_id))
        except Exception:
            tick_size = 0.01

        price = round(round(sell_price / tick_size) * tick_size, 4)
        price = max(tick_size, min(1.0 - tick_size, price))

        try:
            fee_rate_bps = self._clob.get_fee_rate_bps(token_id)
        except Exception:
            fee_rate_bps = 0

        logger.warning(
            f"SELLING POSITION: {shares:.2f} shares of "
            f"{pos.get('question', '')[:50]} @ ${price:.4f}"
        )

        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=shares,
                side="SELL",
                fee_rate_bps=fee_rate_bps,
            )

            signed_order = self._clob.create_order(order_args)
            response = self._clob.post_order(signed_order, orderType=OrderType.GTC)

            if isinstance(response, dict) and response.get("errorMsg"):
                logger.error(f"Sell order rejected: {response['errorMsg']}")
                return {"error": response["errorMsg"]}

            order_id = ""
            if isinstance(response, dict):
                order_id = response.get("orderID", response.get("id", ""))

            logger.info(f"Sell order placed: {order_id}")

        except Exception as e:
            logger.error(f"Sell order failed: {e}")
            return {"error": str(e)}

        # Calculate PnL: sell proceeds - cost
        proceeds = shares * price
        cost = pos.get("size_usdc", 0)
        pnl = proceeds - cost

        pos["status"] = "sold"
        pos["pnl"] = round(pnl, 4)
        pos["exit_time"] = datetime.now(timezone.utc).isoformat()
        pos["exit_price"] = price

        self.risk.record_trade_exit(pos.get("trade_id", pos.get("market_id", "")), pnl)
        if self.session:
            self.session.total_pnl += pnl
            if pnl >= 0:
                self.session.wins += 1
            else:
                self.session.losses += 1
            self.session.current_balance = self.risk.portfolio.balance
            self.session.closed_trades.append(pos)
            self.session.positions = [
                p for p in self.session.positions
                if p.get("trade_id") != pos.get("trade_id")
            ]
            self._save_session()

        # Send Telegram notification
        if self._notifier.is_configured():
            emoji = "\U0001f4b0" if pnl >= 0 else "\U0001f4a5"
            q = pos.get('question', '')[:45]
            roi_pct = (pnl / pos.get('size_usdc', 1)) * 100
            self._notifier.send_message(
                f"{emoji} SOLD <b>${pnl:+.2f}</b> ({roi_pct:+.0f}%) | {q}"
            )

        return {
            "status": "sold",
            "trade_id": pos.get("trade_id", ""),
            "order_id": order_id,
            "sell_price": price,
            "pnl": pnl,
        }

    def cancel_all_orders(self) -> dict:
        """Cancel all open orders on the CLOB."""
        if not self._clob:
            return {"error": "CLOB client not initialized"}
        try:
            result = self._clob.cancel_all()
            logger.warning(f"All orders cancelled: {result}")
            return {"status": "cancelled", "result": result}
        except Exception as e:
            logger.error(f"Failed to cancel orders: {e}")
            return {"error": str(e)}

    def get_summary(self) -> dict:
        """Get current session summary."""
        if not self.session:
            return {"error": "No active session"}

        return {
            "session_id": self.session.session_id,
            "strategy": self.session.strategy,
            "started": self.session.started,
            "starting_balance": self.session.starting_balance,
            "current_balance": self.session.current_balance,
            "total_pnl": round(self.session.total_pnl, 2),
            "total_trades": self.session.total_trades,
            "wins": self.session.wins,
            "losses": self.session.losses,
            "win_rate": f"{self.session.win_rate:.1%}",
            "open_positions": len([p for p in self.session.positions if p.get("status") == "open"]),
            "risk_status": self.risk.get_status(),
        }

    def get_status(self) -> dict:
        """Return live engine status."""
        return {
            "enabled": self._enabled,
            "mode": self.config.get("trading", {}).get("mode", "paper"),
            "env_flag": os.environ.get(LIVE_ENV_FLAG, "not set"),
            "has_api_key": bool(os.environ.get("POLYMARKET_API_KEY", "")),
            "proxy": get_proxy_status(),
            "risk_status": self.risk.get_status(),
        }

    def _save_session(self):
        """Persist session state to disk using atomic write.

        Writes to a temp file first, then renames — prevents corruption
        if two cron cycles overlap or the process is killed mid-write.
        """
        if not self.session:
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = self._session_path.with_suffix(".tmp")
        with open(tmp_path, "w") as f:
            json.dump(asdict(self.session), f, indent=2, default=str)
        tmp_path.replace(self._session_path)

    def load_session(self) -> bool:
        """Load an existing session from disk with corruption recovery."""
        if not self._session_path.exists():
            return False

        for attempt in range(3):
            try:
                with open(self._session_path) as f:
                    data = json.load(f)
                break
            except json.JSONDecodeError:
                if attempt < 2:
                    time.sleep(0.3)
                else:
                    logger.error("Session file corrupted — cannot load")
                    return False

        self.session = LiveSession(**{
            k: v for k, v in data.items()
            if k in LiveSession.__dataclass_fields__
        })

        self.risk.initialize_portfolio(self.session.current_balance)

        # Restore open positions to risk manager
        for pos in self.session.positions:
            if pos.get("status") == "open":
                self.risk.portfolio.open_positions.append({
                    "market_id": pos.get("market_id"),
                    "question": pos.get("question", ""),
                    "signal": pos.get("signal", ""),
                    "entry_price": pos.get("entry_price", 0),
                    "size_usdc": pos.get("size_usdc", 0),
                    "trade_id": pos.get("trade_id", ""),
                })

        open_count = len(self.risk.portfolio.open_positions)
        logger.info(
            f"Loaded live session: {self.session.session_id}, "
            f"{open_count} open positions restored to risk manager"
        )
        return True
