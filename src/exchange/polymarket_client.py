"""Polymarket API client — Gamma API for data, CLOB API for orders."""

import json
import logging
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

logger = logging.getLogger("trading_bot.exchange")


class PolymarketClient:
    """Client for Polymarket Gamma (data) and CLOB (trading) APIs."""

    def __init__(self, config: dict):
        self.config = config
        exchange_cfg = config.get("exchange", {})
        self.gamma_url = exchange_cfg.get(
            "gamma_api_url", "https://gamma-api.polymarket.com"
        )
        self.clob_url = exchange_cfg.get(
            "clob_api_url", "https://clob.polymarket.com"
        )
        self._cache: dict = {}
        self._cache_ttl = 30  # seconds

        self._headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
            "Accept": "application/json",
        }

        logger.info("Polymarket client initialized")

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        """Make a GET request with caching and error handling."""
        if params:
            url = f"{url}?{urlencode(params)}"

        cache_key = url
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < self._cache_ttl:
            return cached["data"]

        req = Request(url, headers=self._headers)

        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                self._cache[cache_key] = {"data": data, "ts": time.time()}
                return data
        except HTTPError as e:
            logger.error(f"HTTP {e.code} fetching {url}: {e.reason}")
            raise
        except URLError as e:
            logger.error(f"Network error fetching {url}: {e.reason}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error from {url}: {e}")
            raise

    def clear_cache(self):
        """Clear the request cache."""
        self._cache.clear()

    # ── Gamma API (public market data) ────────────────────────────────

    def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
    ) -> list[dict]:
        """Fetch markets from Gamma API.

        Returns list of market dicts with keys like:
        id, question, slug, active, closed, volume, liquidity,
        outcomePrices, outcomes, etc.
        """
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
        }
        return self._get(f"{self.gamma_url}/markets", params)

    def get_market(self, market_id: str) -> dict:
        """Fetch a single market by condition ID."""
        return self._get(f"{self.gamma_url}/markets/{market_id}")

    def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """Search markets by keyword."""
        return self._get(
            f"{self.gamma_url}/markets",
            {"tag": query, "limit": limit},
        )

    def get_events(self, limit: int = 50) -> list[dict]:
        """Fetch events (grouped markets)."""
        return self._get(f"{self.gamma_url}/events", {"limit": limit})

    def get_market_prices(self, market: dict) -> dict:
        """Extract current YES/NO prices from a market dict.

        Returns: {
            'yes_price': float,
            'no_price': float,
            'spread': float,
            'question': str,
        }
        """
        prices_str = market.get("outcomePrices", "[]")
        if isinstance(prices_str, str):
            try:
                prices = json.loads(prices_str)
            except json.JSONDecodeError:
                prices = []
        else:
            prices = prices_str

        yes_price = float(prices[0]) if len(prices) > 0 else 0.0
        no_price = float(prices[1]) if len(prices) > 1 else 0.0

        return {
            "yes_price": yes_price,
            "no_price": no_price,
            "spread": abs(1.0 - yes_price - no_price),
            "question": market.get("question", ""),
        }

    def get_filtered_markets(self, config: dict) -> list[dict]:
        """Fetch markets that pass the configured filters.

        Applies: min volume, min liquidity, active only, probability range.
        """
        filters = config.get("filters", {})
        risk = config.get("risk", {})

        min_vol = filters.get("min_volume_usd", 10000)
        min_liq = filters.get("min_liquidity_usd", 5000)
        active_only = filters.get("active_only", True)
        min_prob = risk.get("min_entry_probability", 0.15)
        max_prob = risk.get("max_entry_probability", 0.85)

        raw_markets = self.get_markets(limit=100, active=active_only)
        filtered = []

        for m in raw_markets:
            volume = float(m.get("volume", 0) or 0)
            liquidity = float(m.get("liquidity", 0) or 0)

            if volume < min_vol or liquidity < min_liq:
                continue

            if m.get("closed", False):
                continue

            prices = self.get_market_prices(m)
            yes_p = prices["yes_price"]

            if yes_p < min_prob or yes_p > max_prob:
                continue

            m["_prices"] = prices
            filtered.append(m)

        logger.info(
            f"Filtered {len(filtered)} markets from {len(raw_markets)} total"
        )
        return filtered

    # ── Price history (from Gamma timeseries endpoint) ────────────────

    def get_price_history(
        self,
        token_id: str,
        fidelity: int = 60,
        hours_back: int = 24,
    ) -> list[dict]:
        """Fetch price history for a specific outcome token.

        Uses the CLOB prices-history endpoint.
        Returns list of {t: timestamp, p: price} dicts.
        """
        now = int(time.time())
        start = now - (hours_back * 3600)
        params = {
            "tokenID": token_id,
            "startTs": start,
            "endTs": now,
            "fidelity": fidelity,
        }
        try:
            data = self._get(f"{self.clob_url}/prices-history", params)
            # Normalize response to [{t, p}] format
            if isinstance(data, dict) and "history" in data:
                return data["history"]
            if isinstance(data, list):
                return data
            return []
        except Exception as e:
            logger.warning(f"Could not fetch price history for {token_id}: {e}")
            return []

    # ── CLOB API (order placement — requires auth) ────────────────────

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch order book for a token from the CLOB."""
        try:
            return self._get(f"{self.clob_url}/book", {"token_id": token_id})
        except Exception as e:
            logger.warning(f"Could not fetch orderbook for {token_id}: {e}")
            return {"bids": [], "asks": []}
