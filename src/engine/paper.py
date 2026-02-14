"""Paper trading engine — run strategies against live market data without real money."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict

from ..data.feed import DataFeed
from ..data.indicators import MarketIndicators
from ..risk.manager import RiskManager
from ..strategy.base import TradeSignal
from ..strategy.news_enhanced import NewsEnhancedStrategy

logger = logging.getLogger("trading_bot.paper")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class PaperPosition:
    """A paper trading position."""
    trade_id: str
    market_id: str
    question: str
    signal: str
    entry_price: float
    size_usdc: float
    shares: float
    entry_time: str
    status: str = "open"  # open, won, lost
    exit_time: str = ""
    pnl: float = 0.0


@dataclass
class PaperSession:
    """A paper trading session."""
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


class PaperEngine:
    """Run strategies on live data with simulated trades."""

    def __init__(self, config: dict, data_feed: DataFeed, risk_manager: RiskManager):
        self.config = config
        self.feed = data_feed
        self.risk = risk_manager
        self.session: PaperSession | None = None
        self._session_path = DATA_DIR / "paper_session.json"

    def start_session(self, strategy_name: str, balance: float):
        """Initialize a new paper trading session."""
        self.risk.initialize_portfolio(balance)

        self.session = PaperSession(
            session_id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            started=datetime.now(timezone.utc).isoformat(),
            strategy=strategy_name,
            starting_balance=balance,
            current_balance=balance,
        )

        logger.info(
            f"Paper trading session started: {self.session.session_id}, "
            f"strategy={strategy_name}, balance=${balance:.2f}"
        )

    def scan_markets(
        self,
        strategy,
        news_context: dict[str, dict] | None = None,
    ) -> list[TradeSignal]:
        """Scan all filtered markets and return trade signals.

        Args:
            strategy: The strategy to evaluate markets with.
            news_context: Optional dict mapping market_id -> news analysis.
                Used by NewsEnhancedStrategy for information edge.
        """
        if not self.session:
            raise RuntimeError("No active session. Call start_session() first.")

        snapshots = self.feed.get_all_snapshots(self.config)
        signals = []
        is_news_strategy = isinstance(strategy, NewsEnhancedStrategy)

        for snap in snapshots:
            # Compute indicators
            indicators = MarketIndicators.compute_all(snap)

            # Try to get price history for richer indicators
            token_ids = snap.get("token_ids", [])
            if token_ids:
                try:
                    hist = self.feed.get_price_history_df(token_ids[0])
                    if not hist.empty:
                        indicators = MarketIndicators.compute_all(snap, hist)
                except Exception:
                    pass

            # Evaluate strategy (with news if available)
            if is_news_strategy and news_context:
                news_analysis = news_context.get(snap["market_id"])
                signal = strategy.evaluate(snap, indicators, news_analysis)
            else:
                signal = strategy.evaluate(snap, indicators)

            if signal is None:
                continue

            # Risk check
            allowed, reason = self.risk.validate_trade(signal)
            if not allowed:
                logger.debug(f"Trade rejected by risk manager: {reason}")
                continue

            signals.append(signal)

        logger.info(f"Market scan complete: {len(signals)} signals from {len(snapshots)} markets")
        return signals

    def execute_signal(self, signal: TradeSignal) -> PaperPosition | None:
        """Execute a paper trade from a signal."""
        if not self.session:
            raise RuntimeError("No active session.")

        allowed, reason = self.risk.validate_trade(signal)
        if not allowed:
            logger.warning(f"Trade blocked: {reason}")
            return None

        # Calculate shares
        fee_pct = self.config.get("backtest", {}).get("fee_pct", 2.0) / 100.0
        effective_size = signal.position_size_usdc * (1 - fee_pct)
        shares = effective_size / signal.entry_price if signal.entry_price > 0 else 0

        trade_id = f"PT_{self.session.session_id}_{self.session.total_trades + 1}"

        position = PaperPosition(
            trade_id=trade_id,
            market_id=signal.market_id,
            question=signal.question,
            signal=signal.signal.value,
            entry_price=signal.entry_price,
            size_usdc=signal.position_size_usdc,
            shares=shares,
            entry_time=datetime.now(timezone.utc).isoformat(),
        )

        # Record with risk manager
        self.risk.record_trade_entry(signal, trade_id)

        self.session.positions.append(asdict(position))
        self.session.total_trades += 1
        self.session.current_balance = self.risk.portfolio.balance

        logger.info(
            f"Paper trade executed: {trade_id} | {signal.signal.value} "
            f"${signal.position_size_usdc:.2f} @ {signal.entry_price:.3f} | "
            f"{signal.question[:50]}..."
        )

        self._save_session()
        return position

    def check_and_resolve(self, client) -> list[dict]:
        """Check open positions against current market state and resolve any closed markets."""
        if not self.session:
            return []

        resolved = []
        still_open = []

        for pos_dict in self.session.positions:
            if pos_dict.get("status") != "open":
                still_open.append(pos_dict)
                continue

            try:
                market = client.get_market(pos_dict["market_id"])
                if not market.get("closed", False):
                    still_open.append(pos_dict)
                    continue

                # Market resolved — determine outcome
                prices = client.get_market_prices(market)
                final_yes = prices["yes_price"]

                if pos_dict["signal"] == "BUY_YES":
                    won = final_yes > 0.5
                else:
                    won = final_yes < 0.5

                if won:
                    pnl = pos_dict["shares"] * 1.0 - pos_dict["size_usdc"]
                    pos_dict["status"] = "won"
                    self.session.wins += 1
                else:
                    pnl = -pos_dict["size_usdc"]
                    pos_dict["status"] = "lost"
                    self.session.losses += 1

                pos_dict["pnl"] = round(pnl, 4)
                pos_dict["exit_time"] = datetime.now(timezone.utc).isoformat()

                self.risk.record_trade_exit(pos_dict["market_id"], pnl)
                self.session.total_pnl += pnl
                self.session.current_balance = self.risk.portfolio.balance
                self.session.closed_trades.append(pos_dict)
                resolved.append(pos_dict)

                logger.info(
                    f"Position resolved: {pos_dict['trade_id']} "
                    f"{'WON' if won else 'LOST'} pnl=${pnl:+.2f}"
                )
            except Exception as e:
                logger.error(f"Error checking position {pos_dict.get('trade_id')}: {e}")
                still_open.append(pos_dict)

        self.session.positions = still_open
        self._save_session()
        return resolved

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

    def _save_session(self):
        """Persist session state to disk."""
        if not self.session:
            return

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._session_path, "w") as f:
            json.dump(asdict(self.session), f, indent=2, default=str)

    def load_session(self) -> bool:
        """Load an existing session from disk."""
        if not self._session_path.exists():
            return False

        with open(self._session_path) as f:
            data = json.load(f)

        self.session = PaperSession(**{
            k: v for k, v in data.items()
            if k in PaperSession.__dataclass_fields__
        })

        self.risk.initialize_portfolio(self.session.current_balance)
        logger.info(f"Loaded paper session: {self.session.session_id}")
        return True
