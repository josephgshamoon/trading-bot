"""Trading bot CLI — entry point for all operations.

Usage:
    python -m src.main scan          # Scan markets for signals
    python -m src.main backtest      # Run backtest on historical data
    python -m src.main paper         # Start/resume paper trading
    python -m src.main collect       # Collect market snapshots for backtesting
    python -m src.main status        # Show current system status
"""

import argparse
import json
import logging
import sys

from .config import load_config
from .utils.logger import setup_logger
from .exchange.polymarket_client import PolymarketClient
from .data.feed import DataFeed
from .data.indicators import MarketIndicators
from .strategy import STRATEGIES
from .engine.backtest import BacktestEngine
from .engine.paper import PaperEngine
from .engine.live import LiveEngine
from .risk.manager import RiskManager


def cmd_scan(config: dict):
    """Scan markets for trade signals."""
    logger = logging.getLogger("trading_bot")
    verbose = config.get("_verbose", False)

    client = PolymarketClient(config)
    feed = DataFeed(client)
    strategy_name = config.get("strategy", {}).get("active", "value_betting")

    if strategy_name not in STRATEGIES:
        logger.error(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    strategy = STRATEGIES[strategy_name](config)
    snapshots = feed.get_all_snapshots(config)

    print(f"\nScanning {len(snapshots)} markets with {strategy_name} strategy...\n")

    signals = []
    for snap in snapshots:
        indicators = MarketIndicators.compute_all(snap)

        token_ids = snap.get("token_ids", [])
        if token_ids:
            try:
                hist = feed.get_price_history_df(token_ids[0])
                if not hist.empty:
                    indicators = MarketIndicators.compute_all(snap, hist)
            except Exception:
                pass

        if verbose:
            q = snap["question"][:60]
            est = MarketIndicators.edge_estimate(
                snap["yes_price"], snap["volume"], snap["liquidity"],
                indicators.get("momentum_6", 0.0),
            )
            yes_edge = est - snap["yes_price"]
            no_edge = (1.0 - est) - snap["no_price"]
            print(
                f"  {q}...\n"
                f"    YES={snap['yes_price']:.3f}  NO={snap['no_price']:.3f}  "
                f"Vol=${snap['volume']:,.0f}  Liq=${snap['liquidity']:,.0f}\n"
                f"    Est={est:.3f}  YES_edge={yes_edge:+.3f}  "
                f"NO_edge={no_edge:+.3f}  "
                f"momentum={indicators.get('momentum_6', 0):.4f}"
            )

        signal = strategy.evaluate(snap, indicators)
        if signal:
            signals.append(signal)
            if verbose:
                print(f"    >>> SIGNAL: {signal.signal.value} "
                      f"edge={signal.edge:.4f} conf={signal.confidence:.2f}")
        elif verbose:
            print(f"    --- no signal")

    if not signals:
        print("\nNo trade signals found.")
        if not verbose:
            print("Tip: run with -v to see why each market was skipped.")
        return

    # Sort by confidence descending
    signals.sort(key=lambda s: s.confidence, reverse=True)

    print(f"\nFound {len(signals)} signals:\n")
    for i, sig in enumerate(signals, 1):
        print(f"  {i}. {sig}")
    print()


def cmd_backtest(config: dict):
    """Run backtest on collected snapshot data."""
    logger = logging.getLogger("trading_bot")

    feed = DataFeed(PolymarketClient(config))
    strategy_name = config.get("strategy", {}).get("active", "value_betting")

    if strategy_name not in STRATEGIES:
        logger.error(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    strategy = STRATEGIES[strategy_name](config)
    engine = BacktestEngine(config)

    # Load historical snapshots
    snapshots = feed.load_snapshots()
    if not snapshots:
        print("No historical data found. Run 'collect' first to gather data.")
        print("Usage: python -m src.main collect")
        return

    print(f"\nRunning backtest: {strategy_name} on {len(snapshots)} snapshots...\n")

    result = engine.run(
        strategy=strategy,
        snapshots=snapshots,
        indicators_fn=lambda snap: MarketIndicators.compute_all(snap),
        seed=42,
    )

    print(result.summary())
    engine.save_results(result)
    print("Results saved to data/ directory.")


def cmd_paper(config: dict):
    """Start or resume paper trading."""
    logger = logging.getLogger("trading_bot")

    client = PolymarketClient(config)
    feed = DataFeed(client)
    risk = RiskManager(config)

    strategy_name = config.get("strategy", {}).get("active", "value_betting")
    if strategy_name not in STRATEGIES:
        logger.error(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    strategy = STRATEGIES[strategy_name](config)
    engine = PaperEngine(config, feed, risk)

    # Try to load existing session
    if engine.load_session():
        print(f"\nResumed paper session: {engine.session.session_id}")
    else:
        balance = config.get("backtest", {}).get("starting_balance_usdc", 1000.0)
        engine.start_session(strategy_name, balance)
        print(f"\nStarted new paper session with ${balance:.2f}")

    # Scan for signals
    print(f"\nScanning markets with {strategy_name}...\n")
    signals = engine.scan_markets(strategy)

    if not signals:
        print("No trade signals found.")
    else:
        signals.sort(key=lambda s: s.confidence, reverse=True)
        print(f"Found {len(signals)} signals:\n")
        for i, sig in enumerate(signals, 1):
            print(f"  {i}. {sig}")

        # Auto-execute top signals (up to max open positions)
        executed = 0
        max_new = config.get("trading", {}).get("max_open_positions", 5) - len(
            [p for p in (engine.session.positions if engine.session else [])
             if p.get("status") == "open"]
        )

        for sig in signals[:max_new]:
            pos = engine.execute_signal(sig)
            if pos:
                executed += 1

        print(f"\nExecuted {executed} paper trades.")

    # Check for resolved positions
    resolved = engine.check_and_resolve(client)
    if resolved:
        print(f"\n{len(resolved)} positions resolved:")
        for r in resolved:
            print(f"  {r['trade_id']}: {r['status']} (${r['pnl']:+.2f})")

    # Print summary
    summary = engine.get_summary()
    print(f"\n{'='*50}")
    print(f"  Session: {summary['session_id']}")
    print(f"  Balance: ${summary['current_balance']:.2f}")
    print(f"  PnL: ${summary['total_pnl']:+.2f}")
    print(f"  Trades: {summary['total_trades']} (W:{summary['wins']} L:{summary['losses']})")
    print(f"  Win Rate: {summary['win_rate']}")
    print(f"  Open: {summary['open_positions']}")
    print(f"{'='*50}\n")


def cmd_collect(config: dict):
    """Collect market snapshots for backtesting."""
    client = PolymarketClient(config)
    feed = DataFeed(client)

    print("\nCollecting market snapshots...\n")
    snapshots = feed.get_all_snapshots(config)

    if not snapshots:
        print("No markets found matching filters.")
        return

    feed.save_snapshots(snapshots)
    print(f"Collected {len(snapshots)} market snapshots.")
    print("Data saved to data/snapshots.json")
    print("\nRun 'collect' periodically to build up historical data for backtesting.")


def cmd_status(config: dict):
    """Show current system status."""
    client = PolymarketClient(config)
    risk = RiskManager(config)
    live = LiveEngine(config, risk)
    feed = DataFeed(client)

    # Try loading paper session
    engine = PaperEngine(config, feed, risk)
    has_session = engine.load_session()

    print(f"\n{'='*50}")
    print("  TRADING BOT STATUS")
    print(f"{'='*50}")
    print(f"  Mode:     {config.get('trading', {}).get('mode', 'paper')}")
    print(f"  Strategy: {config.get('strategy', {}).get('active', 'value_betting')}")
    print(f"  Live:     {'ENABLED' if live.is_enabled else 'disabled'}")

    if has_session:
        summary = engine.get_summary()
        print(f"\n  Paper Session: {summary['session_id']}")
        print(f"  Balance: ${summary['current_balance']:.2f}")
        print(f"  PnL: ${summary['total_pnl']:+.2f}")
        print(f"  Trades: {summary['total_trades']}")
    else:
        print("\n  No active paper session.")

    # Market connectivity check
    try:
        markets = client.get_markets(limit=5)
        print(f"\n  Polymarket API: Connected ({len(markets)} markets)")
    except Exception as e:
        print(f"\n  Polymarket API: ERROR — {e}")

    risk_status = risk.get_status()
    print(f"\n  Risk Status:")
    print(f"    Can Trade: {risk_status['can_trade']}")
    print(f"    Kill Switch: {'ACTIVE' if risk_status['kill_switch'] else 'off'}")
    print(f"{'='*50}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  scan      Scan markets for trade signals
  backtest  Run backtest on historical data
  paper     Start/resume paper trading
  collect   Collect market snapshots
  status    Show system status
        """,
    )

    parser.add_argument(
        "command",
        choices=["scan", "backtest", "paper", "collect", "status"],
        help="Command to run",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--strategy", "-s",
        default=None,
        help="Override active strategy",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Setup logging
    log_level = "DEBUG" if args.verbose else config.get("logging", {}).get("level", "INFO")
    log_file = config.get("logging", {}).get("file")
    setup_logger(level=log_level, log_file=log_file)

    # Override strategy if specified
    if args.strategy:
        config.setdefault("strategy", {})["active"] = args.strategy

    # Pass verbose flag into config for scan diagnostics
    config["_verbose"] = args.verbose

    # Route to command
    commands = {
        "scan": cmd_scan,
        "backtest": cmd_backtest,
        "paper": cmd_paper,
        "collect": cmd_collect,
        "status": cmd_status,
    }

    commands[args.command](config)


if __name__ == "__main__":
    main()
