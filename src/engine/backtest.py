"""Backtesting engine for prediction market strategies.

Simulates strategy performance against historical snapshot data.
Binary outcomes are resolved probabilistically based on final market prices.
"""

import json
import logging
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from ..strategy.base import Signal, TradeSignal

logger = logging.getLogger("trading_bot.backtest")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class BacktestTrade:
    """Record of a single backtested trade."""
    market_id: str
    question: str
    signal: str
    entry_price: float
    position_usdc: float
    outcome: str  # "win" or "loss"
    pnl: float
    fees: float
    edge: float
    strategy: str
    timestamp: str = ""


@dataclass
class BacktestResult:
    """Comprehensive backtesting results."""
    strategy_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    net_pnl: float = 0.0
    win_rate: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    roi_pct: float = 0.0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"  BACKTEST RESULTS — {self.strategy_name}\n"
            f"{'='*60}\n"
            f"  Total Trades:     {self.total_trades}\n"
            f"  Wins / Losses:    {self.wins} / {self.losses}\n"
            f"  Win Rate:         {self.win_rate:.1%}\n"
            f"  Total PnL:        ${self.total_pnl:+.2f}\n"
            f"  Fees Paid:        ${self.total_fees:.2f}\n"
            f"  Net PnL:          ${self.net_pnl:+.2f}\n"
            f"  ROI:              {self.roi_pct:+.1f}%\n"
            f"  Avg Win:          ${self.avg_win:+.2f}\n"
            f"  Avg Loss:         ${self.avg_loss:+.2f}\n"
            f"  Profit Factor:    {self.profit_factor:.2f}\n"
            f"  Max Drawdown:     ${self.max_drawdown:.2f}\n"
            f"  Sharpe Ratio:     {self.sharpe_ratio:.2f}\n"
            f"  Starting Balance: ${self.starting_balance:.2f}\n"
            f"  Ending Balance:   ${self.ending_balance:.2f}\n"
            f"{'='*60}\n"
        )


