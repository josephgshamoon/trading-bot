"""Binance market data client — real-time candles, volume, and technical indicators.

Provides 1-minute candle data, order book depth, and computed technical
indicators (RSI, VWAP, support/resistance, volume profile) for SOL and
other crypto assets.

Public endpoints require no API key. Authenticated endpoints (higher rate
limits) use BINANCE_API_KEY from .env if available.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.request import Request, urlopen
from urllib.parse import urlencode

logger = logging.getLogger("trading_bot.binance")

BASE_URL = "https://api.binance.com/api/v3"

# Map our coin names to Binance symbols
SYMBOLS = {
    "sol": "SOLUSDT",
    "solana": "SOLUSDT",
    "btc": "BTCUSDT",
    "bitcoin": "BTCUSDT",
    "eth": "ETHUSDT",
    "ethereum": "ETHUSDT",
}


class BinanceClient:
    """Fetch real-time market data and compute technical indicators."""

    def __init__(self):
        self._api_key = os.environ.get("BINANCE_API_KEY", "")
        self._api_secret = os.environ.get("BINANCE_API_SECRET", "")
        self._cache: dict[str, dict] = {}
        self._cache_ttl = 30  # 30 seconds — much fresher than CoinGecko
        self._last_request = 0.0
        self._min_interval = 0.2  # Binance allows 1200 req/min

    def _get(self, endpoint: str, params: dict | None = None) -> dict | list:
        """Make a GET request to Binance API."""
        url = f"{BASE_URL}/{endpoint}"
        if params:
            url += "?" + urlencode(params)

        cache_key = url
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < self._cache_ttl:
            return cached["data"]

        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        headers = {"User-Agent": "TradingBot/1.0"}
        if self._api_key:
            headers["X-MBX-APIKEY"] = self._api_key

        req = Request(url, headers=headers)
        self._last_request = time.time()
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            self._cache[cache_key] = {"data": data, "ts": time.time()}
            return data

    def get_klines(
        self, coin: str, interval: str = "1m", limit: int = 100
    ) -> list[dict]:
        """Fetch candlestick data.

        Args:
            coin: Coin name (sol, btc, eth)
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles (max 1000)

        Returns list of candle dicts with: open, high, low, close, volume,
        close_time, quote_volume, trades.
        """
        symbol = SYMBOLS.get(coin.lower())
        if not symbol:
            logger.warning(f"Unknown coin for Binance: {coin}")
            return []

        raw = self._get("klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })

        candles = []
        for k in raw:
            candles.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
            })
        return candles

    def get_orderbook(self, coin: str, limit: int = 20) -> dict:
        """Fetch order book depth.

        Returns: {
            "bids": [(price, qty), ...],  # sorted highest first
            "asks": [(price, qty), ...],  # sorted lowest first
            "bid_wall": float,  # largest bid cluster price
            "ask_wall": float,  # largest ask cluster price
            "spread_pct": float,  # bid-ask spread as %
        }
        """
        symbol = SYMBOLS.get(coin.lower())
        if not symbol:
            return {}

        raw = self._get("depth", {"symbol": symbol, "limit": limit})

        bids = [(float(p), float(q)) for p, q in raw.get("bids", [])]
        asks = [(float(p), float(q)) for p, q in raw.get("asks", [])]

        bid_wall = max(bids, key=lambda x: x[1])[0] if bids else 0
        ask_wall = min(asks, key=lambda x: x[1])[0] if asks else 0
        best_bid = bids[0][0] if bids else 0
        best_ask = asks[0][0] if asks else 0
        spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid > 0 else 0

        return {
            "bids": bids,
            "asks": asks,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "spread_pct": spread_pct,
        }

    def get_ticker(self, coin: str) -> dict | None:
        """Get 24h ticker stats.

        Returns: {price, change_pct, high_24h, low_24h, volume_24h, quote_volume_24h}
        """
        symbol = SYMBOLS.get(coin.lower())
        if not symbol:
            return None

        raw = self._get("ticker/24hr", {"symbol": symbol})
        return {
            "price": float(raw.get("lastPrice", 0)),
            "change_pct": float(raw.get("priceChangePercent", 0)),
            "high_24h": float(raw.get("highPrice", 0)),
            "low_24h": float(raw.get("lowPrice", 0)),
            "volume_24h": float(raw.get("volume", 0)),
            "quote_volume_24h": float(raw.get("quoteVolume", 0)),
        }

    def get_technical_analysis(self, coin: str) -> dict | None:
        """Full technical analysis from 1-minute candles.

        Returns: {
            "price": float,
            "rsi_14": float,           # RSI (14-period) on 1m candles
            "rsi_signal": str,         # "oversold" / "overbought" / "neutral"
            "vwap": float,             # Volume-weighted average price
            "price_vs_vwap": str,      # "above" / "below"
            "support": float,          # Nearest support level
            "resistance": float,       # Nearest resistance level
            "volume_trend": str,       # "increasing" / "decreasing" / "flat"
            "volume_ratio": float,     # Current vs average volume
            "trend_1m": float,         # % change last 1 min
            "trend_5m": float,         # % change last 5 min
            "trend_15m": float,        # % change last 15 min
            "trend_1h": float,         # % change last 60 min
            "trend_4h": float,         # % change last 4 hours
            "momentum_score": float,   # Combined score [-1, 1]
            "signal_strength": str,    # "strong_buy" / "buy" / "neutral" / "sell" / "strong_sell"
        }
        """
        # Fetch 1-minute candles (last 240 = 4 hours of data)
        candles_1m = self.get_klines(coin, "1m", limit=240)
        if len(candles_1m) < 20:
            logger.warning(f"Not enough 1m candle data for {coin}: {len(candles_1m)}")
            return None

        closes = [c["close"] for c in candles_1m]
        volumes = [c["volume"] for c in candles_1m]
        highs = [c["high"] for c in candles_1m]
        lows = [c["low"] for c in candles_1m]

        current_price = closes[-1]

        # ── RSI (14-period) ────────────────────────────────────
        rsi = self._compute_rsi(closes, period=14)

        if rsi <= 30:
            rsi_signal = "oversold"
        elif rsi >= 70:
            rsi_signal = "overbought"
        else:
            rsi_signal = "neutral"

        # ── VWAP ───────────────────────────────────────────────
        vwap = self._compute_vwap(candles_1m)
        price_vs_vwap = "above" if current_price > vwap else "below"

        # ── Support / Resistance ───────────────────────────────
        support, resistance = self._find_support_resistance(
            highs, lows, closes, current_price
        )

        # ── Volume analysis ────────────────────────────────────
        avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        recent_vol = sum(volumes[-5:]) / 5
        volume_ratio = recent_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

        if volume_ratio > 1.5:
            volume_trend = "increasing"
        elif volume_ratio < 0.6:
            volume_trend = "decreasing"
        else:
            volume_trend = "flat"

        # ── Multi-timeframe trends from 1m candles ─────────────
        def _pct_change(n_candles: int) -> float:
            if len(closes) < n_candles + 1:
                return 0.0
            return ((closes[-1] - closes[-(n_candles + 1)]) / closes[-(n_candles + 1)]) * 100

        trend_1m = _pct_change(1)
        trend_5m = _pct_change(5)
        trend_15m = _pct_change(15)
        trend_1h = _pct_change(60)
        trend_4h = _pct_change(min(240, len(closes) - 1))

        # ── Combined momentum score ────────────────────────────
        score = 0.0

        # RSI component (mean reversion signal)
        if rsi <= 25:
            score += 0.3  # Strong oversold → likely bounce up
        elif rsi <= 35:
            score += 0.15
        elif rsi >= 75:
            score -= 0.3  # Strong overbought → likely pullback
        elif rsi >= 65:
            score -= 0.15

        # VWAP component (trend confirmation)
        vwap_dist = (current_price - vwap) / vwap * 100 if vwap > 0 else 0
        score += max(-0.2, min(0.2, vwap_dist * 0.1))

        # Short-term momentum (5m trend, highest weight)
        score += max(-0.25, min(0.25, trend_5m / 0.5 * 0.25))

        # Medium-term momentum (15m trend)
        score += max(-0.15, min(0.15, trend_15m / 1.0 * 0.15))

        # Volume confirmation (amplifies signal)
        if volume_ratio > 1.5 and abs(trend_5m) > 0.05:
            # High volume confirms the move
            direction = 1.0 if trend_5m > 0 else -1.0
            score += direction * 0.10

        score = max(-1.0, min(1.0, score))

        # Signal strength classification
        if score > 0.4:
            signal_strength = "strong_buy"
        elif score > 0.15:
            signal_strength = "buy"
        elif score < -0.4:
            signal_strength = "strong_sell"
        elif score < -0.15:
            signal_strength = "sell"
        else:
            signal_strength = "neutral"

        return {
            "price": current_price,
            "rsi_14": round(rsi, 1),
            "rsi_signal": rsi_signal,
            "vwap": round(vwap, 4),
            "price_vs_vwap": price_vs_vwap,
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "volume_trend": volume_trend,
            "volume_ratio": round(volume_ratio, 2),
            "trend_1m": round(trend_1m, 4),
            "trend_5m": round(trend_5m, 4),
            "trend_15m": round(trend_15m, 4),
            "trend_1h": round(trend_1h, 4),
            "trend_4h": round(trend_4h, 4),
            "momentum_score": round(score, 4),
            "signal_strength": signal_strength,
        }

    @staticmethod
    def _compute_rsi(closes: list[float], period: int = 14) -> float:
        """Compute RSI using exponential moving average of gains/losses."""
        if len(closes) < period + 1:
            return 50.0  # Neutral default

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        # Seed with simple average
        gains = [max(0, d) for d in deltas[:period]]
        losses = [max(0, -d) for d in deltas[:period]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        # EMA smoothing for remaining periods
        for d in deltas[period:]:
            gain = max(0, d)
            loss = max(0, -d)
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _compute_vwap(candles: list[dict]) -> float:
        """Compute VWAP from candle data (typical price * volume)."""
        total_pv = 0.0
        total_vol = 0.0
        for c in candles:
            typical = (c["high"] + c["low"] + c["close"]) / 3
            total_pv += typical * c["volume"]
            total_vol += c["volume"]
        return total_pv / total_vol if total_vol > 0 else 0.0

    @staticmethod
    def _find_support_resistance(
        highs: list[float],
        lows: list[float],
        closes: list[float],
        current_price: float,
    ) -> tuple[float, float]:
        """Find nearest support and resistance from recent price action.

        Uses pivot points from recent highs/lows within the last 60 candles.
        """
        window = min(60, len(highs))
        recent_highs = highs[-window:]
        recent_lows = lows[-window:]

        # Find local maxima and minima (simple peak detection)
        resistance_levels = []
        support_levels = []

        for i in range(2, len(recent_highs) - 2):
            # Local high
            if (recent_highs[i] > recent_highs[i - 1]
                    and recent_highs[i] > recent_highs[i - 2]
                    and recent_highs[i] > recent_highs[i + 1]
                    and recent_highs[i] > recent_highs[i + 2]):
                if recent_highs[i] > current_price:
                    resistance_levels.append(recent_highs[i])

            # Local low
            if (recent_lows[i] < recent_lows[i - 1]
                    and recent_lows[i] < recent_lows[i - 2]
                    and recent_lows[i] < recent_lows[i + 1]
                    and recent_lows[i] < recent_lows[i + 2]):
                if recent_lows[i] < current_price:
                    support_levels.append(recent_lows[i])

        # Nearest support (highest support below price)
        support = max(support_levels) if support_levels else min(recent_lows)
        # Nearest resistance (lowest resistance above price)
        resistance = min(resistance_levels) if resistance_levels else max(recent_highs)

        return support, resistance
