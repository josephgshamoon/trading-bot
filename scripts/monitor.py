#!/usr/bin/env python3
"""Automated monitoring — runs every 6 hours via cron.

Reads the trade journal, computes stats, stores a performance snapshot
in Supermemory, and sends a Telegram summary if there are new trades.

Cron entry (update path to match your setup):
  0 */6 * * * cd /path/to/trading-bot && venv/bin/python scripts/monitor.py >> logs/monitor.log 2>&1
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from src.data.journal import TradeJournal
from src.data.memory import BotMemory
from src.notifications.telegram import TelegramNotifier


def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Monitor starting...")

    journal = TradeJournal()
    memory = BotMemory()
    notifier = TelegramNotifier()

    # ── 7-day stats ──────────────────────────────────────────────
    stats = journal.get_accuracy_stats(days=7)
    recent = journal.get_recent_trades(n=20)

    total = stats["total_trades"]
    print(f"  7-day stats: {total} trades, WR={stats['win_rate']:.0%}, PnL=${stats['total_pnl']:+.2f}")

    # ── Build summary text ───────────────────────────────────────
    lines = [
        f"7-day performance ({now.strftime('%Y-%m-%d %H:%M UTC')}):",
        f"  Trades: {total} (W:{stats['wins']} L:{stats['losses']})",
        f"  Win rate: {stats['win_rate']:.1%}",
        f"  Total PnL: ${stats['total_pnl']:+.2f}",
        f"  Avg predicted edge: {stats['avg_predicted_edge']:+.4f}",
        f"  Avg realized PnL%: {stats['avg_realized_pnl_pct']:+.4f}",
    ]

    if stats["by_strategy"]:
        lines.append("\n  By strategy:")
        for name, s in stats["by_strategy"].items():
            lines.append(
                f"    {name}: {s['total_trades']}T W:{s['wins']} L:{s['losses']} "
                f"WR={s['win_rate']:.0%} PnL=${s['total_pnl']:+.2f}"
            )

    if stats["calibration"]:
        lines.append("\n  Calibration:")
        for row in stats["calibration"]:
            lines.append(
                f"    {row['bin']}: predicted={row['predicted']:.3f} "
                f"actual={row['actual']:.3f} (n={row['count']})"
            )

    if recent:
        lines.append(f"\n  Last 5 trades:")
        for t in recent[:5]:
            icon = "W" if t.get("outcome") == "won" else "L"
            lines.append(
                f"    [{icon}] ${t.get('pnl', 0):+.2f} | "
                f"{t.get('strategy', '?')} | {t.get('question', '')[:50]}"
            )

    summary = "\n".join(lines)
    print(summary)

    # ── Store in Supermemory ─────────────────────────────────────
    if memory.is_configured:
        result = memory.store_performance_snapshot(stats, summary)
        if result:
            print(f"  Stored in Supermemory: {result.get('id', 'ok')}")
        else:
            print("  Failed to store in Supermemory")

        # Store observations based on patterns
        if total >= 5:
            if stats["win_rate"] < 0.35:
                memory.store_observation(
                    f"[{now.strftime('%Y-%m-%d')}] WARNING: Win rate below 35% "
                    f"({stats['win_rate']:.0%} over {total} trades). "
                    f"Strategy may need recalibration. "
                    f"Avg edge predicted: {stats['avg_predicted_edge']:+.4f}, "
                    f"avg realized: {stats['avg_realized_pnl_pct']:+.4f}",
                    category="alert",
                )
            elif stats["win_rate"] > 0.60:
                memory.store_observation(
                    f"[{now.strftime('%Y-%m-%d')}] STRONG: Win rate at "
                    f"{stats['win_rate']:.0%} over {total} trades. "
                    f"PnL=${stats['total_pnl']:+.2f}. Strategy performing well.",
                    category="positive",
                )

            # Calibration gap detection
            for row in stats.get("calibration", []):
                if row["count"] >= 3:
                    gap = abs(row["predicted"] - row["actual"])
                    if gap > 0.15:
                        memory.store_observation(
                            f"[{now.strftime('%Y-%m-%d')}] CALIBRATION GAP in "
                            f"{row['bin']} bin: predicted {row['predicted']:.3f} "
                            f"vs actual {row['actual']:.3f} (n={row['count']}). "
                            f"Model is {'overconfident' if row['predicted'] > row['actual'] else 'underconfident'}.",
                            category="calibration",
                        )
    else:
        print("  Supermemory not configured — skipping memory storage")

    # ── Telegram summary (only if there are trades) ──────────────
    if total > 0 and notifier.is_configured():
        pnl_emoji = "\U0001f4c8" if stats["total_pnl"] >= 0 else "\U0001f4c9"
        tg_lines = [
            f"{pnl_emoji} <b>7-Day Performance Report</b>",
            "",
            f"Trades: <code>{total}</code> (W:{stats['wins']} L:{stats['losses']})",
            f"Win Rate: <code>{stats['win_rate']:.1%}</code>",
            f"Total PnL: <code>${stats['total_pnl']:+.2f}</code>",
        ]
        if stats["by_strategy"]:
            tg_lines.append("")
            for name, s in stats["by_strategy"].items():
                tg_lines.append(
                    f"  {name}: {s['total_trades']}T "
                    f"WR={s['win_rate']:.0%} ${s['total_pnl']:+.2f}"
                )

        tg_lines.append(f"\n\U0001f552 {now.strftime('%Y-%m-%d %H:%M UTC')}")
        notifier.send_message("\n".join(tg_lines))
        print("  Telegram summary sent")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Monitor done.\n")


if __name__ == "__main__":
    main()
