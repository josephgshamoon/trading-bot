"""Prediction market indicators — probability-based analysis tools.

Unlike traditional TA (RSI, MACD, etc.), prediction market indicators
focus on probability mispricing, momentum of odds, volume patterns,
and market efficiency signals.
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("trading_bot.indicators")


class MarketIndicators:
    """Indicators designed for prediction market (binary outcome) analysis."""

    @staticmethod
    def price_momentum(prices: pd.Series, window: int = 6) -> pd.Series:
        """Rate of change in probability over a rolling window.

        Positive = odds moving toward YES, Negative = moving toward NO.
        """
        return prices.diff(window)

    @staticmethod
    def price_velocity(prices: pd.Series, window: int = 3) -> pd.Series:
        """Speed of price change (first derivative smoothed)."""
        return prices.diff().rolling(window=window).mean()

    @staticmethod
    def price_acceleration(prices: pd.Series, window: int = 3) -> pd.Series:
        """Acceleration of price change (second derivative)."""
        velocity = prices.diff()
        return velocity.diff().rolling(window=window).mean()

    @staticmethod
    def volatility(prices: pd.Series, window: int = 12) -> pd.Series:
        """Rolling standard deviation of price — higher = more uncertainty."""
        return prices.rolling(window=window).std()

    @staticmethod
    def mean_reversion_score(prices: pd.Series, window: int = 20) -> pd.Series:
        """Z-score of current price vs rolling mean.

        High positive = overbought (price above mean), should revert down.
        High negative = oversold (price below mean), should revert up.
        """
        mean = prices.rolling(window=window).mean()
        std = prices.rolling(window=window).std().replace(0, np.nan)
        return (prices - mean) / std

    @staticmethod
    def volume_momentum(volumes: pd.Series, window: int = 6) -> pd.Series:
        """Ratio of current volume to rolling average.

        > 1.0 means above-average volume (more interest/information).
        """
        avg = volumes.rolling(window=window).mean().replace(0, np.nan)
        return volumes / avg

    @staticmethod
    def spread_efficiency(yes_prices: pd.Series, no_prices: pd.Series) -> pd.Series:
        """How close YES + NO is to 1.00.

        Perfect market: spread = 0.
        Positive spread = overpriced (arbitrage opportunity).
        Negative spread = underpriced.
        """
        return (yes_prices + no_prices) - 1.0

    @staticmethod
    def kelly_criterion(
        probability: float, odds_price: float, fraction: float = 0.25
    ) -> float:
        """Kelly Criterion for position sizing.

        Args:
            probability: Our estimated true probability of outcome.
            odds_price: Current market price (cost to buy YES).
            fraction: Fraction of full Kelly to use (0.25 = quarter Kelly).

        Returns:
            Recommended fraction of bankroll to bet (0.0 if negative edge).
        """
        if odds_price <= 0 or odds_price >= 1 or probability <= 0 or probability >= 1:
            return 0.0

        # Payout is 1.0/odds_price for each dollar risked
        # Net profit if win: (1.0 - odds_price) / odds_price
        b = (1.0 - odds_price) / odds_price
        q = 1.0 - probability

        kelly = (probability * b - q) / b

        if kelly <= 0:
            return 0.0

        return kelly * fraction

    @staticmethod
    def edge_estimate(
        yes_price: float,
        volume: float,
        liquidity: float,
        momentum: float = 0.0,
    ) -> float:
        """Estimate the probability edge based on market characteristics.

        This is a heuristic model that adjusts the market's implied probability
        based on volume, liquidity, and momentum signals. It returns an
        estimated 'true' probability.

        Core idea: Low-volume, low-liquidity markets are more likely to be
        mispriced. Strong momentum suggests the market is correcting.
        """
        base_prob = yes_price

        # Volume discount: low volume markets have wider confidence intervals
        # High volume = more efficient = less likely to find edge
        vol_factor = 1.0
        if volume < 100_000:
            vol_factor = 0.95  # Markets with < $100k volume may be inefficient
        if volume < 50_000:
            vol_factor = 0.90

        # Liquidity penalty: thin markets have more noise
        liq_factor = 1.0
        if liquidity < 20_000:
            liq_factor = 0.97
        if liquidity < 10_000:
            liq_factor = 0.93

        # Momentum adjustment: if price is moving strongly in one direction,
        # the "true" probability may be further in that direction
        momentum_adj = momentum * 0.3  # Dampen momentum signal

        adjusted = base_prob * vol_factor * liq_factor + momentum_adj

        # Clamp to valid probability range
        return max(0.01, min(0.99, adjusted))

    @staticmethod
    def compute_all(snapshot: dict, price_history: pd.DataFrame | None = None) -> dict:
        """Compute all available indicators for a market snapshot.

        Returns a dict of indicator values to be used by strategies.
        """
        result = {
            "yes_price": snapshot["yes_price"],
            "no_price": snapshot["no_price"],
            "spread": snapshot["spread"],
            "volume": snapshot["volume"],
            "liquidity": snapshot["liquidity"],
        }

        if price_history is not None and len(price_history) >= 6:
            prices = price_history["price"]
            result["momentum_6"] = float(
                MarketIndicators.price_momentum(prices, 6).iloc[-1]
            )
            result["velocity"] = float(
                MarketIndicators.price_velocity(prices, 3).iloc[-1]
            )
            result["volatility"] = float(
                MarketIndicators.volatility(prices, 12).iloc[-1]
            )
            if len(prices) >= 20:
                result["mean_reversion_z"] = float(
                    MarketIndicators.mean_reversion_score(prices, 20).iloc[-1]
                )
        else:
            result["momentum_6"] = 0.0
            result["velocity"] = 0.0
            result["volatility"] = 0.0
            result["mean_reversion_z"] = 0.0

        return result
