import yaml
"""
Paper Trading Runner for Polymarket Trading Bot
================================================
Runs the trading strategy in paper mode:
- Scans markets for signals
- Sends Telegram alerts for manual approval
- Tracks all decisions and outcomes
- Provides daily summaries

Safety: NO real trades, all decisions logged for analysis
"""

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import urllib.request
import urllib.parse

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.polymarket_client import PolymarketClient
from src.backtest import StrategyConfig


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class PaperTrade:
    """Record of a paper trade"""
    signal_id: str
    market_id: str
    market_question: str
    signal_time: datetime
    signal_strength: float
    probability: float
    position_size: float
    outcome: str  # "YES" or "NO"
    decision: str  # "APPROVED" or "REJECTED"
    reason: str = ""
    resolved: bool = False
    result: Optional[str] = None  # "WIN" or "LOSS"
    profit: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "signal_time": self.signal_time.isoformat(),
            "signal_strength": self.signal_strength,
            "probability": self.probability,
            "position_size": self.position_size,
            "outcome": self.outcome,
            "decision": self.decision,
            "reason": self.reason,
            "resolved": self.resolved,
            "result": self.result,
            "profit": self.profit
        }


@dataclass
class PaperTradingSession:
    """Paper trading session state"""
    start_time: datetime = field(default_factory=datetime.utcnow)
    trades: List[PaperTrade] = field(default_factory=list)
    signals_received: int = 0
    approved: int = 0
    rejected: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time": self.start_time.isoformat(),
            "total_trades": len(self.trades),
            "signals_received": self.signals_received,
            "approved": self.approved,
            "rejected": self.rejected,
            "wins": self.wins,
            "losses": self.losses,
            "net_pnl": round(self.net_pnl, 4),
            "win_rate": round(self.wins / self.approved * 100, 1) if self.approved > 0 else 0
        }


class TelegramNotifier:
    """Sends Telegram notifications"""
    
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send_message(self, text: str) -> bool:
        """Send a text message"""
        try:
            url = f"{self.base_url}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML"
            }).encode()
            
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    def send_trade_signal(self, signal: Dict[str, Any]) -> bool:
        """Send trade signal for approval"""
        question = signal.get("market_question", "Unknown")[:80]
        prob = signal.get("current_price", 0) * 100
        strength = signal.get("signal_strength", 0) * 100
        volume = signal.get("volume", 0)
        
        text = (
            f"🎯 <b>TRADE SIGNAL</b>\n\n"
            f"📊 <b>Market:</b> {question}...\n\n"
            f"📈 <b>Probability:</b> {prob:.1f}%\n"
            f"💪 <b>Signal Strength:</b> {strength:.0f}%\n"
            f"💰 <b>Volume:</b> ${volume:,.0f}\n\n"
            f"🤖 Reply with:\n"
            f"• <code>YES {signal['market_id'][:8]}</code> to approve YES\n"
            f"• <code>NO {signal['market_id'][:8]}</code> to approve NO\n"
            f"• <code>REJECT {signal['market_id'][:8]}</code> to skip"
        )
        
        return self.send_message(text)
    
    def send_daily_summary(self, session: PaperTradingSession) -> bool:
        """Send daily performance summary"""
        trades = session.trades
        if not trades:
            return self.send_message("📊 No trades today yet.")
        
        win_rate = session.wins / session.approved * 100 if session.approved > 0 else 0
        
        text = (
            f"📊 <b>DAILY SUMMARY</b>\n\n"
            f"📈 Signals: {session.signals_received}\n"
            f"✅ Approved: {session.approved}\n"
            f"❌ Rejected: {session.rejected}\n"
            f"🏆 Wins: {session.wins}\n"
            f"📉 Losses: {session.losses}\n"
            f"📊 Win Rate: {win_rate:.1f}%\n"
            f"💰 Net PnL: ${session.net_pnl:.4f}\n\n"
            f"<i>Paper trading session active since {session.start_time.strftime('%Y-%m-%d')}</i>"
        )
        
        return self.send_message(text)


