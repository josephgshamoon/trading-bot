"""Edge analysis engine — Monte Carlo simulation and strategy comparison.

Runs backtests across multiple random seeds to find strategies and
parameter combinations that show consistent profitability. This helps
distinguish real edge from lucky variance.
"""

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from ..data.indicators import MarketIndicators
from ..strategy import STRATEGIES
from .backtest import BacktestEngine

logger = logging.getLogger("trading_bot.edge_analyzer")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def monte_carlo_backtest(
    config: dict,
    snapshots: list[dict],
    strategy_name: str,
    n_simulations: int = 100,
) -> dict:
    """Run multiple backtests with different random seeds.

    This separates signal from noise. A strategy with real edge
    should be profitable across MOST simulations, not just lucky ones.

    Returns:
        Dict with mean/median/worst/best PnL, win rate, and consistency.
    """
    strategy_cls = STRATEGIES.get(strategy_name)
    if not strategy_cls:
        return {"error": f"Unknown strategy: {strategy_name}"}

    strategy = strategy_cls(config)
    engine = BacktestEngine(config)

    results = []
    for i in range(n_simulations):
        result = engine.run(
            strategy=strategy,
            snapshots=snapshots,
            indicators_fn=lambda snap: MarketIndicators.compute_all(snap),
            seed=i * 17 + 42,  # Spread seeds for independence
        )
        results.append({
            "seed": i,
            "total_pnl": result.total_pnl,
            "roi_pct": result.roi_pct,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
            "profit_factor": result.profit_factor,
            "max_drawdown": result.max_drawdown,
            "sharpe_ratio": result.sharpe_ratio,
        })

    if not results:
        return {"error": "No simulations completed"}

    pnls = [r["total_pnl"] for r in results]
    rois = [r["roi_pct"] for r in results]
    win_rates = [r["win_rate"] for r in results]
    sharpes = [r["sharpe_ratio"] for r in results]

    pnls.sort()
    rois.sort()

    profitable = sum(1 for p in pnls if p > 0)

    return {
        "strategy": strategy_name,
        "simulations": n_simulations,
        "total_trades_per_sim": results[0]["total_trades"] if results else 0,
        "consistency": profitable / n_simulations,
        "pnl": {
            "mean": sum(pnls) / len(pnls),
            "median": pnls[len(pnls) // 2],
            "best": pnls[-1],
            "worst": pnls[0],
            "p5": pnls[int(len(pnls) * 0.05)],   # 5th percentile
            "p95": pnls[int(len(pnls) * 0.95)],   # 95th percentile
        },
        "roi_pct": {
            "mean": sum(rois) / len(rois),
            "median": rois[len(rois) // 2],
        },
        "win_rate": {
            "mean": sum(win_rates) / len(win_rates),
        },
        "sharpe": {
            "mean": sum(sharpes) / len(sharpes),
        },
    }


def compare_all_strategies(
    config: dict,
    snapshots: list[dict],
    n_simulations: int = 50,
) -> list[dict]:
    """Run Monte Carlo analysis across all available strategies.

    Returns results sorted by consistency (profitable simulation rate).
    """
    results = []
    # Only test strategies that work in backtest mode (no news needed)
    testable = ["value_betting", "momentum", "arbitrage"]

    for name in testable:
        logger.info(f"Running Monte Carlo for {name}...")
        mc = monte_carlo_backtest(config, snapshots, name, n_simulations)
        if "error" not in mc:
            results.append(mc)

    results.sort(key=lambda r: r["consistency"], reverse=True)
    return results


def analyze_trade_characteristics(
    config: dict,
    snapshots: list[dict],
    strategy_name: str,
) -> dict:
    """Analyze what characteristics distinguish winning vs losing trades.

    Looks at: price level, volume, liquidity, edge size, position size
    to find which market conditions produce the most consistent profits.
    """
    strategy_cls = STRATEGIES.get(strategy_name)
    if not strategy_cls:
        return {"error": f"Unknown strategy: {strategy_name}"}

    strategy = strategy_cls(config)
    engine = BacktestEngine(config)

    result = engine.run(
        strategy=strategy,
        snapshots=snapshots,
        indicators_fn=lambda snap: MarketIndicators.compute_all(snap),
        seed=42,
    )

    if not result.trades:
        return {"error": "No trades generated"}

    wins = [t for t in result.trades if t["outcome"] == "win"]
    losses = [t for t in result.trades if t["outcome"] == "loss"]

    # Price level analysis — which entry prices produce more wins?
    price_buckets = {}
    for trade in result.trades:
        bucket = f"{int(trade['entry_price'] * 10) / 10:.1f}"
        if bucket not in price_buckets:
            price_buckets[bucket] = {"wins": 0, "losses": 0, "total_pnl": 0}
        b = price_buckets[bucket]
        b["total_pnl"] += trade["pnl"]
        if trade["outcome"] == "win":
            b["wins"] += 1
        else:
            b["losses"] += 1

    for b in price_buckets.values():
        total = b["wins"] + b["losses"]
        b["win_rate"] = b["wins"] / total if total > 0 else 0
        b["n"] = total

    # Edge size analysis
    edge_buckets = {"small (0-0.05)": [], "medium (0.05-0.10)": [], "large (>0.10)": []}
    for trade in result.trades:
        edge = trade.get("edge", 0)
        if edge < 0.05:
            edge_buckets["small (0-0.05)"].append(trade)
        elif edge < 0.10:
            edge_buckets["medium (0.05-0.10)"].append(trade)
        else:
            edge_buckets["large (>0.10)"].append(trade)

    edge_analysis = {}
    for bucket_name, trades in edge_buckets.items():
        if trades:
            w = sum(1 for t in trades if t["outcome"] == "win")
            total_pnl = sum(t["pnl"] for t in trades)
            edge_analysis[bucket_name] = {
                "n": len(trades),
                "wins": w,
                "win_rate": round(w / len(trades), 3),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / len(trades), 2),
            }

    # Signal direction analysis
    buy_yes_trades = [t for t in result.trades if t["signal"] == "BUY_YES"]
    buy_no_trades = [t for t in result.trades if t["signal"] == "BUY_NO"]

    direction_analysis = {}
    for name, trades in [("BUY_YES", buy_yes_trades), ("BUY_NO", buy_no_trades)]:
        if trades:
            w = sum(1 for t in trades if t["outcome"] == "win")
            total_pnl = sum(t["pnl"] for t in trades)
            direction_analysis[name] = {
                "n": len(trades),
                "wins": w,
                "win_rate": round(w / len(trades), 3),
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / len(trades), 2),
            }

    return {
        "strategy": strategy_name,
        "total_trades": len(result.trades),
        "overall_win_rate": result.win_rate,
        "overall_pnl": round(result.total_pnl, 2),
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
        "price_buckets": price_buckets,
        "edge_buckets": edge_analysis,
        "direction": direction_analysis,
    }


