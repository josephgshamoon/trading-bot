"""Value Betting Strategy — find mispriced probabilities.

Core idea: When our estimated 'true' probability differs from the market
price by more than a minimum edge, there's value to be captured. Uses
Kelly Criterion for position sizing.
"""

import logging
from .base import BaseStrategy, Signal, TradeSignal
from ..data.indicators import MarketIndicators

logger = logging.getLogger("trading_bot.strategy.value")


class ValueBettingStrategy(BaseStrategy):
    """Buy outcomes where market probability is mispriced."""

    def __init__(self, config: dict):
        super().__init__(config)
        strat_cfg = config.get("strategy", {}).get("value_betting", {})
        self.min_edge = strat_cfg.get("min_edge", 0.05)
        self.min_volume = strat_cfg.get("min_volume_usd", 50000)
        self.min_liquidity = strat_cfg.get("min_liquidity_usd", 10000)
        self.prob_low = strat_cfg.get("prob_range_low", 0.20)
        self.prob_high = strat_cfg.get("prob_range_high", 0.80)
        self.kelly_frac = strat_cfg.get("kelly_fraction", 0.25)

        trading_cfg = config.get("trading", {})
        self.default_size = trading_cfg.get("default_position_usdc", 5.0)
        self.max_size = trading_cfg.get("max_position_usdc", 25.0)
        self.min_size = trading_cfg.get("min_position_usdc", 1.0)

    def evaluate(self, snapshot: dict, indicators: dict) -> TradeSignal | None:
        if not self.passes_filters(snapshot):
            return None

        yes_price = indicators.get("yes_price", snapshot["yes_price"])
        no_price = indicators.get("no_price", snapshot["no_price"])
        volume = indicators.get("volume", snapshot["volume"])
        liquidity = indicators.get("liquidity", snapshot["liquidity"])
        momentum = indicators.get("momentum_6", 0.0)

        if volume < self.min_volume or liquidity < self.min_liquidity:
            return None

        # Estimate true probability using our heuristic model
        estimated_prob = MarketIndicators.edge_estimate(
            yes_price, volume, liquidity, momentum
        )

        # Check for YES value: our estimate > market price by min_edge
        yes_edge = estimated_prob - yes_price
        # Check for NO value: (1 - our estimate) > no_price by min_edge
        no_edge = (1.0 - estimated_prob) - no_price

        signal = None
        edge = 0.0
        entry_price = 0.0

        if yes_edge >= self.min_edge and self.prob_low <= yes_price <= self.prob_high:
            signal = Signal.BUY_YES
            edge = yes_edge
            entry_price = yes_price
        elif no_edge >= self.min_edge and self.prob_low <= no_price <= self.prob_high:
            signal = Signal.BUY_NO
            edge = no_edge
            entry_price = no_price
        else:
            return None

        # Kelly position sizing
        if signal == Signal.BUY_YES:
            kelly_size = MarketIndicators.kelly_criterion(
                estimated_prob, yes_price, self.kelly_frac
            )
        else:
            kelly_size = MarketIndicators.kelly_criterion(
                1.0 - estimated_prob, no_price, self.kelly_frac
            )

        position_usdc = max(
            self.min_size,
            min(self.max_size, self.default_size * (1 + kelly_size * 10)),
        )

        confidence = min(1.0, edge / self.min_edge * 0.5 + 0.3)

        return TradeSignal(
            signal=signal,
            market_id=snapshot["market_id"],
            question=snapshot["question"],
            confidence=confidence,
            entry_price=entry_price,
            position_size_usdc=round(position_usdc, 2),
            edge=round(edge, 4),
            reason=f"Value edge={edge:.3f}, estimated_prob={estimated_prob:.3f}",
            metadata={
                "estimated_probability": estimated_prob,
                "kelly_fraction": kelly_size,
                "strategy": "value_betting",
            },
        )
