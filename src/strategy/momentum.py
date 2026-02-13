"""Momentum Strategy — trade in the direction of sustained odds movement.

Core idea: When a market's probability is consistently moving in one
direction with increasing volume, information is being priced in.
Ride the wave before it settles.
"""

import logging
from .base import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger("trading_bot.strategy.momentum")


class MomentumStrategy(BaseStrategy):
    """Trade in the direction of sustained probability movement."""

    def __init__(self, config: dict):
        super().__init__(config)
        strat_cfg = config.get("strategy", {}).get("momentum", {})
        self.min_move = strat_cfg.get("min_price_move", 0.05)
        self.volume_spike = strat_cfg.get("volume_spike_factor", 2.0)
        self.confirmation = strat_cfg.get("confirmation_count", 3)
        self.min_volume = strat_cfg.get("min_volume_usd", 50000)

        trading_cfg = config.get("trading", {})
        self.default_size = trading_cfg.get("default_position_usdc", 5.0)
        self.max_size = trading_cfg.get("max_position_usdc", 25.0)
        self.min_size = trading_cfg.get("min_position_usdc", 1.0)

    def evaluate(self, snapshot: dict, indicators: dict) -> TradeSignal | None:
        if not self.passes_filters(snapshot):
            return None

        volume = indicators.get("volume", snapshot["volume"])
        if volume < self.min_volume:
            return None

        momentum = indicators.get("momentum_6", 0.0)
        velocity = indicators.get("velocity", 0.0)
        volatility = indicators.get("volatility", 0.0)
        yes_price = snapshot["yes_price"]
        no_price = snapshot["no_price"]

        # Need sufficient price movement
        if abs(momentum) < self.min_move:
            return None

        # Need velocity in the same direction as momentum (confirmation)
        if momentum > 0 and velocity <= 0:
            return None
        if momentum < 0 and velocity >= 0:
            return None

        # Skip highly volatile markets (noise, not signal)
        if volatility > 0.15:
            return None

        # Determine direction
        if momentum > 0:
            # YES probability increasing — buy YES
            signal = Signal.BUY_YES
            entry_price = yes_price
            edge = abs(momentum)
            reason = f"Upward momentum={momentum:.3f}, velocity={velocity:.4f}"
        else:
            # YES probability decreasing — buy NO
            signal = Signal.BUY_NO
            entry_price = no_price
            edge = abs(momentum)
            reason = f"Downward momentum={momentum:.3f}, velocity={velocity:.4f}"

        # Stronger momentum = larger position (capped)
        strength = min(abs(momentum) / self.min_move, 3.0)
        position_usdc = max(
            self.min_size, min(self.max_size, self.default_size * strength)
        )

        confidence = min(1.0, abs(momentum) / 0.15 * 0.6 + 0.2)

        return TradeSignal(
            signal=signal,
            market_id=snapshot["market_id"],
            question=snapshot["question"],
            confidence=confidence,
            entry_price=entry_price,
            position_size_usdc=round(position_usdc, 2),
            edge=round(edge, 4),
            reason=reason,
            metadata={
                "momentum": momentum,
                "velocity": velocity,
                "volatility": volatility,
                "strategy": "momentum",
            },
        )
