"""Generate realistic synthetic Polymarket snapshot data for backtesting.

This script creates market snapshots that mirror real Polymarket data structure,
with realistic price distributions, volume, and liquidity patterns.
Used when live API access is unavailable (e.g., sandbox environments).
"""

import json
import random
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Realistic market questions spanning common Polymarket categories
MARKETS = [
    # Politics
    {"q": "Will the US federal government shut down before April 2026?", "cat": "Politics", "base_prob": 0.35},
    {"q": "Will Ukraine and Russia agree to a ceasefire by June 2026?", "cat": "Politics", "base_prob": 0.22},
    {"q": "Will the US Senate confirm the next Supreme Court nominee before July 2026?", "cat": "Politics", "base_prob": 0.68},
    {"q": "Will a new US federal AI regulation bill pass in 2026?", "cat": "Politics", "base_prob": 0.30},
    {"q": "Will the UK call a snap general election in 2026?", "cat": "Politics", "base_prob": 0.12},
    {"q": "Will the US rejoin the Paris Climate Agreement in 2026?", "cat": "Politics", "base_prob": 0.18},
    # Crypto
    {"q": "Will Bitcoin exceed $150,000 by March 2026?", "cat": "Crypto", "base_prob": 0.42},
    {"q": "Will Ethereum exceed $10,000 by June 2026?", "cat": "Crypto", "base_prob": 0.28},
    {"q": "Will a Bitcoin spot ETF see $1B daily volume before April 2026?", "cat": "Crypto", "base_prob": 0.55},
    {"q": "Will Solana flip Ethereum in daily DEX volume by May 2026?", "cat": "Crypto", "base_prob": 0.20},
    {"q": "Will the SEC approve a Solana spot ETF in 2026?", "cat": "Crypto", "base_prob": 0.35},
    # Sports
    {"q": "Will the Kansas City Chiefs win Super Bowl LXI?", "cat": "Sports", "base_prob": 0.15},
    {"q": "Will Real Madrid win the Champions League 2025/26?", "cat": "Sports", "base_prob": 0.22},
    {"q": "Will Shohei Ohtani win the NL MVP in 2026?", "cat": "Sports", "base_prob": 0.30},
    {"q": "Will a Premier League team go unbeaten in 2025/26?", "cat": "Sports", "base_prob": 0.05},
    # Science & Tech
    {"q": "Will SpaceX Starship complete an orbital flight by April 2026?", "cat": "Science", "base_prob": 0.72},
    {"q": "Will OpenAI release GPT-5 before July 2026?", "cat": "Science", "base_prob": 0.55},
    {"q": "Will Neuralink receive FDA approval for a consumer device in 2026?", "cat": "Science", "base_prob": 0.08},
    {"q": "Will global average temperature in 2026 exceed 2025 record?", "cat": "Science", "base_prob": 0.45},
    {"q": "Will Apple release AR glasses in 2026?", "cat": "Science", "base_prob": 0.25},
    # Economics
    {"q": "Will the Fed cut rates to below 3.5% by June 2026?", "cat": "Economics", "base_prob": 0.40},
    {"q": "Will US GDP growth exceed 3% in Q1 2026?", "cat": "Economics", "base_prob": 0.32},
    {"q": "Will the US unemployment rate exceed 5% in 2026?", "cat": "Economics", "base_prob": 0.18},
    {"q": "Will the S&P 500 reach 7000 by June 2026?", "cat": "Economics", "base_prob": 0.38},
    {"q": "Will US CPI inflation fall below 2% in 2026?", "cat": "Economics", "base_prob": 0.25},
    # Pop Culture / Misc
    {"q": "Will Taylor Swift announce a new album in Q1 2026?", "cat": "Pop Culture", "base_prob": 0.35},
    {"q": "Will GTA VI release before September 2026?", "cat": "Pop Culture", "base_prob": 0.60},
    {"q": "Will a deepfake scandal impact a major election in 2026?", "cat": "Pop Culture", "base_prob": 0.40},
    {"q": "Will TikTok be banned in the US by July 2026?", "cat": "Pop Culture", "base_prob": 0.15},
    {"q": "Will the global population reach 8.2 billion in 2026?", "cat": "Science", "base_prob": 0.70},
]


def generate_snapshots(num_rounds: int = 10) -> list[dict]:
    """Generate multiple rounds of market snapshots simulating time progression.

    Each round represents a different point in time, with prices drifting
    realistically from their base probabilities.
    """
    snapshots = []
    base_time = datetime(2026, 1, 15, tzinfo=timezone.utc)

    # Assign persistent IDs to markets
    market_ids = {m["q"]: str(uuid.uuid4()) for m in MARKETS}
    token_ids = {m["q"]: [str(uuid.uuid4()), str(uuid.uuid4())] for m in MARKETS}

    for round_num in range(num_rounds):
        timestamp = base_time + timedelta(hours=round_num * 6)

        for market in MARKETS:
            # Drift price from base probability with random walk
            drift = random.gauss(0, 0.03) * (round_num + 1)
            yes_price = max(0.02, min(0.98, market["base_prob"] + drift))
            no_price = 1.0 - yes_price

            # Add small spread inefficiency (sometimes)
            spread_noise = random.uniform(-0.03, 0.03)
            no_price = max(0.02, min(0.98, no_price + spread_noise))

            # Volume and liquidity scale with popularity
            base_volume = random.uniform(20000, 500000)
            volume = base_volume * (1 + round_num * 0.1)
            liquidity = volume * random.uniform(0.05, 0.3)

            snapshot = {
                "market_id": market_ids[market["q"]],
                "condition_id": str(uuid.uuid4()),
                "question": market["q"],
                "slug": market["q"].lower().replace(" ", "-").replace("?", "")[:60],
                "yes_price": round(yes_price, 4),
                "no_price": round(no_price, 4),
                "spread": round(abs(yes_price + no_price - 1.0), 4),
                "volume": round(volume, 2),
                "liquidity": round(liquidity, 2),
                "active": True,
                "closed": False,
                "outcomes": ["Yes", "No"],
                "token_ids": token_ids[market["q"]],
                "end_date": (timestamp + timedelta(days=random.randint(30, 180))).isoformat(),
                "category": market["cat"],
                "timestamp": timestamp.isoformat(),
            }
            snapshots.append(snapshot)

    return snapshots


def main():
    random.seed(2026)
    snapshots = generate_snapshots(num_rounds=10)

    output_path = DATA_DIR / "snapshots.json"
    with open(output_path, "w") as f:
        json.dump(snapshots, f, indent=2)

    print(f"Generated {len(snapshots)} market snapshots across 10 time periods")
    print(f"Covering {len(MARKETS)} unique markets")
    print(f"Saved to {output_path}")

    # Show sample
    sample = snapshots[0]
    print(f"\nSample snapshot:")
    print(f"  Market: {sample['question']}")
    print(f"  YES: ${sample['yes_price']:.4f}  NO: ${sample['no_price']:.4f}")
    print(f"  Spread: {sample['spread']:.4f}")
    print(f"  Volume: ${sample['volume']:,.2f}  Liquidity: ${sample['liquidity']:,.2f}")


if __name__ == "__main__":
    main()
