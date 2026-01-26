"""
Backtest Harness for Polymarket Trading Bot
============================================
Tests trading strategies against historical market data.

Metrics computed:
- Sharpe Ratio
- Max Drawdown
- Win Rate
- Profit Factor
- Average Trade
- Total Exposure
- Fees/Slippage assumptions
"""

import json
import random
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import math


def cumsum(values):
    """Calculate cumulative sum"""
    result = []
    total = 0
    for v in values:
        total += v
        result.append(total)
    return result


def calc_running_max(values):
    """Calculate running maximum"""
    result = []
    current = 0
    for v in values:
        if v > current:
            current = v
        result.append(current)
    return result


def mean(values):
    """Calculate mean of a list"""
    return sum(values) / len(values) if values else 0


def std(values):
    """Calculate standard deviation"""
    if len(values) < 2:
        return 0
    avg = mean(values)
    variance = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def max_drawdown(values):
    """Calculate maximum drawdown from cumulative values"""
    if not values:
        return 0
    running_max = 0
    max_dd = 0
    for val in values:
        if val > running_max:
            running_max = val
        dd = running_max - val
        if dd > max_dd:
            max_dd = dd
    return max_dd
import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.polymarket_client import PolymarketClient


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single trade"""
    market_id: str
    market_question: str
    entry_time: datetime
    entry_price: float  # Probability (0-1)
    position_size: float  # USDC
    outcome: str  # "YES" or "NO"
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    profit: float = 0.0
    resolved: bool = False
    win: Optional[bool] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "market_id": self.market_id,
            "market_question": self.market_question,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price,
            "position_size": self.position_size,
            "outcome": self.outcome,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": self.exit_price,
            "profit": self.profit,
            "resolved": self.resolved,
            "win": self.win
        }


@dataclass
class BacktestResult:
    """Results from a backtest run"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_profit: float = 0.0
    total_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    average_trade: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    total_exposure: float = 0.0
    fees_paid: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate * 100, 2),
            "total_profit": round(self.total_profit, 4),
            "total_loss": round(self.total_loss, 4),
            "net_pnl": round(self.net_pnl, 4),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "average_trade": round(self.average_trade, 4),
            "average_win": round(self.average_win, 4),
            "average_loss": round(self.average_loss, 4),
            "total_exposure": round(self.total_exposure, 4),
            "fees_paid": round(self.fees_paid, 4),
            "trades": [t.to_dict() for t in self.trades]
        }


class StrategyConfig:
    """Strategy configuration"""
    def __init__(self, config_path: str = "config/config.yaml"):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        
        # Strategy parameters
        self.min_probability = self.config.get("strategy", {}).get("min_probability", 0.10)
        self.max_probability = self.config.get("strategy", {}).get("max_probability", 0.90)
        self.position_size = self.config.get("strategy", {}).get("position_size", 1.0)
        
        # Signal thresholds
        self.volume_multiplier = self.config.get("strategy", {}).get("volume_multiplier", 2.0)
        self.odds_change_threshold = self.config.get("strategy", {}).get("odds_change_threshold", 0.05)
        self.min_liquidity = self.config.get("strategy", {}).get("min_liquidity", 1000)
        
        # Risk parameters
        self.max_daily_trades = self.config.get("risk", {}).get("max_trades_per_day", 2)
        self.max_daily_loss_percent = self.config.get("risk", {}).get("max_daily_loss_percent", 20) / 100
        
        # Market type filter
        self.min_volume = self.config.get("strategy", {}).get("min_volume", 50000)
        
        # Fees and slippage
        self.fee_percent = self.config.get("strategy", {}).get("fee_percent", 0.02)  # 2% fee
        self.slippage_percent = self.config.get("strategy", {}).get("slippage_percent", 0.01)  # 1% slippage


