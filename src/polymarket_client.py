"""
Polymarket API Client
Fetches market data from Polymarket's public API
"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Client for Polymarket Gamma API"""
    
    def __init__(self, api_endpoint: str = "https://gamma-api.polymarket.com"):
        self.api_endpoint = api_endpoint
        self.cache = {}
        self.cache_timeout = 30  # seconds
    
    def _fetch(self, endpoint: str) -> Dict[str, Any]:
        """Make API request with caching and stealth headers"""
        url = f"{self.api_endpoint}{endpoint}"
        cache_key = f"{endpoint}"
        
        # Check cache
        if cache_key in self.cache:
            cached_time, cached_data = self.cache[cache_key]
            if time.time() - cached_time < self.cache_timeout:
                logger.debug(f"Cache hit for {endpoint}")
                return cached_data
        
        # Fetch fresh data with stealth headers
        try:
            logger.info(f"Fetching {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://polymarket.com/",
                "Origin": "https://polymarket.com",
            }
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())
                
                # Cache it
                self.cache[cache_key] = (time.time(), data)
                return data
                
        except HTTPError as e:
            logger.error(f"HTTP Error {e.code} fetching {endpoint}: {e.read().decode()}")
            raise
        except URLError as e:
            logger.error(f"URL Error fetching {endpoint}: {e.reason}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error fetching {endpoint}: {e}")
            raise
    
    def _normalize_market(self, market: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize API fields so downstream code can use consistent names."""
        # outcomePrices may be a JSON string — parse it into a list
        raw = market.get("outcomePrices") or market.get("outcome_prices")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        market["outcome_prices"] = raw if isinstance(raw, list) else []
        return market

    def get_markets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get active markets sorted by 24h volume"""
        endpoint = f"/markets?limit={limit}&active=true&closed=false&order=volume24hr&ascending=false"
        data = self._fetch(endpoint)
        # API returns list directly for markets endpoint
        if isinstance(data, list):
            markets = data[:limit]
        else:
            markets = data.get("markets", [])[:limit]
        return [self._normalize_market(m) for m in markets]
    
    def get_market(self, market_id: str) -> Dict[str, Any]:
        """Get specific market by ID"""
        endpoint = f"/markets/{market_id}"
        return self._normalize_market(self._fetch(endpoint))
    
    def get_tickers(self) -> List[Dict[str, Any]]:
        """Get market tickers with odds"""
        endpoint = "/tickers"
        data = self._fetch(endpoint)
        return data.get("tickers", [])
    
    def get_orderbook(self, market_id: str) -> Dict[str, Any]:
        """Get order book for a market"""
        endpoint = f"/order-books/{market_id}"
        return self._fetch(endpoint)
    
    def search_markets(self, query: str) -> List[Dict[str, Any]]:
        """Search active markets by keyword (client-side filter)."""
        markets = self.get_markets(limit=200)
        query_lower = query.lower()
        return [m for m in markets if query_lower in m.get("question", "").lower()]
    
    def get_market_history(self, market_id: str, start: str = None, end: str = None) -> List[Dict[str, Any]]:
        """Get price history for a market"""
        endpoint = f"/markets/{market_id}/history"
        if start:
            endpoint += f"?start={start}"
        if end:
            endpoint += f"&end={end}" if "?" in endpoint else f"?end={end}"
        data = self._fetch(endpoint)
        return data.get("history", [])
    
    def clear_cache(self):
        """Clear the cache"""
        self.cache.clear()
        logger.info("Cache cleared")


def main():
    """Quick test of the client"""
    client = PolymarketClient()
    
    print("Testing Polymarket Client...")
    print(f"API Endpoint: {client.api_endpoint}")
    print()
    
    try:
        # Get some markets
        markets = client.get_markets(limit=5)
        print(f"✅ Found {len(markets)} markets:")
        
        for m in markets[:3]:
            print(f"  - {m.get('question', 'Unknown')[:50]}...")
            print(f"    ID: {m.get('id', 'N/A')[:8]}...")
            print(f"    Active: {m.get('active', 'N/A')}")
            print()
            
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
