"""Performance reporting — analyze paper trading results.

Breaks down performance by:
- News-boosted vs pure statistical trades
- News confirmed vs contradicted trades
- Win rate, P&L, edge accuracy
- Market category performance
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("trading_bot.report")

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def generate_report(config: dict) -> str:
    """Generate a comprehensive performance report from the active session."""
    # Prefer live session, fallback to paper
    live_path = DATA_DIR / "live_session.json"
    paper_path = DATA_DIR / "paper_session.json"

    if live_path.exists():
        session_path = live_path
    elif paper_path.exists():
        session_path = paper_path
    else:
        return "No session found. Run 'live' or 'paper' first."

    with open(session_path) as f:
        session = json.load(f)

    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append("  TRADING BOT PERFORMANCE REPORT")
    lines.append("=" * 60)

    # ── Session overview ────────────────────────────────────────────
    lines.append(f"\n  Session:  {session.get('session_id', '?')}")
    lines.append(f"  Strategy: {session.get('strategy', '?')}")
    lines.append(f"  Started:  {session.get('started', '?')[:19]}")
    lines.append(f"  Balance:  ${session.get('starting_balance', 0):.2f} -> ${session.get('current_balance', 0):.2f}")
    lines.append(f"  Total PnL: ${session.get('total_pnl', 0):+.2f}")
    lines.append(f"  Trades:   {session.get('total_trades', 0)} (W:{session.get('wins', 0)} L:{session.get('losses', 0)})")

    total_trades = session.get("total_trades", 0)
    wins = session.get("wins", 0)
    if total_trades > 0:
        lines.append(f"  Win Rate: {wins/total_trades:.1%}")
    else:
        lines.append("  Win Rate: N/A")

    # ── Collect all positions ───────────────────────────────────────
    all_positions = []
    for p in session.get("positions", []):
        all_positions.append(p)
    for p in session.get("closed_trades", []):
        all_positions.append(p)

    if not all_positions:
        lines.append("\n  No trades to analyze yet.")
        lines.append("=" * 60)
        return "\n".join(lines)

    # ── Categorize trades ───────────────────────────────────────────
    news_boosted = []     # News confirmed the direction
    news_contradicted = []  # News contradicted the direction
    no_news = []          # No news signal
    open_positions = []

    total_invested = 0.0

    for p in all_positions:
        meta = p.get("metadata", {})
        status = p.get("status", "open")

        if status == "open":
            open_positions.append(p)
            total_invested += p.get("size_usdc", 0)
            continue

        news_strength = meta.get("news_strength", 0)
        contradicted = meta.get("news_contradicted", False)

        if news_strength > 0.1 and not contradicted:
            news_boosted.append(p)
        elif contradicted:
            news_contradicted.append(p)
        else:
            no_news.append(p)

    # ── News impact analysis ────────────────────────────────────────
    lines.append(f"\n{'─' * 60}")
    lines.append("  NEWS INTELLIGENCE IMPACT")
    lines.append(f"{'─' * 60}")

    def _category_stats(trades, label):
        if not trades:
            lines.append(f"\n  {label}: 0 trades")
            return
        wins_cat = sum(1 for t in trades if t.get("status") == "won")
        losses_cat = sum(1 for t in trades if t.get("status") == "lost")
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        total_size = sum(t.get("size_usdc", 0) for t in trades)
        avg_edge = sum(t.get("edge", 0) for t in trades) / len(trades)
        avg_conf = sum(t.get("confidence", 0) for t in trades) / len(trades)
        wr = wins_cat / len(trades) if trades else 0

        lines.append(f"\n  {label}: {len(trades)} trades")
        lines.append(f"    Win Rate:  {wr:.1%} (W:{wins_cat} L:{losses_cat})")
        lines.append(f"    PnL:       ${total_pnl:+.2f}")
        lines.append(f"    Invested:  ${total_size:.2f}")
        lines.append(f"    Avg Edge:  {avg_edge:.4f}")
        lines.append(f"    Avg Conf:  {avg_conf:.2f}")
        if total_size > 0:
            lines.append(f"    ROI:       {total_pnl/total_size*100:+.1f}%")

    _category_stats(news_boosted, "News-Boosted (news confirmed direction)")
    _category_stats(news_contradicted, "News-Contradicted (news opposed direction)")
    _category_stats(no_news, "Pure Statistical (no relevant news)")

    # ── Open positions ──────────────────────────────────────────────
    lines.append(f"\n{'─' * 60}")
    lines.append(f"  OPEN POSITIONS ({len(open_positions)})")
    lines.append(f"{'─' * 60}")

    if open_positions:
        total_open_value = sum(p.get("size_usdc", 0) for p in open_positions)
        lines.append(f"  Total invested: ${total_open_value:.2f}")

        # Count by type
        open_news = sum(1 for p in open_positions if p.get("metadata", {}).get("news_strength", 0) > 0.1)
        open_stat = len(open_positions) - open_news
        lines.append(f"  News-informed: {open_news} | Pure statistical: {open_stat}")

        lines.append("")
        for p in open_positions:
            q = p.get("question", "")[:45]
            sig = p.get("signal", "?")
            price = p.get("entry_price", 0)
            size = p.get("size_usdc", 0)
            edge = p.get("edge", 0)
            meta = p.get("metadata", {})
            news_dir = meta.get("news_direction", "")
            news_str = meta.get("news_strength", 0)

            news_tag = ""
            if news_str > 0.1:
                news_tag = f" [NEWS:{news_dir}]"

            lines.append(f"    {sig} ${size:.0f} @ {price:.3f} edge={edge:.3f}{news_tag}")
            lines.append(f"      {q}...")
    else:
        lines.append("  No open positions.")

    # ── Closed trades detail ────────────────────────────────────────
    closed = [p for p in all_positions if p.get("status") in ("won", "lost")]
    if closed:
        lines.append(f"\n{'─' * 60}")
        lines.append(f"  CLOSED TRADES ({len(closed)})")
        lines.append(f"{'─' * 60}")

        for p in closed:
            q = p.get("question", "")[:40]
            sig = p.get("signal", "?")
            status = p.get("status", "?").upper()
            pnl = p.get("pnl", 0)
            size = p.get("size_usdc", 0)
            meta = p.get("metadata", {})
            news_str = meta.get("news_strength", 0)
            news_tag = f" [NEWS]" if news_str > 0.1 else ""

            lines.append(f"    {status} ${pnl:+.2f} | {sig} ${size:.0f}{news_tag} | {q}...")

    # ── Summary verdict ─────────────────────────────────────────────
    lines.append(f"\n{'─' * 60}")
    lines.append("  VERDICT")
    lines.append(f"{'─' * 60}")

    if news_boosted and no_news:
        news_wr = sum(1 for t in news_boosted if t.get("status") == "won") / len(news_boosted) if news_boosted else 0
        stat_wr = sum(1 for t in no_news if t.get("status") == "won") / len(no_news) if no_news else 0
        news_roi = sum(t.get("pnl", 0) for t in news_boosted) / sum(t.get("size_usdc", 1) for t in news_boosted) * 100
        stat_roi = sum(t.get("pnl", 0) for t in no_news) / sum(t.get("size_usdc", 1) for t in no_news) * 100

        if news_wr > stat_wr:
            lines.append(f"  News intelligence is HELPING: {news_wr:.0%} vs {stat_wr:.0%} win rate")
        elif news_wr < stat_wr:
            lines.append(f"  News intelligence is HURTING: {news_wr:.0%} vs {stat_wr:.0%} win rate")
        else:
            lines.append(f"  News intelligence is NEUTRAL: both at {news_wr:.0%} win rate")

        lines.append(f"  News ROI: {news_roi:+.1f}% vs Statistical ROI: {stat_roi:+.1f}%")
    elif not closed:
        lines.append("  No closed trades yet — check back after markets resolve.")
    else:
        lines.append("  Not enough data to compare news vs statistical performance.")

    lines.append("=" * 60)
    lines.append("")

    return "\n".join(lines)