class PaperTrader:
    """
    Paper trading orchestrator.
    
    Scans markets, generates signals, sends alerts, tracks decisions.
    """
    
    def __init__(self, config_path: str = "config/config.yaml"):
        # Load config
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        
        # Initialize components
        self.client = PolymarketClient()
        self.strategy_config = StrategyConfig(config_path)
        self.session = PaperTradingSession()
        
        # Telegram
        self.telegram = TelegramNotifier(
            bot_token=self.config["notifications"]["telegram"]["bot_token"],
            chat_id=self.config["notifications"]["telegram"]["chat_id"]
        )
        
        # State
        self.seen_signals = set()
        self.pending_approvals = {}  # market_id -> signal
        
        logger.info("Paper Trader initialized")
        logger.info(f"Mode: {self.config['mode']['automation_level']}")
    
    def scan_markets(self) -> List[Dict[str, Any]]:
        """Scan markets for trading signals"""
        markets = self.client.get_markets(limit=100)
        
        # Use backtest strategy to generate signals
        signals = self._generate_signals(markets)
        
        return signals
    
    def _generate_signals(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate trading signals using strategy"""
        signals = []
        
        for market in markets:
            try:
                volume = float(market.get("volume", 0))
                liquidity = float(market.get("liquidity", 0))
                probabilities = market.get("outcome_prices", [0.5, 0.5])
                
                if volume < self.strategy_config.min_volume:
                    continue
                
                if liquidity < self.strategy_config.min_liquidity:
                    continue
                
                yes_prob = float(probabilities[0]) if len(probabilities) > 0 else 0.5
                
                if yes_prob < self.strategy_config.min_probability:
                    continue
                if yes_prob > self.strategy_config.max_probability:
                    continue
                
                # Calculate signal strength
                strength = 0.0
                reasons = []
                
                if volume > self.strategy_config.min_volume * self.strategy_config.volume_multiplier:
                    strength += 0.3
                    reasons.append("High Volume")
                
                mid_range = abs(yes_prob - 0.5)
                if 0.2 < mid_range < 0.4:
                    strength += 0.3
                    reasons.append("Mid-Range Odds")
                
                if liquidity > self.strategy_config.min_liquidity * 2:
                    strength += 0.2
                    reasons.append("High Liquidity")
                
                if strength >= 0.3:
                    signal = {
                        "market_id": market.get("id"),
                        "market_question": market.get("question"),
                        "current_price": yes_prob,
                        "volume": volume,
                        "liquidity": liquidity,
                        "signal_strength": strength,
                        "reasons": reasons,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    signals.append(signal)
                    
            except Exception as e:
                logger.warning(f"Error processing market: {e}")
                continue
        
        return signals
    
    def process_new_signals(self, signals: List[Dict[str, Any]]) -> int:
        """Process new signals, send alerts for approval"""
        new_count = 0
        
        for signal in signals:
            signal_id = signal["market_id"]
            
            # Skip if already seen
            if signal_id in self.seen_signals:
                continue
            
            # Skip if pending approval (wait for decision)
            if signal_id in self.pending_approvals:
                continue
            
            # New signal!
            self.seen_signals.add(signal_id)
            self.session.signals_received += 1
            new_count += 1
            
            # Send Telegram alert
            self.telegram.send_trade_signal(signal)
            self.pending_approvals[signal_id] = signal
            
            logger.info(f"📨 Signal sent for approval: {signal['market_question'][:40]}...")
        
        return new_count
    
    def approve_trade(self, market_id: str, outcome: str, reason: str = "") -> bool:
        """Record an approved trade"""
        if market_id not in self.pending_approvals:
            logger.warning(f"Unknown market ID: {market_id}")
            return False
        
        signal = self.pending_approvals[market_id]
        
        trade = PaperTrade(
            signal_id=f"sig_{int(time.time())}",
            market_id=market_id,
            market_question=signal["market_question"],
            signal_time=datetime.fromisoformat(signal["timestamp"]),
            signal_strength=signal["signal_strength"],
            probability=signal["current_price"],
            position_size=self.strategy_config.position_size,
            outcome=outcome,
            decision="APPROVED",
            reason=reason
        )
        
        self.session.trades.append(trade)
        self.session.approved += 1
        del self.pending_approvals[market_id]
        
        # Simulate outcome (placeholder - in real paper trading, we'd wait for resolution)
        # For now, mark as unresolved
        logger.info(f"✅ Trade APPROVED: {outcome} on {trade.market_question[:30]}...")
        
        return True
    
    def reject_trade(self, market_id: str, reason: str = "") -> bool:
        """Record a rejected trade"""
        if market_id not in self.pending_approvals:
            logger.warning(f"Unknown market ID: {market_id}")
            return False
        
        signal = self.pending_approvals[market_id]
        
        trade = PaperTrade(
            signal_id=f"sig_{int(time.time())}",
            market_id=market_id,
            market_question=signal["market_question"],
            signal_time=datetime.fromisoformat(signal["timestamp"]),
            signal_strength=signal["signal_strength"],
            probability=signal["current_price"],
            position_size=self.strategy_config.position_size,
            outcome="N/A",
            decision="REJECTED",
            reason=reason
        )
        
        self.session.trades.append(trade)
        self.session.rejected += 1
        del self.pending_approvals[market_id]
        
        logger.info(f"❌ Trade REJECTED: {trade.market_question[:30]}...")
        
        return True
    
    def simulate_outcomes(self):
        """Simulate outcomes for approved trades (for testing)"""
        for trade in self.session.trades:
            if trade.resolved:
                continue
            
            # Simulate based on probability
            import random
            outcome_yes = random.random() < trade.probability
            
            actual_outcome = "YES" if outcome_yes else "NO"
            
            # Calculate profit
            fee = trade.position_size * self.strategy_config.fee_percent
            
            if trade.outcome == actual_outcome:
                # Win
                profit = trade.position_size * (1/trade.probability - 1) - fee
                trade.result = "WIN"
                trade.profit = profit
                self.session.wins += 1
                self.session.net_pnl += profit
            else:
                # Loss
                profit = -trade.position_size - fee
                trade.result = "LOSS"
                trade.profit = profit
                self.session.losses += 1
                self.session.net_pnl += profit
            
            trade.resolved = True
            logger.info(f"📊 Resolved: {trade.result} (${trade.profit:.4f}) on {trade.market_question[:30]}...")
    
    def run_cycle(self):
        """Run one scan cycle"""
        logger.info("🔍 Scanning markets...")
        
        signals = self.scan_markets()
        new_signals = self.process_new_signals(signals)
        
        if new_signals > 0:
            logger.info(f"📨 Sent {new_signals} new signal(s) for approval")
        
        return new_signals
    
    def save_session(self, filepath: str = "data/paper_trading_session.json"):
        """Save session state to file"""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        output = {
            "session": self.session.to_dict(),
            "trades": [t.to_dict() for t in self.session.trades],
            "pending_approvals": {
                k: v["market_question"] for k, v in self.pending_approvals.items()
            }
        }
        
        with open(filepath, 'w') as f:
            json.dump(output, f, indent=2)
        
        logger.info(f"💾 Session saved to {filepath}")
    
    def send_summary(self):
        """Send daily summary to Telegram"""
        self.telegram.send_daily_summary(self.session)


def run_paper_trader():
    """Main entry point for paper trading"""
    print("\n" + "=" * 60)
    print("🤖 POLYMARKET PAPER TRADING RUNNER")
    print("=" * 60 + "\n")
    
    trader = PaperTrader()
    
    print("📊 Paper Trading Session Started")
    print(f"   Signals will be sent to Telegram for approval")
    print(f"   Reply with: YES <id>, NO <id>, or REJECT <id>")
    print(f"\n💡 Tip: Run 'python3 src/paper_trader.py --scan' for one-time scan")
    print("=" * 60)
    
    # Run one cycle
    new = trader.run_cycle()
    
    if new > 0:
        print(f"\n📨 {new} signal(s) sent to Telegram for approval")
    
    # Save session
    trader.save_session()
    
    # Show pending approvals
    if trader.pending_approvals:
        print(f"\n⏳ Pending Approvals:")
        for mid, sig in trader.pending_approvals.items():
            print(f"   [{mid[:8]}] {sig['market_question'][:40]}...")
    
    return trader


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Paper Trading Runner")
    parser.add_argument("--scan", action="store_true", help="Run single scan")
    parser.add_argument("--approve", metavar="ID", help="Approve trade")
    parser.add_argument("--reject", metavar="ID", help="Reject trade")
    parser.add_argument("--summary", action="store_true", help="Show summary")
    parser.add_argument("--simulate", action="store_true", help="Simulate outcomes")
    
    args = parser.parse_args()
    
    trader = PaperTrader()
    
    if args.scan:
        trader.run_cycle()
        trader.save_session()
    elif args.approve:
        trader.approve_trade(args.approve, "YES", "Manual approval")
    elif args.reject:
        trader.reject_trade(args.reject, "Manual rejection")
    elif args.summary:
        print(json.dumps(trader.session.to_dict(), indent=2))
    elif args.simulate:
        trader.simulate_outcomes()
        trader.save_session()
    else:
        run_paper_trader()