class PolymarketBacktester:
    """
    Backtesting engine for Polymarket trading strategies.
    
    Simulates trading on historical market data to validate strategy performance.
    """
    
    def __init__(self, config: StrategyConfig = None):
        self.config = config or StrategyConfig()
        self.client = PolymarketClient()
        self.results = BacktestResult()
        
        logger.info(f"Backtester initialized with config:")
        logger.info(f"  Probability range: {self.config.min_probability}-{self.config.max_probability}")
        logger.info(f"  Position size: ${self.config.position_size}")
        logger.info(f"  Min volume: ${self.config.min_volume}")
    
    def fetch_historical_data(self, days: int = 90) -> List[Dict[str, Any]]:
        """
        Fetch historical market data for backtesting.
        
        Note: Polymarket API provides current data. For true historical backtesting,
        we would need historical price databases. This implementation uses
        available data and simulates historical scenarios.
        """
        logger.info(f"Fetching market data (last {days} days)...")
        
        markets = self.client.get_markets(limit=100)
        
        # Filter for high-volume markets
        high_volume_markets = [
            m for m in markets 
            if float(m.get("volume", 0)) >= self.config.min_volume
        ]
        
        logger.info(f"Found {len(high_volume_markets)} high-volume markets")
        
        return high_volume_markets
    
    def generate_trading_signals(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Generate trading signals based on strategy rules.
        
        Signal criteria:
        1. Probability between min and max threshold
        2. Volume above minimum
        3. Liquidity above minimum
        4. Volume spike or odds movement detected
        """
        signals = []
        
        for market in markets:
            try:
                volume = float(market.get("volume", 0))
                liquidity = float(market.get("liquidity", 0))
                probabilities = market.get("outcome_prices", [0.5, 0.5])
                
                # Skip if volume too low
                if volume < self.config.min_volume:
                    continue
                
                # Skip if liquidity too low
                if liquidity < self.config.min_liquidity:
                    continue
                
                # Get current probability (Yes outcome)
                yes_prob = float(probabilities[0]) if len(probabilities) > 0 else 0.5
                no_prob = float(probabilities[1]) if len(probabilities) > 1 else 0.5
                
                # Check probability range
                if yes_prob < self.config.min_probability or yes_prob > self.config.max_probability:
                    continue
                
                # Calculate signal strength (0-1)
                signal_strength = 0.0
                reasons = []
                
                # Signal 1: Volume indicator (normalized)
                if volume > self.config.min_volume * self.config.volume_multiplier:
                    signal_strength += 0.3
                    reasons.append("High volume")
                
                # Signal 2: Mid-range probability (more uncertainty = more opportunity)
                mid_range = abs(yes_prob - 0.5)
                if 0.2 < mid_range < 0.4:  # Not too certain, not too uncertain
                    signal_strength += 0.3
                    reasons.append("Mid-range odds")
                
                # Signal 3: Liquidity indicator
                if liquidity > self.config.min_liquidity * 2:
                    signal_strength += 0.2
                    reasons.append("High liquidity")
                
                # Signal 4: Time-based (newer markets might have more movement)
                # Note: Would need market creation date for this
                
                # Only generate signal if strength meets threshold
                if signal_strength >= 0.3:
                    signal = {
                        "market_id": market.get("id"),
                        "market_question": market.get("question"),
                        "current_price": yes_prob,
                        "volume": volume,
                        "liquidity": liquidity,
                        "signal_strength": signal_strength,
                        "reasons": reasons,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    signals.append(signal)
                    
            except Exception as e:
                logger.warning(f"Error processing market: {e}")
                continue
        
        logger.info(f"Generated {len(signals)} trading signals")
        return signals
    
    def execute_trade(self, signal: Dict[str, Any]) -> Trade:
        """
        Simulate executing a trade based on a signal.
        """
        # Apply slippage to entry price
        entry_price = signal["current_price"]
        slippage = entry_price * self.config.slippage_percent
        entry_price_adjusted = entry_price + slippage if entry_price < 0.5 else entry_price - slippage
        
        # Clamp to valid range
        entry_price_adjusted = max(0.01, min(0.99, entry_price_adjusted))
        
        # Determine outcome (simulated - in real backtest, we'd know the result)
        # For simulation, we use a probability-based outcome
        outcome_yes = random.random() < entry_price_adjusted
        outcome = "YES" if outcome_yes else "NO"
        
        # Calculate profit
        position_size = self.config.position_size
        fee = position_size * self.config.fee_percent
        
        if outcome == "YES":
            profit = position_size * (1 / entry_price_adjusted - 1) - fee
        else:
            profit = -position_size - fee
        
        trade = Trade(
            market_id=signal["market_id"],
            market_question=signal["market_question"],
            entry_time=datetime.utcnow(),
            entry_price=entry_price,
            position_size=position_size,
            outcome=outcome,
            profit=profit,
            resolved=True,
            win=profit > 0
        )
        
        return trade
    
    def run_backtest(self, markets: List[Dict[str, Any]] = None) -> BacktestResult:
        """
        Run the complete backtest on historical data.
        """
        logger.info("=" * 60)
        logger.info("STARTING BACKTEST")
        logger.info("=" * 60)
        
        # Get market data
        if markets is None:
            markets = self.fetch_historical_data()
        
        if not markets:
            logger.warning("No markets available for backtesting")
            return self.results
        
        # Generate signals
        signals = self.generate_trading_signals(markets)
        
        if not signals:
            logger.warning("No signals generated - strategy may be too restrictive")
            return self.results
        
        # Execute trades (simulated)
        logger.info(f"Executing {len(signals)} trades...")
        
        daily_trades = {}
        for signal in signals:
            # Check daily trade limit
            today = datetime.utcnow().date().isoformat()
            daily_count = daily_trades.get(today, 0)
            
            if daily_count >= self.config.max_daily_trades:
                logger.debug(f"Daily trade limit reached for {today}")
                continue
            
            # Execute trade
            trade = self.execute_trade(signal)
            self.results.trades.append(trade)
            daily_trades[today] = daily_count + 1
            
            logger.info(f"Trade: {trade.outcome} | PnL: ${trade.profit:.4f}")
        
        # Calculate metrics
        self._calculate_metrics()
        
        # Log results
        self._log_results()
        
        return self.results
    
    def _calculate_metrics(self):
        """Calculate performance metrics from executed trades"""
        trades = self.results.trades
        
        if not trades:
            return
        
        # Basic counts
        self.results.total_trades = len(trades)
        self.results.winning_trades = sum(1 for t in trades if t.profit > 0)
        self.results.losing_trades = sum(1 for t in trades if t.profit <= 0)
        
        # Win rate
        self.results.win_rate = self.results.winning_trades / self.results.total_trades
        
        # PnL calculations
        profits = [t.profit for t in trades]
        self.results.total_profit = sum(p for p in profits if p > 0)
        self.results.total_loss = abs(sum(p for p in profits if p < 0))
        self.results.net_pnl = sum(profits)
        
        # Profit factor
        if self.results.total_loss > 0:
            self.results.profit_factor = self.results.total_profit / self.results.total_loss
        else:
            self.results.profit_factor = float('inf') if self.results.total_profit > 0 else 0
        
        # Average trade
        self.results.average_trade = mean(profits)
        
        # Average win/loss
        wins = [t.profit for t in trades if t.profit > 0]
        losses = [t.profit for t in trades if t.profit <= 0]
        
        self.results.average_win = mean(wins) if wins else 0
        self.results.average_loss = mean(losses) if losses else 0
        
        # Sharpe Ratio (annualized)
        if std(profits) > 0:
            daily_returns = profits  # Simplified - assumes 1 trade per "day"
            sharpe = mean(daily_returns) / std(daily_returns) * math.sqrt(252)
            self.results.sharpe_ratio = sharpe
        else:
            self.results.sharpe_ratio = 0
        
        # Max Drawdown
        cumulative_pnl = cumsum(profits)
        running_max = calc_running_max(cumulative_pnl)
        drawdowns = [rm - cp for rm, cp in zip(running_max, cumulative_pnl)]
        self.results.max_drawdown = max(drawdowns) if len(drawdowns) > 0 else 0
        
        # Total exposure (sum of position sizes)
        self.results.total_exposure = sum(t.position_size for t in trades)
        
        # Fees
        self.results.fees_paid = sum(t.position_size * self.config.fee_percent for t in trades)
    
    def _log_results(self):
        """Log backtest results"""
        r = self.results
        
        logger.info("=" * 60)
        logger.info("BACKTEST RESULTS")
        logger.info("=" * 60)
        logger.info(f"Total Trades: {r.total_trades}")
        logger.info(f"Win Rate: {r.win_rate*100:.1f}%")
        logger.info(f"Net PnL: ${r.net_pnl:.4f}")
        logger.info(f"Profit Factor: {r.profit_factor:.2f}")
        logger.info(f"Sharpe Ratio: {r.sharpe_ratio:.2f}")
        logger.info(f"Max Drawdown: {r.max_drawdown*100:.2f}%")
        logger.info(f"Average Trade: ${r.average_trade:.4f}")
        logger.info(f"Total Exposure: ${r.total_exposure:.2f}")
        logger.info(f"Fees Paid: ${r.fees_paid:.4f}")
        logger.info("=" * 60)
    
    def save_results(self, filepath: str = "data/backtest_results.json"):
        """Save backtest results to file"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'w') as f:
            json.dump(self.results.to_dict(), f, indent=2)
        
        logger.info(f"Results saved to {filepath}")
        return filepath


def run_quick_backtest():
    """Run a quick backtest with default settings"""
    print("\n" + "=" * 60)
    print("POLYMARKET TRADING BOT - QUICK BACKTEST")
    print("=" * 60 + "\n")
    
    # Initialize
    config = StrategyConfig()
    backtester = PolymarketBacktester(config)
    
    # Run backtest
    results = backtester.run_backtest()
    
    # Save results
    output_file = backtester.save_results()
    
    print(f"\n✅ Backtest complete!")
    print(f"📊 Results saved to: {output_file}")
    print(f"\nKey Metrics:")
    print(f"  - Win Rate: {results.win_rate*100:.1f}%")
    print(f"  - Net PnL: ${results.net_pnl:.4f}")
    print(f"  - Profit Factor: {results.profit_factor:.2f}")
    print(f"  - Sharpe Ratio: {results.sharpe_ratio:.2f}")
    print(f"  - Max Drawdown: {results.max_drawdown*100:.2f}%")
    
    return results


if __name__ == "__main__":
    run_quick_backtest()