def format_edge_report(mc_results: list[dict], characteristics: dict | None = None) -> str:
    """Format edge analysis into a comprehensive report."""
    lines = [
        f"\n{'='*65}",
        f"  EDGE ANALYSIS REPORT",
        f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"{'='*65}",
    ]

    if not mc_results:
        lines.append("  No results to display.")
        return "\n".join(lines)

    lines.append(f"\n  {'─'*61}")
    lines.append(f"  MONTE CARLO STRATEGY COMPARISON ({mc_results[0]['simulations']} simulations each)")
    lines.append(f"  {'─'*61}")

    for mc in mc_results:
        consistency_pct = mc["consistency"] * 100
        star = " ***" if consistency_pct >= 90 else " **" if consistency_pct >= 70 else ""
        lines.extend([
            f"\n  {mc['strategy'].upper()}{star}",
            f"    Consistency:    {consistency_pct:.0f}% of simulations profitable",
            f"    Trades/sim:     {mc['total_trades_per_sim']}",
            f"    Mean PnL:       ${mc['pnl']['mean']:+.2f}  (ROI: {mc['roi_pct']['mean']:+.1f}%)",
            f"    Median PnL:     ${mc['pnl']['median']:+.2f}",
            f"    5th-95th pctl:  ${mc['pnl']['p5']:+.2f} to ${mc['pnl']['p95']:+.2f}",
            f"    Best / Worst:   ${mc['pnl']['best']:+.2f} / ${mc['pnl']['worst']:+.2f}",
            f"    Avg Win Rate:   {mc['win_rate']['mean']:.1%}",
            f"    Avg Sharpe:     {mc['sharpe']['mean']:.2f}",
        ])

    # Trade characteristics breakdown
    if characteristics and "error" not in characteristics:
        lines.extend([
            f"\n  {'─'*61}",
            f"  TRADE CHARACTERISTICS ({characteristics['strategy']})",
            f"  {'─'*61}",
            f"    Total trades: {characteristics['total_trades']}",
            f"    Win rate: {characteristics['overall_win_rate']:.1%}",
            f"    Avg win: ${characteristics['avg_win']:+.2f}  |  Avg loss: ${characteristics['avg_loss']:+.2f}",
        ])

        # Edge size breakdown
        edge = characteristics.get("edge_buckets", {})
        if edge:
            lines.append(f"\n    Edge Size Analysis:")
            for bucket, data in sorted(edge.items()):
                lines.append(
                    f"      {bucket:<20} n={data['n']:>3}  "
                    f"WR={data['win_rate']:.0%}  "
                    f"PnL=${data['total_pnl']:>+8.2f}  "
                    f"avg=${data['avg_pnl']:>+6.2f}"
                )

        # Direction breakdown
        direction = characteristics.get("direction", {})
        if direction:
            lines.append(f"\n    Direction Analysis:")
            for dir_name, data in direction.items():
                lines.append(
                    f"      {dir_name:<10} n={data['n']:>3}  "
                    f"WR={data['win_rate']:.0%}  "
                    f"PnL=${data['total_pnl']:>+8.2f}"
                )

        # Price bucket breakdown (top 5 by PnL)
        price = characteristics.get("price_buckets", {})
        if price:
            lines.append(f"\n    Entry Price Analysis:")
            sorted_prices = sorted(price.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
            for bucket, data in sorted_prices[:8]:
                lines.append(
                    f"      price ~{bucket}  n={data['n']:>3}  "
                    f"WR={data['win_rate']:.0%}  "
                    f"PnL=${data['total_pnl']:>+8.2f}"
                )

    lines.append(f"\n{'='*65}")

    # Key takeaways
    best = mc_results[0] if mc_results else None
    if best and best["consistency"] >= 0.7:
        lines.extend([
            f"\n  KEY FINDINGS:",
            f"  - Best strategy: {best['strategy']} ({best['consistency']*100:.0f}% consistency)",
            f"  - Expected PnL range: ${best['pnl']['p5']:+.2f} to ${best['pnl']['p95']:+.2f}",
        ])

        if characteristics and "error" not in characteristics:
            edge = characteristics.get("edge_buckets", {})
            best_edge = max(edge.items(), key=lambda x: x[1].get("avg_pnl", 0)) if edge else None
            if best_edge:
                lines.append(f"  - Most profitable edge bucket: {best_edge[0]} (avg ${best_edge[1]['avg_pnl']:+.2f}/trade)")

    lines.append("")
    return "\n".join(lines)
