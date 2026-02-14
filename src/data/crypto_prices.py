"""Live crypto price data from CoinGecko API.

Provides current prices, historical data, and volatility metrics
for major crypto assets. Used by the crypto probability model to
form independent price-target estimates.

No API key required for basic endpoints (rate limit: ~10-30 req/min).
"""

import json
import logging
import math
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np

logger = logging.getLogger("trading_bot.crypto_prices")

# Map common names/tickers to CoinGecko IDs
COIN_IDS = {
    "bitcoin": "bitcoin",
    "btc": "bitcoin",
    "ethereum": "ethereum",
    "eth": "ethereum",
    "solana": "solana",
    "sol": "solana",
    "xrp": "ripple",
    "ripple": "ripple",
    "dogecoin": "dogecoin",
    "doge": "dogecoin",
    "cardano": "cardano",
    "ada": "cardano",
    "avalanche": "avalanche-2",
    "avax": "avalanche-2",
    "chainlink": "chainlink",
    "link": "chainlink",
    "polkadot": "polkadot",
    "dot": "polkadot",
    "polygon": "matic-network",
    "matic": "matic-network",
    "megaeth": None,  # Not listed on CoinGecko yet
}

BASE_URL = "https://api.coingecko.com/api/v3"


class CryptoPriceClient:
    """Fetch live and historical crypto prices from CoinGecko."""

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._cache_ttl = 300  # 5 minutes
        self._last_request = 0.0
        self._min_interval = 1.5  # Rate limit: wait between requests

    def _get(self, url: str) -> dict | list:
        """Rate-limited GET request."""
        # Check cache
        cached = self._cache.get(url)
        if cached and (time.time() - cached["ts"]) < self._cache_ttl:
            return cached["data"]

        # Rate limit
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        req = Request(url, headers={
            "User-Agent": "TradingBot/1.0",
            "Accept": "application/json",
        })

        try:
            self._last_request = time.time()
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                self._cache[url] = {"data": data, "ts": time.time()}
                return data
        except HTTPError as e:
            logger.error(f"CoinGecko HTTP {e.code}: {e.reason} for {url}")
            raise

    def get_price(self, coin: str) -> dict | None:
        """Get current price and 24h change for a coin.

        Returns: {"price": float, "market_cap": float, "change_24h": float}
        """
        coin_id = COIN_IDS.get(coin.lower(), coin.lower())
        if coin_id is None:
            return None

        url = (
            f"{BASE_URL}/simple/price?ids={coin_id}"
            f"&vs_currencies=usd&include_24hr_change=true"
            f"&include_market_cap=true"
        )
        data = self._get(url)
        info = data.get(coin_id)
        if not info:
            return None

        return {
            "price": info.get("usd", 0),
            "market_cap": info.get("usd_market_cap", 0),
            "change_24h": info.get("usd_24h_change", 0),
        }

    def get_prices_batch(self, coins: list[str]) -> dict[str, dict]:
        """Get prices for multiple coins in a single request."""
        coin_ids = []
        id_to_name = {}
        for c in coins:
            cid = COIN_IDS.get(c.lower(), c.lower())
            if cid is not None:
                coin_ids.append(cid)
                id_to_name[cid] = c.lower()

        if not coin_ids:
            return {}

        url = (
            f"{BASE_URL}/simple/price?ids={','.join(coin_ids)}"
            f"&vs_currencies=usd&include_24hr_change=true"
            f"&include_market_cap=true"
        )
        data = self._get(url)

        result = {}
        for cid, info in data.items():
            name = id_to_name.get(cid, cid)
            result[name] = {
                "price": info.get("usd", 0),
                "market_cap": info.get("usd_market_cap", 0),
                "change_24h": info.get("usd_24h_change", 0),
            }
        return result

    def get_historical_prices(
        self, coin: str, days: int = 365
    ) -> list[tuple[float, float]]:
        """Get daily historical prices.

        Returns list of (timestamp_ms, price_usd) tuples.
        """
        coin_id = COIN_IDS.get(coin.lower(), coin.lower())
        if coin_id is None:
            return []

        url = (
            f"{BASE_URL}/coins/{coin_id}/market_chart"
            f"?vs_currency=usd&days={days}&interval=daily"
        )
        data = self._get(url)
        return data.get("prices", [])

    def get_recent_trend(self, coin: str, minutes: int = 30) -> dict | None:
        """Get short-term price trend over the last N minutes.

        Uses CoinGecko's 1-day endpoint (5-min granularity) and
        calculates the price change over the recent window.

        Returns: {
            "change_pct": float,   # % change over the window
            "current": float,      # latest price
            "window_start": float, # price at start of window
            "data_points": int,    # number of 5-min candles used
        }
        """
        coin_id = COIN_IDS.get(coin.lower(), coin.lower())
        if coin_id is None:
            return None

        # days=1 without interval param → 5-minute granularity (~288 points)
        url = f"{BASE_URL}/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        try:
            data = self._get(url)
        except Exception as e:
            logger.warning(f"Failed to fetch intraday data for {coin}: {e}")
            return None

        prices = data.get("prices", [])
        if len(prices) < 5:
            return None

        # Each point is ~5 min apart. Find points within our window.
        now_ms = time.time() * 1000
        cutoff_ms = now_ms - (minutes * 60 * 1000)

        recent = [(ts, p) for ts, p in prices if ts >= cutoff_ms]
        if len(recent) < 2:
            # Fallback: use last N points
            n_points = max(2, minutes // 5)
            recent = prices[-n_points:]

        start_price = recent[0][1]
        end_price = recent[-1][1]

        if start_price <= 0:
            return None

        change_pct = ((end_price - start_price) / start_price) * 100

        return {
            "change_pct": change_pct,
            "current": end_price,
            "window_start": start_price,
            "data_points": len(recent),
        }

    def get_multi_timeframe(self, coin: str) -> dict | None:
        """Get multi-timeframe price analysis for a coin.

        Uses a single CoinGecko API call (1-day chart, 5-min granularity)
        to extract 30m, 4h, and 24h trends from one dataset.

        Returns: {
            "current_price": float,
            "trend_30m": {"change_pct": float, "start_price": float, "points": int},
            "trend_4h":  {"change_pct": float, "start_price": float, "points": int},
            "trend_24h": {"change_pct": float, "start_price": float, "points": int},
            "bias": "bullish" | "bearish" | "neutral",
            "bias_strength": float,  # 0-1
        }
        """
        coin_id = COIN_IDS.get(coin.lower(), coin.lower())
        if coin_id is None:
            return None

        url = f"{BASE_URL}/coins/{coin_id}/market_chart?vs_currency=usd&days=1"
        try:
            data = self._get(url)
        except Exception as e:
            logger.warning(f"Failed to fetch multi-timeframe data for {coin}: {e}")
            return None

        prices = data.get("prices", [])
        if len(prices) < 10:
            return None

        now_ms = time.time() * 1000
        current_price = prices[-1][1]

        def _trend(minutes: int) -> dict:
            cutoff = now_ms - (minutes * 60 * 1000)
            window = [(ts, p) for ts, p in prices if ts >= cutoff]
            if len(window) < 2:
                n = max(2, minutes // 5)
                window = prices[-n:]
            start_p = window[0][1]
            if start_p <= 0:
                return {"change_pct": 0, "start_price": 0, "points": 0}
            return {
                "change_pct": ((current_price - start_p) / start_p) * 100,
                "start_price": start_p,
                "points": len(window),
            }

        t30 = _trend(30)
        t4h = _trend(240)
        t24h = _trend(1440)

        # Determine overall bias from multi-timeframe alignment
        directions = []
        for t in [t30, t4h, t24h]:
            if t["change_pct"] > 0.05:
                directions.append(1)
            elif t["change_pct"] < -0.05:
                directions.append(-1)
            else:
                directions.append(0)

        avg_dir = sum(directions) / 3
        if avg_dir > 0.3:
            bias = "bullish"
        elif avg_dir < -0.3:
            bias = "bearish"
        else:
            bias = "neutral"

        # Strength = how aligned the timeframes are (0 = mixed, 1 = all agree)
        bias_strength = abs(avg_dir)

        return {
            "current_price": current_price,
            "trend_30m": t30,
            "trend_4h": t4h,
            "trend_24h": t24h,
            "bias": bias,
            "bias_strength": bias_strength,
        }

    def get_volatility(self, coin: str, days: int = 365) -> dict | None:
        """Calculate annualized volatility and drift from historical data.

        Uses log returns for proper GBM parameter estimation.

        Returns: {
            "annualized_volatility": float,
            "daily_volatility": float,
            "annualized_drift": float,
            "daily_drift": float,
            "current_price": float,
            "period_high": float,
            "period_low": float,
            "data_points": int,
        }
        """
        prices_raw = self.get_historical_prices(coin, days)
        if len(prices_raw) < 30:
            logger.warning(f"Not enough data for {coin}: {len(prices_raw)} points")
            return None

        prices = np.array([p[1] for p in prices_raw])

        # Log returns
        log_returns = np.diff(np.log(prices))

        daily_vol = float(np.std(log_returns, ddof=1))
        daily_drift = float(np.mean(log_returns))

        # Annualize (365 calendar days)
        ann_vol = daily_vol * math.sqrt(365)
        ann_drift = daily_drift * 365

        return {
            "annualized_volatility": ann_vol,
            "daily_volatility": daily_vol,
            "annualized_drift": ann_drift,
            "daily_drift": daily_drift,
            "current_price": float(prices[-1]),
            "period_high": float(np.max(prices)),
            "period_low": float(np.min(prices)),
            "data_points": len(prices),
        }
