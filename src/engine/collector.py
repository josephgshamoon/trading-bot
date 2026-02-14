"""Resolved market data collector — fetches closed markets with actual outcomes.

This is the foundation for edge discovery. By collecting markets that have
already resolved (YES won or NO won), we can:
1. Backtest strategies against ground truth outcomes
2. Calibrate our probability estimates (do markets at 0.60 actually resolve YES 60%?)
3. Find systematic mispricings across market categories and price ranges
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("trading_bot.collector")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def collect_resolved_markets(client, max_pages: int = 10) -> list[dict]:
    """Fetch closed/resolved markets from Polymarket with actual outcomes.

    Collects markets that have already resolved (closed=True) and records:
    - The market question and metadata
    - The YES/NO prices at some point before resolution
    - The actual outcome (did YES or NO win?)

    Args:
        client: PolymarketClient instance.
        max_pages: Maximum pages to fetch (100 markets per page).

    Returns:
        List of resolved market dicts with 'resolved_yes' boolean.
    """
    resolved = []
    page_size = 100

    for page in range(max_pages):
        try:
            batch = client.get_markets(
                limit=page_size,
                offset=page * page_size,
                active=False,
                closed=True,
            )
        except Exception as e:
            logger.error(f"Error fetching page {page}: {e}")
            break

        if not batch:
            break

        for market in batch:
            try:
                parsed = _parse_resolved_market(market, client)
                if parsed:
                    resolved.append(parsed)
            except Exception as e:
                logger.debug(f"Skipping market {market.get('id', '?')}: {e}")

        logger.info(f"Page {page+1}: collected {len(resolved)} resolved markets so far")

        if len(batch) < page_size:
            break

        # Rate limit
        time.sleep(0.5)

    logger.info(f"Total resolved markets collected: {len(resolved)}")
    return resolved


def _parse_resolved_market(market: dict, client) -> dict | None:
    """Parse a closed market into a standardized resolved snapshot.

    Determines the actual outcome from the final prices:
    - YES price near 1.0 = YES won
    - YES price near 0.0 = NO won
    - Anything ambiguous is skipped
    """
    if not market.get("closed", False):
        return None

    prices = client.get_market_prices(market)
    yes_price = prices["yes_price"]
    no_price = prices["no_price"]

    # Determine resolution from final prices
    # Resolved markets should have prices near 0 or 1
    if yes_price >= 0.90:
        resolved_yes = True
    elif yes_price <= 0.10:
        resolved_yes = False
    else:
        # Market closed but not clearly resolved (cancelled/ambiguous)
        return None

    # Parse token IDs
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
        "yes_price": yes_price,
        "no_price": no_price,
        "spread": abs(1.0 - yes_price - no_price),
        "volume": float(market.get("volume", 0) or 0),
        "liquidity": float(market.get("liquidity", 0) or 0),
        "active": False,
        "closed": True,
        "resolved_yes": resolved_yes,
        "outcomes": market.get("outcomes", []),
        "token_ids": tokens,
        "end_date": market.get("endDate", ""),
        "category": market.get("groupItemTitle", ""),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def save_resolved_markets(resolved: list[dict], filename: str = "resolved_markets.json"):
    """Save resolved market data, merging with any existing data."""
    path = DATA_DIR / filename
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing = []
    existing_ids = set()
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
        existing_ids = {m["market_id"] for m in existing}

    # Add only new markets
    new_count = 0
    for market in resolved:
        if market["market_id"] not in existing_ids:
            existing.append(market)
            new_count += 1

    with open(path, "w") as f:
        json.dump(existing, f, indent=2)

    total = len(existing)
    logger.info(f"Saved {new_count} new resolved markets ({total} total)")
    return new_count, total


def load_resolved_markets(filename: str = "resolved_markets.json") -> list[dict]:
    """Load resolved market data from disk."""
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def calibration_analysis(resolved: list[dict]) -> dict:
    """Analyze prediction market calibration across price buckets.

    For each price bucket (0.10-0.20, 0.20-0.30, etc.), calculates:
    - How many markets fell in that bucket
    - What fraction actually resolved YES
    - The "calibration error" (difference between price and actual rate)

    A perfectly calibrated market would have markets priced at 0.60
    resolving YES exactly 60% of the time.

    Returns a dict with calibration data, category analysis, and volume analysis.
    """
    if not resolved:
        return {"error": "No resolved markets to analyze"}

    # Price bucket calibration
    buckets = {}
    for i in range(10):
        low = i * 0.10
        high = (i + 1) * 0.10
        label = f"{low:.1f}-{high:.1f}"
        buckets[label] = {"count": 0, "yes_count": 0, "markets": []}

    # Category analysis
    categories = {}

    # Volume tier analysis
    volume_tiers = {
        "micro (<$10K)": {"min": 0, "max": 10_000, "count": 0, "yes_count": 0, "total_price": 0},
        "small ($10K-$100K)": {"min": 10_000, "max": 100_000, "count": 0, "yes_count": 0, "total_price": 0},
        "medium ($100K-$1M)": {"min": 100_000, "max": 1_000_000, "count": 0, "yes_count": 0, "total_price": 0},
        "large (>$1M)": {"min": 1_000_000, "max": float("inf"), "count": 0, "yes_count": 0, "total_price": 0},
    }

    for market in resolved:
        yes_price = market.get("yes_price", 0)
        is_yes = market.get("resolved_yes", False)
        volume = market.get("volume", 0)
        category = market.get("category", "Unknown") or "Unknown"

        # Note: for resolved markets, yes_price is the FINAL price (near 0 or 1)
        # We need the PRE-RESOLUTION price to do proper calibration.
        # For now, we can still analyze category and volume patterns.
        # TODO: collect pre-resolution snapshots for proper calibration

        # Category analysis
        if category not in categories:
            categories[category] = {
                "count": 0, "yes_wins": 0, "no_wins": 0,
                "total_volume": 0, "avg_volume": 0,
            }
        cat = categories[category]
        cat["count"] += 1
        cat["total_volume"] += volume
        if is_yes:
            cat["yes_wins"] += 1
        else:
            cat["no_wins"] += 1

        # Volume tier analysis
        for tier_name, tier in volume_tiers.items():
            if tier["min"] <= volume < tier["max"]:
                tier["count"] += 1
                if is_yes:
                    tier["yes_count"] += 1
                break

    # Compute averages for categories
    for cat in categories.values():
        if cat["count"] > 0:
            cat["avg_volume"] = round(cat["total_volume"] / cat["count"], 2)
            cat["yes_rate"] = round(cat["yes_wins"] / cat["count"], 3)

    # Compute volume tier stats
    for tier in volume_tiers.values():
        if tier["count"] > 0:
            tier["yes_rate"] = round(tier["yes_count"] / tier["count"], 3)
        else:
            tier["yes_rate"] = 0

    return {
        "total_markets": len(resolved),
        "yes_resolved": sum(1 for m in resolved if m.get("resolved_yes")),
        "no_resolved": sum(1 for m in resolved if not m.get("resolved_yes")),
        "categories": categories,
        "volume_tiers": {
            k: {"count": v["count"], "yes_rate": v["yes_rate"]}
            for k, v in volume_tiers.items()
        },
    }


def format_calibration_report(analysis: dict) -> str:
    """Format calibration analysis into a human-readable report."""
    if "error" in analysis:
        return f"\n  {analysis['error']}\n"

    lines = [
        f"\n{'='*60}",
        f"  MARKET CALIBRATION ANALYSIS",
        f"{'='*60}",
        f"  Total Resolved Markets: {analysis['total_markets']}",
        f"  YES wins: {analysis['yes_resolved']}  |  NO wins: {analysis['no_resolved']}",
        f"  Overall YES rate: {analysis['yes_resolved']/analysis['total_markets']:.1%}",
    ]

    # Category breakdown
    categories = analysis.get("categories", {})
    if categories:
        lines.append(f"\n  {'─'*56}")
        lines.append(f"  CATEGORY BREAKDOWN")
        lines.append(f"  {'─'*56}")

        # Sort by count descending
        sorted_cats = sorted(categories.items(), key=lambda x: x[1]["count"], reverse=True)
        for name, data in sorted_cats[:15]:
            lines.append(
                f"  {name[:30]:<30} n={data['count']:>4}  "
                f"YES={data.get('yes_rate', 0):.0%}  "
                f"avg_vol=${data['avg_volume']:>12,.0f}"
            )

    # Volume tier breakdown
    tiers = analysis.get("volume_tiers", {})
    if tiers:
        lines.append(f"\n  {'─'*56}")
        lines.append(f"  VOLUME TIER ANALYSIS")
        lines.append(f"  {'─'*56}")
        for name, data in tiers.items():
            if data["count"] > 0:
                lines.append(
                    f"  {name:<25} n={data['count']:>4}  YES rate={data['yes_rate']:.0%}"
                )

    lines.append(f"{'='*60}\n")
    return "\n".join(lines)
