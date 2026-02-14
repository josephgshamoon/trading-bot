"""Supermemory integration — persistent long-term memory for the trading bot.

Stores and retrieves context about:
- Architecture decisions, code patterns, user preferences
- Trading performance summaries (daily/weekly)
- Strategy tuning observations
- Resolved bugs and lessons learned

Used by the monitoring cron and can be queried by Claude in future sessions.
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("trading_bot.memory")

CONTAINER_TAG = "polymarket-bot"


class BotMemory:
    """Wrapper around the Supermemory SDK for the trading bot."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("SUPERMEMORY_API_KEY", "")
        self._client = None
        if not self.api_key:
            logger.warning("SUPERMEMORY_API_KEY not set — memory disabled")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            from supermemory import Supermemory
            self._client = Supermemory(api_key=self.api_key)
        return self._client

    def add(
        self,
        content: str,
        custom_id: str | None = None,
        category: str = "general",
        metadata: dict | None = None,
    ) -> dict | None:
        """Add or update a memory document.

        Uses custom_id for idempotent upserts — same ID updates
        rather than duplicates.
        """
        if not self.is_configured:
            return None

        try:
            client = self._get_client()
            kwargs = {
                "content": content,
                "container_tag": CONTAINER_TAG,
                "metadata": {
                    "category": category,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **(metadata or {}),
                },
            }
            if custom_id:
                kwargs["custom_id"] = custom_id

            result = client.add(**kwargs)
            logger.debug("Memory added: %s", getattr(result, "id", ""))
            return {"id": getattr(result, "id", ""), "status": getattr(result, "status", "")}
        except Exception as e:
            logger.error("Supermemory add error: %s", e)
            return None

    def search(
        self,
        query: str,
        limit: int = 10,
        category: str | None = None,
        search_mode: str = "hybrid",
    ) -> list[dict]:
        """Search memories. Returns list of results with memory/chunk and similarity."""
        if not self.is_configured:
            return []

        try:
            client = self._get_client()
            kwargs = {
                "q": query,
                "container_tags": [CONTAINER_TAG],
                "limit": limit,
            }
            if category:
                kwargs["filters"] = {
                    "AND": [{"key": "category", "value": category}]
                }

            response = client.search.execute(**kwargs)
            results = []
            for r in response.results:
                results.append({
                    "memory": getattr(r, "memory", None),
                    "chunk": getattr(r, "chunk", None),
                    "similarity": getattr(r, "similarity", 0),
                    "metadata": getattr(r, "metadata", {}),
                })
            return results
        except Exception as e:
            logger.error("Supermemory search error: %s", e)
            return []

    def store_performance_snapshot(self, stats: dict, journal_summary: str):
        """Store a daily performance snapshot as a memory."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        content = (
            f"Trading bot performance snapshot for {date_str}:\n\n"
            f"{journal_summary}\n\n"
            f"Raw stats: {json.dumps(stats, indent=2, default=str)}"
        )
        return self.add(
            content=content,
            custom_id=f"perf-snapshot-{date_str}",
            category="performance",
            metadata={
                "date": date_str,
                "total_trades": stats.get("total_trades", 0),
                "win_rate": stats.get("win_rate", 0),
                "total_pnl": stats.get("total_pnl", 0),
            },
        )

    def store_observation(self, observation: str, category: str = "observation"):
        """Store a trading observation or lesson learned."""
        return self.add(
            content=observation,
            category=category,
        )

    def recall(self, topic: str, limit: int = 5) -> str:
        """Search memories and return a formatted context string."""
        results = self.search(topic, limit=limit)
        if not results:
            return ""

        lines = []
        for r in results:
            text = r.get("memory") or r.get("chunk", "")
            sim = r.get("similarity", 0)
            if text:
                lines.append(f"[{sim:.2f}] {text}")
        return "\n".join(lines)
