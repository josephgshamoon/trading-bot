"""Data feed for Polymarket — fetches and structures market data."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger("trading_bot.data")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class DataFeed:
    """Manages fetching, caching, and structuring Polymarket data."""

    def __init__(self, client):
        self.client = client
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def get_active_markets(self, config: dict) -> list[dict]:
        """Fetch filtered active markets with price data attached."""
        return self.client.get_filtered_markets(config)

    def get_market_snapshot(self, market: dict) -> dict:
        """Create a point-in-time snapshot of a market.

        Returns a dict with standardized fields for strategy consumption.
        """
        prices = market.get("_prices") or self.client.get_market_prices(market)

        tokens = market.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except json.JSONDecodeError:
                tokens = []

        return {
            "market_id": market.get("id", ""),
            "condition_id": market.get("conditionId", ""),
            "question": market.get("question", ""),
            "slug": market.get("slug", ""),
            "yes_price": prices["yes_price"],
            "no_price": prices["no_price"],
            "spread": prices["spread"],
            "volume": float(market.get("volume", 0) or 0),
            "liquidity": float(market.get("liquidity", 0) or 0),
            "active": market.get("active", False),
            "closed": market.get("closed", False),
            "outcomes": market.get("outcomes", []),
            "token_ids": tokens,
            "end_date": market.get("endDate", ""),
            "category": market.get("groupItemTitle", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_all_snapshots(self, config: dict) -> list[dict]:
        """Get snapshots for all filtered markets."""
        markets = self.get_active_markets(config)
        snapshots = []
        for m in markets:
            try:
                snap = self.get_market_snapshot(m)
                snapshots.append(snap)
            except Exception as e:
                logger.warning(f"Error snapshotting market {m.get('id', '?')}: {e}")
        logger.info(f"Created {len(snapshots)} market snapshots")
        return snapshots

    def get_price_history_df(self, token_id: str) -> pd.DataFrame:
        """Fetch price history for a token and return as DataFrame.

        Columns: timestamp, price
        """
        history = self.client.get_price_history(token_id)
        if not history:
            return pd.DataFrame(columns=["timestamp", "price"])

        rows = []
        for entry in history:
            ts = entry.get("t", 0)
            price = float(entry.get("p", 0))
            rows.append({"timestamp": ts, "price": price})

        df = pd.DataFrame(rows)
        if not df.empty and df["timestamp"].dtype in ("int64", "float64"):
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("timestamp").sort_index()
        return df

    def save_snapshots(self, snapshots: list[dict], filename: str = "snapshots.json"):
        """Persist snapshots to disk for backtesting."""
        path = DATA_DIR / filename
        existing = []
        if path.exists():
            with open(path) as f:
                existing = json.load(f)

        existing.extend(snapshots)

        with open(path, "w") as f:
            json.dump(existing, f, indent=2, default=str)

        logger.info(f"Saved {len(snapshots)} snapshots to {path}")

    def load_snapshots(self, filename: str = "snapshots.json") -> list[dict]:
        """Load historical snapshots from disk."""
        path = DATA_DIR / filename
        if not path.exists():
            logger.warning(f"No snapshot file found at {path}")
            return []

        with open(path) as f:
            return json.load(f)
