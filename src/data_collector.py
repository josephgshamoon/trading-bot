#!/usr/bin/env python3
"""
Polymarket Data Collector
========================
Collects market data hourly and builds historical database for backtesting.
Runs autonomously in background.
"""

import json
import csv
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.polymarket_client import PolymarketClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/data_collector.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class DataCollector:
    """Collects and stores Polymarket data for backtesting."""
    
    def __init__(self, data_dir: str = "data"):
        self.client = PolymarketClient()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "market_history.csv"
        
        # Initialize CSV if not exists
        self._init_csv()
        
    def _init_csv(self):
        """Initialize CSV with headers if not exists."""
        if not self.history_file.exists():
            with open(self.history_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp',
                    'market_id',
                    'question',
                    'yes_prob',
                    'no_prob',
                    'volume',
                    'liquidity',
                    'outcome',
                    'active'
                ])
            logger.info(f"Created history file: {self.history_file}")
    
    def fetch_markets(self) -> List[Dict[str, Any]]:
        """Fetch all active markets."""
        try:
            markets = self.client.get_markets(limit=100)
            logger.info(f"Fetched {len(markets)} markets")
            return markets
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []
    
    def collect_snapshot(self) -> int:
        """Take a snapshot of all market data."""
        markets = self.fetch_markets()
        now = datetime.utcnow().isoformat()
        collected = 0
        
        for market in markets:
            try:
                market_id = market.get('id', '')
                if not market_id:
                    continue
                    
                probabilities = market.get('outcome_prices', [0.5, 0.5])
                yes_prob = float(probabilities[0]) if len(probabilities) > 0 else 0.5
                no_prob = float(probabilities[1]) if len(probabilities) > 1 else 0.5
                
                # Check if market has resolved (has outcome)
                outcome = market.get('outcome', '')
                active = not bool(outcome)
                
                with open(self.history_file, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        now,
                        market_id,
                        market.get('question', '')[:200],  # Truncate long questions
                        round(yes_prob, 4),
                        round(no_prob, 4),
                        market.get('volume', 0),
                        market.get('liquidity', 0),
                        outcome,
                        active
                    ])
                
                collected += 1
                
            except Exception as e:
                logger.warning(f"Error processing market {market_id}: {e}")
                continue
        
        logger.info(f"Collected {collected} market snapshots")
        return collected
    
    def get_history(self, market_id: str = None, days: int = None) -> List[Dict[str, Any]]:
        """Retrieve historical data."""
        if not self.history_file.exists():
            return []
        
        with open(self.history_file, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        
        # Filter by market
        if market_id:
            rows = [r for r in rows if r['market_id'] == market_id]
        
        # Filter by date
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            rows = [r for r in rows if r['timestamp'] >= cutoff]
        
        return rows
    
    def get_price_history(self, market_id: str) -> List[Dict[str, float]]:
        """Get price history for a specific market."""
        rows = self.get_history(market_id)
        return [
            {
                'timestamp': r['timestamp'],
                'yes_prob': float(r['yes_prob']),
                'no_prob': float(r['no_prob']),
                'volume': float(r['volume'])
            }
            for r in rows
        ]


def run_collection():
    """Run one data collection cycle."""
    logger.info("=" * 60)
    logger.info("Starting data collection cycle")
    logger.info("=" * 60)
    
    collector = DataCollector()
    count = collector.collect_snapshot()
    
    logger.info(f"Cycle complete. Collected {count} snapshots.")
    return count


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Polymarket Data Collector')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--continuous', action='store_true', help='Run continuously')
    parser.add_argument('--interval', type=int, default=3600, help='Interval in seconds (default: 1 hour)')
    
    args = parser.parse_args()
    
    if args.once:
        run_collection()
    elif args.continuous:
        logger.info(f"Starting continuous mode (interval: {args.interval}s)")
        while True:
            try:
                run_collection()
                time.sleep(args.interval)
            except KeyboardInterrupt:
                logger.info("Stopping collector")
                break
    else:
        # Default: run once
        run_collection()


if __name__ == "__main__":
    main()
