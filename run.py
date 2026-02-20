#!/usr/bin/env python3
"""
Polymarket Trading Bot — CLI Entry Point

Usage:
    python3 run.py markets              # Browse top markets
    python3 run.py search "bitcoin"     # Search by keyword
    python3 run.py market <ID>          # View market details + token IDs
    python3 run.py paper <ID>           # Paper trade (simulated)
    python3 run.py trade <ID>           # Real trade (needs .env + py-clob-client)
    python3 run.py backtest             # Run strategy backtest
    python3 run.py collect              # Collect market data snapshot
"""

import argparse
import json
import os
import sys
from pathlib import Path


def parse_json_field(value, default=None):
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


def get_outcome_prices(market):
    """Extract outcome prices from a market dict, handling API format variations."""
    probs = market.get("outcomePrices") or market.get("outcome_prices")
    return parse_json_field(probs)


def load_env():
    """Load .env file into os.environ (no extra dependency)."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if value and key:
                os.environ.setdefault(key, value)


def notify(message: str):
    """Send a Telegram notification if configured, silently skip if not."""
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
        pass


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_markets(args):
    """Browse top active markets."""
    from src.polymarket_client import PolymarketClient
    client = PolymarketClient()
    markets = client.get_markets(limit=args.limit)

    if not markets:
        print("No markets found.")
        return

    print(f"\n{'='*70}")
    print(f" Top {len(markets)} Active Markets")
    print(f"{'='*70}\n")

    for i, m in enumerate(markets, 1):
        question = m.get("question", "Unknown")
        mid = m.get("id", "")
        volume = float(m.get("volume", 0))
        liquidity = float(m.get("liquidity", 0))
        probs = get_outcome_prices(m)
        yes_str = f"{float(probs[0])*100:.0f}%" if probs else "?"

        print(f"  {i:>3}. {question[:65]}")
        print(f"       ID: {mid}")
        print(f"       YES: {yes_str}  |  Vol: ${volume:,.0f}  |  Liq: ${liquidity:,.0f}")
        print()


def cmd_search(args):
    """Search markets by keyword."""
    from src.polymarket_client import PolymarketClient
    client = PolymarketClient()
    results = client.search_markets(args.query)

    if not results:
        print(f"No markets found for '{args.query}'.")
        return

    print(f"\n{'='*70}")
    print(f" Search results for '{args.query}' ({len(results)} found)")
    print(f"{'='*70}\n")

    for i, m in enumerate(results[:20], 1):
        question = m.get("question", "Unknown")
        mid = m.get("id", "")
        probs = get_outcome_prices(m)
        yes_str = f"{float(probs[0])*100:.0f}%" if probs else "?"

        print(f"  {i:>3}. {question[:65]}")
        print(f"       ID: {mid}  |  YES: {yes_str}")
        print()


def cmd_market(args):
    """View market details and token IDs."""
    from src.polymarket_client import PolymarketClient
    client = PolymarketClient()
    m = client.get_market(args.id)

    if not m:
        print(f"Market '{args.id}' not found.")
        return

    print(f"\n{'='*70}")
    print(f" Market Details")
    print(f"{'='*70}\n")

    print(f"  Question:    {m.get('question', 'Unknown')}")
    print(f"  ID:          {m.get('id', '')}")
    print(f"  Active:      {m.get('active', '?')}")
    print(f"  Volume:      ${float(m.get('volume', 0)):,.0f}")
    print(f"  Liquidity:   ${float(m.get('liquidity', 0)):,.0f}")

    probs = get_outcome_prices(m)
    if probs:
        print(f"  YES price:   {float(probs[0])*100:.1f}%")
        if len(probs) > 1:
            print(f"  NO price:    {float(probs[1])*100:.1f}%")

    # Show token IDs (needed for trading)
    tokens = parse_json_field(m.get("tokens"))
    clob_tokens = parse_json_field(m.get("clobTokenIds"))
    outcomes = parse_json_field(m.get("outcomes"), ["YES", "NO"])

    if tokens and isinstance(tokens[0], dict):
        print(f"\n  Tokens:")
        for t in tokens:
            outcome = t.get("outcome", "?")
            tid = t.get("token_id", "")
            print(f"    {outcome}: {tid}")
    elif clob_tokens:
        print(f"\n  CLOB Token IDs:")
        for i, tid in enumerate(clob_tokens):
            label = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
            print(f"    {label}: {tid}")

    print(f"\n  URL: https://polymarket.com/event/{m.get('id', '')}")
    print()


def cmd_paper(args):
    """Run a paper trade on a specific market."""
    from src.polymarket_client import PolymarketClient
    client = PolymarketClient()
    m = client.get_market(args.id)

    if not m:
        print(f"Market '{args.id}' not found.")
        return

    question = m.get("question", "Unknown")
    probs = get_outcome_prices(m)
    yes_price = float(probs[0]) if probs else 0.5

    print(f"\n  Market: {question}")
    print(f"  YES: {yes_price*100:.1f}%  |  NO: {(1-yes_price)*100:.1f}%\n")

    # Get side
    side = input("  Side (yes/no): ").strip().upper()
    if side not in ("YES", "NO"):
        print("  Invalid side. Use 'yes' or 'no'.")
        return

    # Get amount
    try:
        amount = float(input("  Amount in USDC [1.0]: ").strip() or "1.0")
    except ValueError:
        print("  Invalid amount.")
        return

    entry_price = yes_price if side == "YES" else (1 - yes_price)

    print(f"\n  Paper trade: {side} ${amount:.2f} @ {entry_price*100:.1f}%")
    print(f"  Potential payout: ${amount / entry_price:.2f}" if entry_price > 0 else "")
    print(f"  Status: SIMULATED (no real money)")

    notify(f"Paper trade: {side} ${amount:.2f} on {question[:50]}")
    print("\n  Done.\n")


def cmd_trade(args):
    """Place a real trade on Polymarket."""
    # Check for private key before importing heavy deps
    load_env()
    if not os.environ.get("POLYMARKET_PRIVATE_KEY"):
        print("\n  Error: POLYMARKET_PRIVATE_KEY not set.")
        print("  Copy .env.example to .env and add your wallet private key.")
        print("  This is only needed for real trading.\n")
        return

    from src.polymarket_client import PolymarketClient
    client = PolymarketClient()
    m = client.get_market(args.id)

    if not m:
        print(f"Market '{args.id}' not found.")
        return

    question = m.get("question", "Unknown")
    probs = get_outcome_prices(m)
    yes_price = float(probs[0]) if probs else 0.5

    print(f"\n  Market: {question}")
    print(f"  YES: {yes_price*100:.1f}%  |  NO: {(1-yes_price)*100:.1f}%\n")

    # Get token IDs
    tokens = parse_json_field(m.get("tokens"))
    clob_tokens = parse_json_field(m.get("clobTokenIds"))
    outcomes = parse_json_field(m.get("outcomes"), ["Yes", "No"])

    token_map = {}
    if tokens and isinstance(tokens[0], dict):
        for t in tokens:
            token_map[t.get("outcome", "").upper()] = t.get("token_id", "")
    elif clob_tokens:
        for i, tid in enumerate(clob_tokens):
            label = outcomes[i].upper() if i < len(outcomes) else f"OUTCOME_{i}"
            token_map[label] = tid

    if not token_map:
        print("  Error: Could not find token IDs for this market.")
        return

    # Get side
    side = input("  Side (yes/no): ").strip().upper()
    if side not in ("YES", "NO"):
        print("  Invalid side. Use 'yes' or 'no'.")
        return

    token_id = token_map.get(side)
    if not token_id:
        print(f"  Error: No token ID found for {side}.")
        return

    # Get amount
    try:
        amount = float(input("  Amount in USDC [1.0]: ").strip() or "1.0")
    except ValueError:
        print("  Invalid amount.")
        return

    entry_price = yes_price if side == "YES" else (1 - yes_price)

    print(f"\n  REAL TRADE: BUY {side} ${amount:.2f} @ {entry_price*100:.1f}%")
    print(f"  Token: {token_id[:20]}...")
    print(f"  This will use real USDC from your wallet.\n")

    confirm = input("  Type 'confirm' to execute: ").strip().lower()
    if confirm != "confirm":
        print("  Trade cancelled.")
        return

    try:
        from src.trader import RealTrader
        trader = RealTrader()
        result = trader.place_market_order(
            token_id=token_id,
            side="BUY",
            size=amount,
        )
        print(f"\n  Order submitted: {result}")
        notify(f"REAL TRADE: BUY {side} ${amount:.2f} on {question[:50]}")
    except ImportError as e:
        print(f"\n  Error: {e}")
    except Exception as e:
        print(f"\n  Trade failed: {e}")


def cmd_backtest(args):
    """Run strategy backtest."""
    from src.backtest import run_quick_backtest
    run_quick_backtest()


def cmd_collect(args):
    """Collect a market data snapshot."""
    from src.data_collector import run_collection
    count = run_collection()
    print(f"\nCollected {count} market snapshots.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    load_env()

    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 run.py markets              Browse top markets\n"
            "  python3 run.py search \"bitcoin\"      Search by keyword\n"
            "  python3 run.py market <ID>           View market details\n"
            "  python3 run.py paper <ID>            Paper trade (simulated)\n"
            "  python3 run.py trade <ID>            Real trade (needs .env)\n"
            "  python3 run.py backtest              Run strategy backtest\n"
            "  python3 run.py collect               Collect data snapshot\n"
        ),
    )

    sub = parser.add_subparsers(dest="command")

    # markets
    p_markets = sub.add_parser("markets", help="Browse top active markets")
    p_markets.add_argument("--limit", type=int, default=20, help="Number of markets (default: 20)")

    # search
    p_search = sub.add_parser("search", help="Search markets by keyword")
    p_search.add_argument("query", help="Search keyword")

    # market
    p_market = sub.add_parser("market", help="View market details and token IDs")
    p_market.add_argument("id", help="Market ID")

    # paper
    p_paper = sub.add_parser("paper", help="Paper trade a market (simulated)")
    p_paper.add_argument("id", help="Market ID")

    # trade
    p_trade = sub.add_parser("trade", help="Real trade (requires .env + py-clob-client)")
    p_trade.add_argument("id", help="Market ID")

    # backtest
    sub.add_parser("backtest", help="Run strategy backtest")

    # collect
    sub.add_parser("collect", help="Collect market data snapshot")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "markets": cmd_markets,
        "search": cmd_search,
        "market": cmd_market,
        "paper": cmd_paper,
        "trade": cmd_trade,
        "backtest": cmd_backtest,
        "collect": cmd_collect,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
