"""Trade journal — append-only JSONL logs for every cycle and resolution.

One file per day: ``data/journal/2026-02-14.jsonl``

Two record types:

* ``"cycle"``      — written every 5-min evaluation: timestamp, balance,
                     all signals found (executed / skipped + reason), open count.
* ``"resolution"`` — written when a trade resolves: predicted vs actual
                     probability, predicted edge vs realised PnL, strategy name.

Reading helpers aggregate across multiple day-files so the ``stats`` command
can report win rate, calibration, and recent trades.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("trading_bot.journal")

JOURNAL_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "journal"


class TradeJournal:
    """Append-only JSONL trade journal."""

    def __init__(self, journal_dir: Path | None = None):
        self.journal_dir = journal_dir or JOURNAL_DIR
        self.journal_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _today_file(self) -> Path:
        return self.journal_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"

    def _append(self, record: dict):
        path = self._today_file()
        with open(path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def _read_days(self, days: int) -> list[dict]:
        """Read all records from the last *days* day-files."""
        records: list[dict] = []
        today = datetime.now(timezone.utc).date()
        for offset in range(days):
            d = today - timedelta(days=offset)
            path = self.journal_dir / f"{d.isoformat()}.jsonl"
            if not path.exists():
                continue
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Corrupt journal line in %s", path.name)
        return records

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def log_cycle(
        self,
        balance: float,
        signals: list[dict],
        open_positions: int,
        strategy: str = "",
    ):
        """Log a single evaluation cycle.

        Each entry in *signals* should be a dict with at least:
        ``market_id``, ``question``, ``signal``, ``edge``, ``confidence``,
        ``action`` (``"executed"`` or ``"skipped"``), and optionally
        ``skip_reason``.
        """
        record = {
            "type": "cycle",
            "ts": datetime.now(timezone.utc).isoformat(),
            "balance": round(balance, 4),
            "strategy": strategy,
            "open_positions": open_positions,
            "signals": signals,
        }
        self._append(record)
        logger.debug("Logged cycle: %d signals, balance=$%.2f", len(signals), balance)

    def log_resolution(
        self,
        trade_id: str,
        market_id: str,
        question: str,
        strategy: str,
        signal: str,
        entry_price: float,
        predicted_prob: float,
        predicted_edge: float,
        outcome: str,
        pnl: float,
        size_usdc: float = 0.0,
        metadata: dict | None = None,
    ):
        """Log the outcome of a resolved trade."""
        won = outcome in ("won",)
        actual_prob = 1.0 if won else 0.0

        record = {
            "type": "resolution",
            "ts": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "market_id": market_id,
            "question": question,
            "strategy": strategy,
            "signal": signal,
            "entry_price": round(entry_price, 6),
            "predicted_prob": round(predicted_prob, 6),
            "predicted_edge": round(predicted_edge, 6),
            "actual_prob": actual_prob,
            "outcome": outcome,
            "pnl": round(pnl, 4),
            "size_usdc": round(size_usdc, 4),
            **({"metadata": metadata} if metadata else {}),
        }
        self._append(record)
        logger.info(
            "Logged resolution: %s %s pnl=$%.2f",
            trade_id, outcome, pnl,
        )

    # ------------------------------------------------------------------
    # Read / aggregation API
    # ------------------------------------------------------------------

    def get_recent_trades(self, n: int = 20) -> list[dict]:
        """Return the last *n* resolution records (newest first)."""
        records = self._read_days(30)
        resolutions = [r for r in records if r.get("type") == "resolution"]
        resolutions.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return resolutions[:n]

    def get_accuracy_stats(self, days: int = 7) -> dict:
        """Compute win rate, calibration, and edge accuracy over *days*.

        Returns::

            {
                "total_trades": int,
                "wins": int,
                "losses": int,
                "win_rate": float,
                "total_pnl": float,
                "avg_predicted_edge": float,
                "avg_realized_pnl_pct": float,
                "by_strategy": { strategy_name: { same fields } },
                "calibration": [ { "bin": "0.5-0.6", "predicted": float,
                                   "actual": float, "count": int } ],
            }
        """
        records = self._read_days(days)
        resolutions = [r for r in records if r.get("type") == "resolution"]

        if not resolutions:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_predicted_edge": 0.0,
                "avg_realized_pnl_pct": 0.0,
                "by_strategy": {},
                "calibration": [],
            }

        # Overall stats
        overall = self._aggregate(resolutions)

        # Per-strategy
        by_strategy: dict[str, list[dict]] = {}
        for r in resolutions:
            strat = r.get("strategy", "unknown")
            by_strategy.setdefault(strat, []).append(r)

        strategy_stats = {
            name: self._aggregate(recs) for name, recs in by_strategy.items()
        }

        # Calibration bins (0.0-0.1, 0.1-0.2, ... 0.9-1.0)
        calibration = self._calibration_bins(resolutions)

        return {
            **overall,
            "by_strategy": strategy_stats,
            "calibration": calibration,
        }

    @staticmethod
    def _aggregate(resolutions: list[dict]) -> dict:
        wins = sum(1 for r in resolutions if r.get("outcome") == "won")
        losses = sum(1 for r in resolutions if r.get("outcome") == "lost")
        total = wins + losses
        total_pnl = sum(r.get("pnl", 0) for r in resolutions)
        edges = [r.get("predicted_edge", 0) for r in resolutions]
        avg_edge = sum(edges) / len(edges) if edges else 0

        pnl_pcts = []
        for r in resolutions:
            size = r.get("size_usdc", 0)
            if size > 0:
                pnl_pcts.append(r.get("pnl", 0) / size)
        avg_pnl_pct = sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total if total else 0.0,
            "total_pnl": round(total_pnl, 4),
            "avg_predicted_edge": round(avg_edge, 6),
            "avg_realized_pnl_pct": round(avg_pnl_pct, 6),
        }

    @staticmethod
    def _calibration_bins(resolutions: list[dict]) -> list[dict]:
        bins: dict[str, list[dict]] = {}
        for r in resolutions:
            prob = r.get("predicted_prob", 0.5)
            # Bucket into 0.1-wide bins
            lo = int(prob * 10) / 10.0
            lo = max(0.0, min(0.9, lo))
            label = f"{lo:.1f}-{lo + 0.1:.1f}"
            bins.setdefault(label, []).append(r)

        result = []
        for label in sorted(bins.keys()):
            recs = bins[label]
            avg_pred = sum(r.get("predicted_prob", 0) for r in recs) / len(recs)
            actual_wins = sum(1 for r in recs if r.get("outcome") == "won")
            actual_rate = actual_wins / len(recs)
            result.append({
                "bin": label,
                "predicted": round(avg_pred, 4),
                "actual": round(actual_rate, 4),
                "count": len(recs),
            })
        return result
