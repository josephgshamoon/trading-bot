"""Trading bot CLI — entry point for all operations.

Usage:
    python -m src.main scan          # Scan markets for trade signals
    python -m src.main backtest      # Run backtest on historical data
    python -m src.main paper         # Start/resume paper trading
    python -m src.main live          # Start/resume live trading (real USDC)
    python -m src.main fast          # Fast-cycle short-term trading (15-min crypto, tweets)
    python -m src.main collect       # Collect market snapshots for backtesting
    python -m src.main resolve       # Fetch resolved markets for edge analysis
    python -m src.main calibrate     # Calibration analysis on resolved data
    python -m src.main edge          # Monte Carlo edge analysis across strategies
    python -m src.main status        # Show current system status
    python -m src.main report        # Performance report with news impact analysis
    python -m src.main stats         # Trade journal accuracy & calibration report
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (two levels up from this file)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from .config import load_config
from .utils.logger import setup_logger
from .exchange.polymarket_client import PolymarketClient
from .data.feed import DataFeed
from .data.indicators import MarketIndicators
from .data.news_feed import NewsFeed, RSS_FEEDS
from .data.categorizer import MarketCategorizer
from .intelligence.analyzer import MarketAnalyzer
from .strategy import STRATEGIES
from .strategy.news_enhanced import NewsEnhancedStrategy
from .engine.backtest import BacktestEngine
from .engine.paper import PaperEngine
from .engine.live import LiveEngine
from .engine.report import generate_report
from .engine.collector import (
    collect_resolved_markets,
    save_resolved_markets,
    load_resolved_markets,
    calibration_analysis,
    format_calibration_report,
)
from .engine.edge_analyzer import (
    compare_all_strategies,
    analyze_trade_characteristics,
    format_edge_report,
)
from .risk.manager import RiskManager
from .data.journal import TradeJournal

DATA_DIR = Path(__file__).parent.parent / "data"


def _get_news_context(config: dict, snapshots: list[dict]) -> dict[str, dict]:
    """Fetch news and run LLM analysis for all markets.

    Returns a dict mapping market_id -> analysis result.
    Used by both scan and paper commands when news_enhanced is active.
    """
    logger = logging.getLogger("trading_bot")

    news_feed = NewsFeed(config)
    analyzer = MarketAnalyzer(config)

    logger.info(f"Fetching news (backend: {analyzer.backend})...")
    print(f"  News intelligence: backend={analyzer.backend}")

    # Bulk fetch news once (efficient)
    all_news = news_feed.get_bulk_news()
    print(f"  Fetched {len(all_news)} news articles")

    if not all_news:
        logger.warning("No news articles fetched — falling back to pure statistical")
        return {}

    # Analyze each market against the news
    results = analyzer.analyze_markets_batch(snapshots, all_news)

    news_hits = sum(
        1 for r in results.values()
        if r.get("news_signal_strength", 0) > 0.1
    )
    print(f"  News signals found for {news_hits}/{len(snapshots)} markets")

    return results


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

    # Get news context if using news_enhanced strategy
    news_context = {}
    is_news_strategy = isinstance(strategy, NewsEnhancedStrategy)
    if is_news_strategy:
        news_context = _get_news_context(config, snapshots)

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

            # Show news info if available
            news = news_context.get(snap["market_id"])
            if news and news.get("news_signal_strength", 0) > 0.1:
                print(
                    f"    NEWS: {news['direction']} "
                    f"shift={news['probability_shift']:+.3f} "
                    f"str={news['news_signal_strength']:.2f} "
                    f"| {news.get('reasoning', '')[:60]}"
                )

        # Evaluate with or without news
        if is_news_strategy:
            news_analysis = news_context.get(snap["market_id"])
            signal = strategy.evaluate(snap, indicators, news_analysis)
        else:
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
        balance = config.get("backtest", {}).get("starting_balance_usdc", 200.0)
        engine.start_session(strategy_name, balance)
        print(f"\nStarted new paper session with ${balance:.2f}")

    # Get news context if using news_enhanced strategy
    is_news_strategy = isinstance(strategy, NewsEnhancedStrategy)
    news_context = {}

    if is_news_strategy:
        print(f"\nFetching news intelligence...\n")
        snapshots = feed.get_all_snapshots(config)
        news_context = _get_news_context(config, snapshots)

    # Scan for signals (with news if available)
    print(f"\nScanning markets with {strategy_name}...\n")
    signals = engine.scan_markets(strategy, news_context=news_context)

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
    print(f"  Strategy: {summary['strategy']}")
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

    # Check news/intelligence status
    analyzer = MarketAnalyzer(config)
    news_feed = NewsFeed(config)

    print(f"\n{'='*50}")
    print("  TRADING BOT STATUS")
    print(f"{'='*50}")
    print(f"  Mode:     {config.get('trading', {}).get('mode', 'paper')}")
    print(f"  Strategy: {config.get('strategy', {}).get('active', 'value_betting')}")
    print(f"  Live:     {'ENABLED' if live.is_enabled else 'disabled'}")

    # News intelligence status
    print(f"\n  News Intelligence:")
    print(f"    LLM Backend: {analyzer.backend}")
    print(f"    NewsAPI:     {'configured' if news_feed.newsapi_key else 'not set'}")
    print(f"    GNews:       {'configured' if news_feed.gnews_key else 'not set'}")
    print(f"    RSS Feeds:   {sum(len(v) for v in RSS_FEEDS.values())} configured")

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



def cmd_resolve(config: dict):
    """Fetch resolved markets from Polymarket for edge analysis."""
    client = PolymarketClient(config)

    print("\nFetching resolved markets from Polymarket...\n")
    resolved = collect_resolved_markets(client, max_pages=10)

    if not resolved:
        print("No resolved markets found. Check API connectivity.")
        return

    new_count, total = save_resolved_markets(resolved)
    print(f"\nCollected {len(resolved)} resolved markets from API.")
    print(f"  New: {new_count}  |  Total on disk: {total}")
    print("Data saved to data/resolved_markets.json")

    # Quick summary
    yes_count = sum(1 for m in resolved if m.get("resolved_yes"))
    no_count = len(resolved) - yes_count
    print(f"\n  YES wins: {yes_count}  |  NO wins: {no_count}")

    print("\nRun 'calibrate' to analyze this data for edge opportunities.")


def cmd_calibrate(config: dict):
    """Run calibration analysis on resolved market data."""
    resolved = load_resolved_markets()

    if not resolved:
        print("No resolved market data found.")
        print("Run 'resolve' first to collect data: python -m src.main resolve")
        return

    print(f"\nAnalyzing {len(resolved)} resolved markets...\n")

    analysis = calibration_analysis(resolved)
    print(format_calibration_report(analysis))

    # Also run backtest against resolved data to test strategy accuracy
    strategy_name = config.get("strategy", {}).get("active", "value_betting")
    if strategy_name in STRATEGIES:
        strategy = STRATEGIES[strategy_name](config)
        engine = BacktestEngine(config)

        print(f"Running {strategy_name} against resolved markets (ground truth)...\n")
        result = engine.run(
            strategy=strategy,
            snapshots=resolved,
            indicators_fn=lambda snap: MarketIndicators.compute_all(snap),
            seed=42,
        )
        print(result.summary())
        engine.save_results(result, f"calibration_{strategy_name}.json")


def cmd_edge(config: dict):
    """Run Monte Carlo edge analysis across all strategies."""
    feed = DataFeed(PolymarketClient(config))

    # Load snapshot data
    snapshots = feed.load_snapshots()
    if not snapshots:
        print("No snapshot data found. Run 'collect' first.")
        return

    n_sims = 100
    print(f"\nRunning Monte Carlo edge analysis ({n_sims} simulations per strategy)...")
    print(f"Using {len(snapshots)} market snapshots\n")

    # Compare all strategies
    mc_results = compare_all_strategies(config, snapshots, n_sims)

    # Get detailed characteristics for the best strategy
    characteristics = None
    if mc_results:
        best_name = mc_results[0]["strategy"]
        print(f"Analyzing trade characteristics for {best_name}...\n")
        characteristics = analyze_trade_characteristics(config, snapshots, best_name)

    # Format and display report
    report = format_edge_report(mc_results, characteristics)
    print(report)

    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "snapshots_used": len(snapshots),
        "simulations_per_strategy": n_sims,
        "monte_carlo_results": mc_results,
        "characteristics": characteristics,
    }
    output_path = DATA_DIR / "edge_analysis.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"Full results saved to {output_path}")


def cmd_live(config: dict):
    """Run live trading — real CLOB orders with real USDC."""
    logger = logging.getLogger("trading_bot")

    # Force live mode in config
    config.setdefault("trading", {})["mode"] = "live"

    client = PolymarketClient(config)
    feed = DataFeed(client)
    risk = RiskManager(config)

    strategy_name = config.get("strategy", {}).get("active", "value_betting")
    if strategy_name not in STRATEGIES:
        logger.error(f"Unknown strategy: {strategy_name}")
        sys.exit(1)

    strategy = STRATEGIES[strategy_name](config)
    engine = LiveEngine(config, risk)

    if not engine.is_enabled:
        print("\nLive trading is NOT enabled. Requirements:")
        print("  1. Set POLYMARKET_LIVE_ENABLED=true in .env")
        print("  2. Have valid API credentials (run scripts/setup_api_creds.py)")
        print("  3. If geo-blocked, set POLYMARKET_PROXY_URL in .env")
        print(f"\n  Status: {engine.get_status()}")
        return

    # Show proxy status for live trading
    from .exchange.proxy import get_proxy_status
    proxy_info = get_proxy_status()
    if proxy_info["configured"]:
        print(f"\n  Proxy: {proxy_info['proxy_url']} ({proxy_info['scheme']})")
    else:
        print(f"\n  Proxy: none (set POLYMARKET_PROXY_URL if getting 403 geo-blocks)")

    # Fetch live balance
    live_balance = engine.get_balance()
    print(f"  CLOB USDC Balance: ${live_balance:.2f}")

    if live_balance < 1.0:
        print("  Insufficient balance for live trading.")
        return

    # Load or start session
    if engine.load_session():
        print(f"\n  Resumed live session: {engine.session.session_id}")
    else:
        engine.start_session(strategy_name, live_balance)
        print(f"\n  Started new live session with ${live_balance:.2f}")

    # Get news context if using news_enhanced strategy
    is_news_strategy = isinstance(strategy, NewsEnhancedStrategy)
    news_context = {}

    if is_news_strategy:
        print(f"\nFetching news intelligence...\n")
        snapshots = feed.get_all_snapshots(config)
        news_context = _get_news_context(config, snapshots)

    # Scan for signals
    print(f"\nScanning markets with {strategy_name}...\n")
    signals = engine.scan_markets(strategy, feed, news_context=news_context)

    if not signals:
        print("No trade signals found.")
    else:
        signals.sort(key=lambda s: s.confidence, reverse=True)
        print(f"Found {len(signals)} signals:\n")
        for i, sig in enumerate(signals, 1):
            print(f"  {i}. {sig}")

        # Execute top signals
        max_new = config.get("trading", {}).get("max_open_positions", 5) - len(
            [p for p in (engine.session.positions if engine.session else [])
             if p.get("status") == "open"]
        )

        executed = 0
        for sig in signals[:max_new]:
            result = engine.execute_trade(sig)
            if result.get("status") == "executed":
                executed += 1
                print(f"  >> EXECUTED: {result['trade_id']} | "
                      f"{result['signal']} {result['shares']:.1f} shares "
                      f"@ ${result['price']:.4f} | order={result['order_id']}")
            elif result.get("error"):
                print(f"  >> FAILED: {result['error']}")

        print(f"\nExecuted {executed} live trades.")

    # Check for resolved positions (with journal logging)
    journal = TradeJournal()
    resolved = engine.check_and_resolve(client, journal=journal)
    if resolved:
        print(f"\n{len(resolved)} positions resolved:")
        for r in resolved:
            print(f"  {r['trade_id']}: {r['status']} (${r['pnl']:+.2f})")

    # Print summary
    summary = engine.get_summary()
    balance_now = engine.get_balance()
    print(f"\n{'='*50}")
    print(f"  LIVE SESSION: {summary['session_id']}")
    print(f"  Strategy: {summary['strategy']}")
    print(f"  CLOB Balance: ${balance_now:.2f}")
    print(f"  Session PnL: ${summary['total_pnl']:+.2f}")
    print(f"  Trades: {summary['total_trades']} (W:{summary['wins']} L:{summary['losses']})")
    print(f"  Win Rate: {summary['win_rate']}")
    print(f"  Open: {summary['open_positions']}")
    print(f"{'='*50}\n")


def cmd_fast(config: dict):
    """Run fast-cycle short-term trading (15-min crypto, tweet brackets).

    Designed to be called every minute by cron. Lightweight — no
    news fetch, no LLM calls, just event market discovery + momentum.

    Includes:
    - Cycle logging (all signals — executed and skipped)
    - 15-min crypto resolution checking (time-based)
    - Tweet bracket resolution checking (Gamma API closed flag)
    - Journal logging for every resolution
    """
    import time
    logger = logging.getLogger("trading_bot")

    config.setdefault("trading", {})["mode"] = "live"

    risk = RiskManager(config)
    engine = LiveEngine(config, risk)
    journal = TradeJournal()

    if not engine.is_enabled:
        print("Live trading not enabled. Set POLYMARKET_LIVE_ENABLED=true")
        return

    from .exchange.proxy import get_proxy_status
    proxy_info = get_proxy_status()
    if proxy_info["configured"]:
        print(f"  Proxy: {proxy_info['proxy_url']}")

    balance = engine.get_balance()
    print(f"  Balance: ${balance:.2f}")

    if balance < 1.0:
        print("  Insufficient balance.")
        return

    if engine.load_session():
        print(f"  Session: {engine.session.session_id}")
    else:
        engine.start_session("short_term", balance)
        print(f"  New session: ${balance:.2f}")

    # ── Position cleanup — remove ghost positions ──────────────
    if engine.session:
        ghosts = [
            p for p in engine.session.positions
            if p.get("status") != "open"
        ]
        if ghosts:
            for g in ghosts:
                logger.warning(
                    f"Cleaning ghost position: {g.get('trade_id')} "
                    f"status={g.get('status')}"
                )
                engine.session.closed_trades.append(g)
            engine.session.positions = [
                p for p in engine.session.positions
                if p.get("status") == "open"
            ]
            engine._save_session()

    # ── Resolution checking ──────────────────────────────────────
    resolved_count = 0

    if engine.session and engine.session.positions:
        now_ts = time.time()

        # Pass 1: Resolve crypto positions by time (15-min and 1-hour)
        for pos in list(engine.session.positions):
            if pos.get("status") != "open":
                continue
            meta = pos.get("metadata", {})
            if meta.get("strategy") in ("crypto_momentum_15m", "crypto_momentum_1h"):
                if _resolve_crypto_position(pos, engine, journal):
                    resolved_count += 1

        # Pass 2: Resolve tweet brackets via CLOB orderbook price
        for pos in list(engine.session.positions):
            if pos.get("status") != "open":
                continue
            meta = pos.get("metadata", {})
            if meta.get("strategy") == "tweet_brackets":
                if _resolve_tweet_position(pos, engine, journal, config):
                    resolved_count += 1

        # Pass 3: Resolve news_enhanced positions via numeric Gamma ID
        has_gamma_positions = any(
            p.get("status") == "open"
            and p.get("metadata", {}).get("strategy") == "news_enhanced"
            and not p.get("market_id", "").startswith("0x")
            for p in engine.session.positions
        )
        if has_gamma_positions:
            try:
                client = PolymarketClient(config)
                resolved_list = engine.check_and_resolve(client, journal=journal)
                resolved_count += len(resolved_list)
            except Exception as e:
                logger.error(f"Gamma resolution error: {e}")

        # Pass 4: Profit-taking — sell positions that have moved in our favor
        _check_profit_taking(engine, config, journal)

    if resolved_count:
        print(f"\n  {resolved_count} position(s) resolved.")

    # ── Signal scanning ──────────────────────────────────────────
    from .strategy.short_term import ShortTermStrategy
    strategy = ShortTermStrategy(config)

    print(f"\nScanning short-term markets...")
    signals = strategy.evaluate_all(balance)

    # Print Binance technical analysis if available
    ta = getattr(strategy.crypto, '_last_ta', None)
    if ta:
        print(
            f"\n  SOL ${ta['price']:.2f} | "
            f"RSI={ta['rsi_14']:.0f}({ta['rsi_signal']}) | "
            f"VWAP=${ta['vwap']:.2f}({ta['price_vs_vwap']}) | "
            f"Vol={ta['volume_ratio']}x | "
            f"{ta['signal_strength'].upper()}"
        )
        print(
            f"  S=${ta['support']:.2f} R=${ta['resistance']:.2f} | "
            f"5m={ta['trend_5m']:+.3f}% 15m={ta['trend_15m']:+.3f}% "
            f"1h={ta['trend_1h']:+.3f}% 4h={ta['trend_4h']:+.3f}%"
        )
    else:
        # Fallback to CoinGecko display
        mtf = getattr(strategy.crypto, '_last_mtf', None)
        if mtf:
            print(
                f"\n  SOL ${mtf['current_price']:.2f} | "
                f"30m: {mtf['trend_30m']['change_pct']:+.2f}% | "
                f"4h: {mtf['trend_4h']['change_pct']:+.2f}% | "
                f"Bias: {mtf['bias'].upper()}"
            )

    # Track all signals for journal (executed + skipped)
    cycle_signals: list[dict] = []

    if not signals:
        print("No short-term signals.")
    else:
        signals.sort(key=lambda s: s.edge, reverse=True)
        print(f"\n{len(signals)} signals found:\n")
        for i, sig in enumerate(signals, 1):
            strat = sig.metadata.get("strategy", "unknown")
            print(f"  {i}. [{strat}] {sig}")

        # Execute — count only crypto momentum positions against the limit
        # (existing tweet bracket positions shouldn't block new SOL trades)
        max_crypto = config.get("short_term", {}).get("max_crypto_positions", 2)
        open_crypto = len([
            p for p in (engine.session.positions if engine.session else [])
            if p.get("status") == "open"
            and p.get("metadata", {}).get("strategy") in ("crypto_momentum_15m", "crypto_momentum_1h")
        ])
        max_new = max(0, max_crypto - open_crypto)

        executed = 0
        for idx, sig in enumerate(signals):
            sig_record = {
                "market_id": sig.market_id,
                "question": sig.question[:80],
                "signal": sig.signal.value,
                "edge": round(sig.edge, 4),
                "confidence": round(sig.confidence, 4),
                "strategy": sig.metadata.get("strategy", "unknown"),
            }

            # Check if we should execute
            target_token = sig.metadata.get("target_token_id", "")
            if not target_token:
                sig_record["action"] = "skipped"
                sig_record["skip_reason"] = "no target token"
                cycle_signals.append(sig_record)
                continue

            # Skip if we already have a position in this market/slot
            is_crypto = sig.metadata.get("strategy") in ("crypto_momentum_15m", "crypto_momentum_1h")
            existing_markets = set()
            existing_slots = set()  # track slot_start timestamps to prevent duplicates
            if engine.session:
                for p in engine.session.positions:
                    if p.get("status") == "open":
                        existing_markets.add(p.get("market_id"))
                        # Track slot timestamps for crypto positions
                        p_meta = p.get("metadata", {})
                        if p_meta.get("slot_start"):
                            existing_slots.add(p_meta["slot_start"])
                if not is_crypto:
                    for p in engine.session.closed_trades:
                        existing_markets.add(p.get("market_id"))

            # Block duplicate crypto trades on the same slot
            if is_crypto and sig.metadata.get("slot_start") in existing_slots:
                sig_record["action"] = "skipped"
                sig_record["skip_reason"] = "already have position on this slot"
                cycle_signals.append(sig_record)
                continue

            if sig.market_id in existing_markets and not is_crypto:
                sig_record["action"] = "skipped"
                sig_record["skip_reason"] = "already have position"
                cycle_signals.append(sig_record)
                continue

            if executed >= max_new:
                sig_record["action"] = "skipped"
                sig_record["skip_reason"] = "max trades per cycle"
                cycle_signals.append(sig_record)
                continue

            result = engine.execute_trade(sig)
            if result.get("status") == "executed":
                executed += 1
                sig_record["action"] = "executed"
                sig_record["trade_id"] = result.get("trade_id", "")
                print(
                    f"  >> EXECUTED: {result['trade_id']} | "
                    f"{result['signal']} @ ${result['price']:.4f} | "
                    f"order={result.get('order_id', 'N/A')[:16]}..."
                )
            elif result.get("error"):
                sig_record["action"] = "skipped"
                sig_record["skip_reason"] = result["error"]
                print(f"  >> FAILED: {result['error']}")

            cycle_signals.append(sig_record)

        print(f"\nExecuted {executed} short-term trades.")

    # ── Balance reconciliation ──────────────────────────────────
    # Sync session balance with on-chain to prevent drift
    onchain_balance = engine.get_balance()
    if engine.session and abs(engine.session.current_balance - onchain_balance) > 1.0:
        logger.info(
            f"Balance reconciliation: session=${engine.session.current_balance:.2f} "
            f"vs on-chain=${onchain_balance:.2f}"
        )
        engine.session.current_balance = onchain_balance
        engine._save_session()

    # ── Log cycle to journal ─────────────────────────────────────
    open_count = len([
        p for p in (engine.session.positions if engine.session else [])
        if p.get("status") == "open"
    ])
    journal.log_cycle(
        balance=onchain_balance,
        signals=cycle_signals,
        open_positions=open_count,
        strategy="short_term",
    )

    # ── Hourly Telegram summary (top of each hour) ────────────
    now = datetime.now(timezone.utc)
    if now.minute <= 1:
        flag_file = DATA_DIR / ".last_hourly_summary"
        current_hour = now.strftime("%Y-%m-%d-%H")
        last_sent = ""
        if flag_file.exists():
            last_sent = flag_file.read_text().strip()
        if last_sent != current_hour:
            from .notifications.telegram import TelegramNotifier
            notifier = TelegramNotifier()
            if notifier.is_configured():
                # Last hour trades from journal
                recent = journal.get_recent_trades(n=50)
                one_hour_ago = (now - timedelta(hours=1)).isoformat()
                hour_trades = [t for t in recent if t.get("ts", "") >= one_hour_ago]
                h_wins = sum(1 for t in hour_trades if t.get("outcome") in ("won", "sold_profit"))
                h_losses = sum(1 for t in hour_trades if t.get("outcome") in ("lost", "sold_loss"))
                h_pnl = sum(t.get("pnl", 0) for t in hour_trades)

                # Session PnL
                session_pnl = engine.session.total_pnl if engine.session else 0.0

                # Next slot info
                next_info = "—"
                try:
                    from .data.event_markets import discover_hourly_slots
                    upcoming = discover_hourly_slots(coins=["sol"], look_ahead_hours=2)
                    future = [s for s in upcoming if s.start_ts > now.timestamp()]
                    if future:
                        ns = future[0]
                        slot_time = datetime.fromtimestamp(ns.start_ts, tz=timezone.utc).strftime("%H:%M")
                        next_info = f"SOL [{slot_time} UTC]"
                except Exception:
                    pass

                msg = (
                    "\U0001f4ca <b>Hourly Summary</b>\n"
                    f"Balance: ${onchain_balance:.2f}\n"
                    f"Last hour: {h_wins}W/{h_losses}L | ${h_pnl:+.2f}\n"
                    f"Session PnL: ${session_pnl:+.2f}\n"
                    f"Next: {next_info}"
                )
                notifier.send_message(msg)

            flag_file.parent.mkdir(parents=True, exist_ok=True)
            flag_file.write_text(current_hour)

    print(f"\n  Balance: ${onchain_balance:.2f}")


def _resolve_crypto_position(pos: dict, engine, journal) -> bool:
    """Resolve a 15-min crypto up/down position if its slot has ended.

    Re-fetches the slot via ``discover_updown_slots()`` to check the
    final price.  Returns True if the position was resolved.
    """
    import time
    logger = logging.getLogger("trading_bot")

    if not engine.session:
        logger.warning("Cannot resolve crypto position — no session loaded")
        return False

    meta = pos.get("metadata", {})
    slot_start = meta.get("slot_start", 0)
    if not slot_start:
        return False

    now_ts = time.time()
    # Determine slot duration: 1-hour (3600s) or 15-min (900s)
    slot_duration = meta.get("slot_duration", 900)
    strategy_name = meta.get("strategy", "crypto_momentum_15m")

    # Slot hasn't ended yet
    if slot_start + slot_duration > now_ts:
        return False

    coin = meta.get("coin", "btc")

    # Re-fetch the slot to check resolution
    if strategy_name == "crypto_momentum_1h":
        from .data.event_markets import discover_hourly_slots
        try:
            slots = discover_hourly_slots(
                coins=[coin], look_ahead_hours=0, look_back_hours=4,
            )
        except Exception as e:
            logger.error(f"Failed to re-fetch 1h slots for {coin}: {e}")
            return False
    else:
        from .data.event_markets import discover_updown_slots
        try:
            slots = discover_updown_slots(
                coins=[coin], look_ahead_slots=0, look_back_slots=4,
            )
        except Exception as e:
            logger.error(f"Failed to re-fetch slots for {coin}: {e}")
            return False

    # Find the matching slot by start timestamp
    target_slot = None
    for s in slots:
        if s.start_ts == slot_start:
            target_slot = s
            break

    if target_slot is None:
        logger.warning(f"Slot {coin} {slot_start} not found in recent slots")
        return False

    # For 1-hour slots: check if the event is marked as closed
    # (Polymarket auto-resolves 1h markets via Binance candle)
    if strategy_name == "crypto_momentum_1h" and hasattr(target_slot, 'closed'):
        if not target_slot.closed:
            # Not yet resolved by Polymarket — check price thresholds as fallback
            pass

    # Determine outcome from final price
    # Tighter thresholds after elapsed time past slot end
    up_price = target_slot.up_price
    elapsed_since_end = now_ts - (slot_start + slot_duration)
    if elapsed_since_end > 300:
        # 5+ min past end: loosen to 0.60/0.40 — outcome is clear by now
        threshold_hi, threshold_lo = 0.60, 0.40
    else:
        threshold_hi, threshold_lo = 0.70, 0.30

    if threshold_lo <= up_price <= threshold_hi:
        # Not yet clearly resolved — skip
        return False

    direction = meta.get("direction", "up")
    actual_direction = "up" if up_price > (threshold_hi - 0.01) else "down"
    if direction == "up":
        won = up_price > (threshold_hi - 0.01)
    else:
        won = up_price < (threshold_lo + 0.01)

    if won:
        pnl = pos.get("shares", 0) * 1.0 - pos.get("size_usdc", 0)
        pos["status"] = "won"
        if engine.session:
            engine.session.wins += 1
    else:
        pnl = -pos.get("size_usdc", 0)
        pos["status"] = "lost"
        if engine.session:
            engine.session.losses += 1

    pos["pnl"] = round(pnl, 4)
    pos["exit_time"] = datetime.now(timezone.utc).isoformat()

    # ── Analysis ───────────────────────────────────────────────
    momentum = meta.get("momentum_score", 0)
    entry_price = pos.get("entry_price", 0)
    market_price = meta.get("market_price", 0.5)
    edge = pos.get("edge", 0)
    roi = (pnl / pos.get("size_usdc", 1)) * 100

    analysis = (
        f"Bet {coin.upper()} {direction.upper()} | Actual: {actual_direction.upper()}\n"
        f"Momentum: {momentum:+.2f} | Edge: {edge:.3f} | Entry: ${entry_price:.3f}\n"
    )
    if won:
        analysis += f"Signal was correct. ROI: {roi:+.0f}%"
    else:
        if direction != actual_direction:
            analysis += f"Wrong direction — momentum said {direction.upper()} but {coin.upper()} went {actual_direction.upper()}"
        else:
            analysis += f"Direction correct but didn't resolve cleanly"

    pos["analysis"] = analysis

    engine.risk.record_trade_exit(pos.get("trade_id", pos.get("market_id", "")), pnl)
    if engine.session:
        engine.session.total_pnl += pnl
        engine.session.current_balance = engine.risk.portfolio.balance
        engine.session.closed_trades.append(pos)
        engine.session.positions = [
            p for p in engine.session.positions
            if p.get("trade_id") != pos.get("trade_id")
        ]
        engine._save_session()

    # Log resolution to journal with analysis
    predicted_prob = meta.get("market_price", entry_price)
    journal.log_resolution(
        trade_id=pos.get("trade_id", ""),
        market_id=pos.get("market_id", ""),
        question=pos.get("question", ""),
        strategy=strategy_name,
        signal=pos.get("signal", ""),
        entry_price=entry_price,
        predicted_prob=predicted_prob,
        predicted_edge=edge,
        outcome=pos["status"],
        pnl=pnl,
        size_usdc=pos.get("size_usdc", 0),
        metadata={**meta, "analysis": analysis},
    )

    logger.info(
        f"Crypto resolved: {pos.get('trade_id')} "
        f"{'WON' if won else 'LOST'} ${pnl:+.2f} | {analysis}"
    )

    # Send Telegram notification with analysis
    from .notifications.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    if notifier.is_configured():
        emoji = "\U0001f389" if won else "\U0001f4a5"
        notifier.send_message(
            f"{emoji} <b>${pnl:+.2f}</b> {coin.upper()} {direction.upper()}\n"
            f"Actual: {actual_direction.upper()} | Mom: {momentum:+.2f}\n"
            f"{'Correct call' if won else 'Wrong direction'} | ROI: {roi:+.0f}%"
        )

    return True


def _resolve_tweet_position(pos: dict, engine, journal, config: dict) -> bool:
    """Resolve a tweet bracket position — CONSERVATIVE approach.

    Only auto-resolves when there is VERY strong evidence of a win
    (best bid > 0.95). Never auto-marks losses — those are handled
    by on-chain settlement when the event period ends.

    For neg_risk tweet bracket markets, low asks with no bids can mean
    EITHER a win (tokens being redeemed) or a loss (tokens worthless),
    so we don't resolve on that signal alone.
    """
    logger = logging.getLogger("trading_bot")

    if not engine.session:
        logger.warning("Cannot resolve tweet position — no session loaded")
        return False

    token_id = pos.get("token_id", "")
    if not token_id:
        return False

    client = PolymarketClient(config)
    try:
        book = client.get_orderbook(token_id)
    except Exception as e:
        logger.debug(f"Could not fetch orderbook for {pos.get('trade_id')}: {e}")
        return False

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    best_bid = max(float(b["price"]) for b in bids) if bids else 0.0
    best_ask = min(float(a["price"]) for a in asks) if asks else 1.0

    # ONLY resolve as WIN when best bid is very high (clear resolution)
    if best_bid <= 0.95:
        return False

    # Clear win — best bid > 0.95 means market is near-certain YES
    won = True
    # Only wins reach here
    pnl = pos.get("shares", 0) * 1.0 - pos.get("size_usdc", 0)
    pos["status"] = "won"
    if engine.session:
        engine.session.wins += 1

    pos["pnl"] = round(pnl, 4)
    pos["exit_time"] = datetime.now(timezone.utc).isoformat()

    engine.risk.record_trade_exit(pos.get("trade_id", pos.get("market_id", "")), pnl)
    if engine.session:
        engine.session.total_pnl += pnl
        engine.session.current_balance = engine.risk.portfolio.balance
        engine.session.closed_trades.append(pos)
        engine.session.positions = [
            p for p in engine.session.positions
            if p.get("trade_id") != pos.get("trade_id")
        ]
        engine._save_session()

    meta = pos.get("metadata", {})
    entry_price = pos.get("entry_price", 0)

    if journal:
        journal.log_resolution(
            trade_id=pos.get("trade_id", ""),
            market_id=pos.get("market_id", ""),
            question=pos.get("question", ""),
            strategy="tweet_brackets",
            signal=pos.get("signal", ""),
            entry_price=entry_price,
            predicted_prob=meta.get("model_prob", entry_price),
            predicted_edge=pos.get("edge", 0),
            outcome=pos["status"],
            pnl=pnl,
            size_usdc=pos.get("size_usdc", 0),
            metadata=meta,
        )

    logger.info(
        f"Tweet position resolved: {pos.get('trade_id')} "
        f"{'WON' if won else 'LOST'} pnl=${pnl:+.2f} "
        f"(bid={best_bid:.3f} ask={best_ask:.3f})"
    )

    from .notifications.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    if notifier.is_configured():
        emoji = "\U0001f389" if won else "\U0001f4a5"
        q = pos.get('question', '')[:45]
        notifier.send_message(
            f"{emoji} {'WON' if won else 'LOST'} <b>${pnl:+.2f}</b> | {q}"
        )

    return True


def _check_profit_taking(engine, config: dict, journal):
    """Check open positions for profit-taking opportunities.

    Sells positions where the current market price has moved significantly
    in our favor. This locks in profit rather than waiting for full
    resolution (which could be days for tweet brackets).

    Thresholds:
    - Crypto 15-min: sell when market price hits 0.95 (lock in ~90% profit)
    - Tweet brackets: sell if >80% of max profit captured or >15% ROI
    """
    import time
    logger = logging.getLogger("trading_bot")

    if not engine.session or not engine._clob:
        return

    client = PolymarketClient(config)

    for pos in list(engine.session.positions):
        if pos.get("status") != "open":
            continue

        meta = pos.get("metadata", {})
        strategy = meta.get("strategy", "")

        token_id = pos.get("token_id", "")
        if not token_id:
            continue

        # Get current market price for our token
        try:
            book = client.get_orderbook(token_id)
            bids = book.get("bids", [])
            if not bids:
                continue
            # Best bid = what we'd get if we sell now
            current_bid = max(float(b.get("price", 0)) for b in bids)
        except Exception as e:
            logger.debug(f"Could not fetch orderbook for {pos.get('trade_id')}: {e}")
            continue

        entry_price = pos.get("entry_price", 0)
        if entry_price <= 0:
            continue

        # Calculate potential profit
        # If we sell at current_bid: proceeds = shares * current_bid
        # Cost was size_usdc
        shares = pos.get("shares", 0)
        cost = pos.get("size_usdc", 0)
        proceeds = shares * current_bid
        potential_pnl = proceeds - cost
        roi = potential_pnl / cost if cost > 0 else 0

        # Max possible profit = shares * 1.0 - cost (if market resolves in our favor)
        max_profit = shares * 1.0 - cost
        profit_captured = potential_pnl / max_profit if max_profit > 0 else 0

        # Profit-taking conditions vary by strategy.
        # No stop-loss for binary hourly candles — mid-candle dips are noise,
        # the position resolves to $1 or $0 at candle close regardless.
        if strategy in ("crypto_momentum_15m", "crypto_momentum_1h"):
            # CRYPTO: cash out at 80%+ ROI — lock in profit
            should_sell = roi >= 0.80 and potential_pnl > 0.10
        else:
            # NON-CRYPTO: sell if >80% of max profit captured or >15% ROI
            should_sell = (roi > 0.15 and potential_pnl > 0.50) or (profit_captured > 0.80 and potential_pnl > 0.50)

        if should_sell:
            direction = meta.get("direction", "?")
            momentum = meta.get("momentum_score", 0)
            coin = meta.get("coin", "?")
            exit_type = "profit_taking"
            analysis = (
                f"Early exit at {roi:.0%} ROI | Entry ${entry_price:.3f} -> Bid ${current_bid:.3f}\n"
                f"Momentum was {momentum:+.2f} ({direction.upper()}) — signal correct, taking profit"
            )

            logger.info(
                f"{exit_type}: {pos.get('trade_id')} | "
                f"ROI={roi:.1%} | {analysis}"
            )
            result = engine.sell_position(pos, current_bid)
            if result.get("status") == "sold":
                actual_pnl = result.get("pnl", 0)
                actual_roi = (actual_pnl / cost * 100) if cost > 0 else 0
                print(
                    f"  >> PROFIT TAKEN: {pos.get('trade_id')} | "
                    f"${actual_pnl:+.2f} ({actual_roi:.0f}% ROI)"
                )
                if journal:
                    outcome = "sold_profit"
                    journal.log_resolution(
                        trade_id=pos.get("trade_id", ""),
                        market_id=pos.get("market_id", ""),
                        question=pos.get("question", ""),
                        strategy=strategy,
                        signal=pos.get("signal", ""),
                        entry_price=entry_price,
                        predicted_prob=meta.get("model_prob", entry_price),
                        predicted_edge=pos.get("edge", 0),
                        outcome=outcome,
                        pnl=actual_pnl,
                        size_usdc=cost,
                        metadata={**meta, "exit_type": exit_type, "roi": actual_roi, "analysis": analysis},
                    )
                from .notifications.telegram import TelegramNotifier
                notifier = TelegramNotifier()
                if notifier.is_configured():
                    notifier.send_message(
                        f"\U0001f4b0 <b>PROFIT ${actual_pnl:+.2f}</b> "
                        f"{coin.upper() if coin != '?' else ''} {direction.upper()}\n"
                        f"ROI: {actual_roi:+.0f}% | Entry ${entry_price:.3f} -> ${current_bid:.3f}"
                    )
            else:
                logger.warning(f"Profit-taking sell failed: {result.get('error')}")


def cmd_stats(config: dict):
    """Show trade journal accuracy and calibration report."""
    journal = TradeJournal()
    days = 7

    stats = journal.get_accuracy_stats(days=days)

    print(f"\n{'='*55}")
    print(f"  TRADE JOURNAL — last {days} days")
    print(f"{'='*55}")

    if stats["total_trades"] == 0:
        print("\n  No resolved trades in journal yet.")
        print("  Trades are logged when positions resolve (15-min crypto,")
        print("  tweet brackets, or Gamma API market closures).\n")
        return

    print(f"\n  Overall:")
    print(f"    Trades:  {stats['total_trades']} (W:{stats['wins']} L:{stats['losses']})")
    print(f"    Win Rate: {stats['win_rate']:.1%}")
    print(f"    Total PnL: ${stats['total_pnl']:+.2f}")
    print(f"    Avg Predicted Edge: {stats['avg_predicted_edge']:+.4f}")
    print(f"    Avg Realized PnL%:  {stats['avg_realized_pnl_pct']:+.4f}")

    # Per-strategy breakdown
    if stats["by_strategy"]:
        print(f"\n  By Strategy:")
        for name, s in stats["by_strategy"].items():
            print(
                f"    {name}: {s['total_trades']} trades, "
                f"W:{s['wins']} L:{s['losses']}, "
                f"WR={s['win_rate']:.0%}, "
                f"PnL=${s['total_pnl']:+.2f}"
            )

    # Calibration table
    if stats["calibration"]:
        print(f"\n  Calibration (predicted vs actual win rate):")
        print(f"    {'Bin':>9s}  {'Pred':>6s}  {'Actual':>6s}  {'Count':>5s}")
        print(f"    {'─'*9}  {'─'*6}  {'─'*6}  {'─'*5}")
        for row in stats["calibration"]:
            print(
                f"    {row['bin']:>9s}  "
                f"{row['predicted']:6.3f}  "
                f"{row['actual']:6.3f}  "
                f"{row['count']:5d}"
            )

    # Recent trades
    recent = journal.get_recent_trades(n=10)
    if recent:
        print(f"\n  Recent Trades (last 10):")
        for t in recent:
            outcome = t.get("outcome", "?")
            icon = "W" if outcome == "won" else "L"
            pnl = t.get("pnl", 0)
            q = t.get("question", "")[:45]
            strat = t.get("strategy", "?")
            print(
                f"    [{icon}] ${pnl:+6.2f}  {strat:<22s}  {q}"
            )

    print(f"\n{'='*55}\n")


def cmd_report(config: dict):
    """Show performance report with news intelligence impact analysis."""
    print(generate_report(config))


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  scan       Scan markets for trade signals
  backtest   Run backtest on historical data
  paper      Start/resume paper trading
  live       Start/resume live trading (real USDC)
  fast       Fast-cycle short-term trading (15-min crypto, tweets)
  collect    Collect market snapshots
  resolve    Fetch resolved markets for edge analysis
  calibrate  Calibration analysis on resolved data
  edge       Monte Carlo edge analysis across strategies
  status     Show system status
  report     Performance report with news impact analysis
  stats      Trade journal accuracy & calibration report
        """,
    )

    parser.add_argument(
        "command",
        choices=["scan", "backtest", "paper", "live", "fast", "collect", "resolve", "calibrate", "edge", "status", "report", "stats"],
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
        "live": cmd_live,
        "fast": cmd_fast,
        "collect": cmd_collect,
        "resolve": cmd_resolve,
        "calibrate": cmd_calibrate,
        "edge": cmd_edge,
        "status": cmd_status,
        "report": cmd_report,
        "stats": cmd_stats,
    }

    commands[args.command](config)


if __name__ == "__main__":
    main()
