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

        Core ideas:
        - Low-liquidity markets are more likely mispriced (thin books = noise).
        - Volume/liquidity ratio signals informed vs uninformed flow.
        - Momentum suggests the market is still correcting toward fair value.
        - Spread inefficiency (YES+NO != 1.0) indicates pricing gaps.
        """
        base_prob = yes_price

        # Liquidity-adjusted confidence: thin markets have wider mispricings
        # Use a continuous scale instead of hard cutoffs
        liq_adj = 0.0
        if liquidity < 100_000:
            # Up to 8% adjustment for very thin markets
            liq_adj = 0.08 * max(0, (100_000 - liquidity)) / 100_000

        # Volume/liquidity ratio: high ratio = lots of trading vs available
        # liquidity, suggesting price discovery is active and may overshoot
        vl_ratio = volume / max(liquidity, 1)
        vl_adj = 0.0
        if vl_ratio > 10:
            # Active markets with thin books tend to overshoot
            vl_adj = min(0.04, (vl_ratio - 10) * 0.002)

        # Momentum: if price is trending, true value is likely further ahead
        momentum_adj = momentum * 0.5

        # Direction of adjustment depends on price level:
        # - Prices near extremes (0.1 or 0.9) more likely to be mispriced
        #   toward the center (mean reversion at extremes)
        # - Prices near 0.5 more likely to be pushed by momentum
        extreme_factor = abs(base_prob - 0.5) * 2  # 0 at center, 1 at extremes
        mean_reversion = extreme_factor * 0.03  # Pull toward 0.5

        if base_prob > 0.5:
            adjusted = base_prob - liq_adj - mean_reversion + momentum_adj - vl_adj
        else:
            adjusted = base_prob + liq_adj + mean_reversion + momentum_adj + vl_adj

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