class BacktestEngine:
    """Run strategy backtests on historical Polymarket snapshot data."""

    def __init__(self, config: dict):
        self.config = config
        bt_cfg = config.get("backtest", {})
        self.fee_pct = bt_cfg.get("fee_pct", 2.0)
        self.slippage_pct = bt_cfg.get("slippage_pct", 1.0)
        self.starting_balance = bt_cfg.get("starting_balance_usdc", 1000.0)

    def run(
        self,
        strategy,
        snapshots: list[dict],
        indicators_fn=None,
        seed: int | None = None,
    ) -> BacktestResult:
        """Run a backtest on historical snapshots.

        Args:
            strategy: Strategy instance with evaluate() method.
            snapshots: List of market snapshots (from DataFeed).
            indicators_fn: Optional function to compute indicators per snapshot.
            seed: Random seed for reproducible outcome simulation.

        Returns:
            BacktestResult with full trade history and metrics.
        """
        if seed is not None:
            random.seed(seed)

        balance = self.starting_balance
        peak_balance = balance
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        equity_curve = [balance]
        pnl_list = []

        logger.info(
            f"Running backtest: {strategy.name} on {len(snapshots)} snapshots, "
            f"balance=${balance:.2f}"
        )

        for snapshot in snapshots:
            # Compute indicators if function provided
            indicators = {}
            if indicators_fn:
                try:
                    indicators = indicators_fn(snapshot)
                except Exception as e:
                    logger.debug(f"Indicator computation failed: {e}")

            # Get strategy signal
            signal = strategy.evaluate(snapshot, indicators)
            if signal is None:
                continue

            # Check if we can afford the trade
            if signal.position_size_usdc > balance:
                continue

            # Simulate the trade
            trade = self._simulate_trade(signal, snapshot)
            trades.append(trade)
            pnl_list.append(trade.pnl)

            balance += trade.pnl
            equity_curve.append(balance)

            # Track drawdown
            peak_balance = max(peak_balance, balance)
            drawdown = peak_balance - balance
            max_drawdown = max(max_drawdown, drawdown)

            logger.debug(
                f"Trade: {trade.signal} {trade.question[:40]}... "
                f"pnl=${trade.pnl:+.2f} balance=${balance:.2f}"
            )

        # Compute final metrics
        result = self._compute_metrics(
            strategy.name, trades, pnl_list, equity_curve, max_drawdown
        )

        logger.info(result.summary())
        return result

    def _simulate_trade(self, signal: TradeSignal, snapshot: dict) -> BacktestTrade:
        """Simulate a single trade outcome.

        In a prediction market, the outcome is binary:
        - WIN: you paid entry_price, you get $1.00 per share
        - LOSS: you paid entry_price, you get $0.00

        We simulate outcomes based on the market's probability
        (i.e., the current market price IS the probability of YES).
        """
        entry = signal.entry_price
        size = signal.position_size_usdc

        # Apply slippage to entry price
        slippage = entry * (self.slippage_pct / 100.0)
        effective_entry = entry + slippage

        # Calculate fees
        fees = size * (self.fee_pct / 100.0)

        # Number of shares bought
        if effective_entry <= 0:
            shares = 0
        else:
            shares = (size - fees) / effective_entry

        # Determine outcome probabilistically
        # If we bought YES: probability of winning = yes_price
        # If we bought NO: probability of winning = no_price
        if signal.signal == Signal.BUY_YES:
            win_prob = snapshot["yes_price"]
        else:
            win_prob = snapshot["no_price"]

        won = random.random() < win_prob

        if won:
            # Each share pays out $1.00
            payout = shares * 1.0
            pnl = payout - size
            outcome = "win"
        else:
            # Shares are worthless
            pnl = -size
            outcome = "loss"

        return BacktestTrade(
            market_id=snapshot.get("market_id", ""),
            question=snapshot.get("question", ""),
            signal=signal.signal.value,
            entry_price=effective_entry,
            position_usdc=size,
            outcome=outcome,
            pnl=round(pnl, 4),
            fees=round(fees, 4),
            edge=signal.edge,
            strategy=signal.metadata.get("strategy", "unknown"),
            timestamp=snapshot.get("timestamp", ""),
        )

    def _compute_metrics(
        self,
        name: str,
        trades: list[BacktestTrade],
        pnl_list: list[float],
        equity_curve: list[float],
        max_drawdown: float,
    ) -> BacktestResult:
        """Calculate comprehensive performance metrics."""
        result = BacktestResult(
            strategy_name=name,
            starting_balance=self.starting_balance,
            trades=[asdict(t) for t in trades],
            equity_curve=equity_curve,
        )

        if not trades:
            result.ending_balance = self.starting_balance
            return result

        result.total_trades = len(trades)
        result.wins = sum(1 for t in trades if t.outcome == "win")
        result.losses = sum(1 for t in trades if t.outcome == "loss")
        result.total_fees = sum(t.fees for t in trades)

        wins_pnl = [t.pnl for t in trades if t.outcome == "win"]
        loss_pnl = [t.pnl for t in trades if t.outcome == "loss"]

        result.win_rate = result.wins / result.total_trades if result.total_trades else 0
        result.avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
        result.avg_loss = sum(loss_pnl) / len(loss_pnl) if loss_pnl else 0

        total_wins = sum(wins_pnl)
        total_losses = abs(sum(loss_pnl))
        result.profit_factor = total_wins / total_losses if total_losses > 0 else 0

        result.total_pnl = sum(pnl_list)
        result.net_pnl = result.total_pnl
        result.ending_balance = self.starting_balance + result.net_pnl
        result.roi_pct = (result.net_pnl / self.starting_balance) * 100
        result.max_drawdown = max_drawdown

        # Sharpe ratio (simplified — pnl per trade / std of pnl)
        if len(pnl_list) > 1:
            import numpy as np
            mean_pnl = np.mean(pnl_list)
            std_pnl = np.std(pnl_list, ddof=1)
            result.sharpe_ratio = (mean_pnl / std_pnl) if std_pnl > 0 else 0
        else:
            result.sharpe_ratio = 0

        return result

    def save_results(self, result: BacktestResult, filename: str | None = None):
        """Save backtest results to JSON."""
        if filename is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"backtest_{result.strategy_name}_{ts}.json"

        path = DATA_DIR / filename
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(asdict(result), f, indent=2, default=str)

        logger.info(f"Backtest results saved to {path}")
