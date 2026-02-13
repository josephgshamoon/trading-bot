"""Arbitrage Strategy — exploit YES + NO pricing inefficiencies.

Core idea: In a perfect binary market, YES_price + NO_price = 1.00.
When this sum deviates significantly (after fees), there's a risk-free
profit opportunity. Buy both sides when underpriced, or identify which
side is cheaper relative to fair value.
"""

import logging
from .base import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger("trading_bot.strategy.arbitrage")


class ArbitrageStrategy(BaseStrategy):
    """Find and exploit YES/NO pricing inefficiencies."""

    def __init__(self, config: dict):
        super().__init__(config)
        strat_cfg = config.get("strategy", {}).get("arbitrage", {})
        self.min_spread = strat_cfg.get("min_spread", 0.02)
        self.min_liquidity = strat_cfg.get("min_liquidity_usd", 5000)
        self.fee_pct = strat_cfg.get("fee_pct", 2.0)

        trading_cfg = config.get("trading", {})
        self.default_size = trading_cfg.get("default_position_usdc", 5.0)
        self.max_size = trading_cfg.get("max_position_usdc", 25.0)
        self.min_size = trading_cfg.get("min_position_usdc", 1.0)

    def evaluate(self, snapshot: dict, indicators: dict) -> TradeSignal | None:
        if not self.passes_filters(snapshot):
            return None

        liquidity = indicators.get("liquidity", snapshot["liquidity"])
        if liquidity < self.min_liquidity:
            return None

        yes_price = snapshot["yes_price"]
        no_price = snapshot["no_price"]

        total = yes_price + no_price
        deviation = 1.0 - total  # positive = underpriced, negative = overpriced

        # Account for fees
        fee_cost = self.fee_pct / 100.0

        # Case 1: Market is underpriced (YES + NO < 1.0)
        # Both sides are cheap — buy the cheaper one for value
        if deviation > (self.min_spread + fee_cost):
            # Buy whichever side is cheaper relative to 0.50
            if yes_price < no_price:
                signal = Signal.BUY_YES
                entry_price = yes_price
                edge = deviation - fee_cost
                reason = f"Underpriced market: YES+NO={total:.3f}, gap={deviation:.3f}"
            else:
                signal = Signal.BUY_NO
                entry_price = no_price
                edge = deviation - fee_cost
                reason = f"Underpriced market: YES+NO={total:.3f}, gap={deviation:.3f}"

        # Case 2: One side is significantly cheaper than fair split
        # When YES is very cheap relative to NO (or vice versa)
        elif abs(yes_price - (1.0 - no_price)) > (self.min_spread + fee_cost):
            fair_yes = 1.0 - no_price
            mispricing = fair_yes - yes_price

            if mispricing > (self.min_spread + fee_cost):
                signal = Signal.BUY_YES
                entry_price = yes_price
                edge = mispricing - fee_cost
                reason = f"YES underpriced: market={yes_price:.3f}, fair={fair_yes:.3f}"
            elif mispricing < -(self.min_spread + fee_cost):
                signal = Signal.BUY_NO
                entry_price = no_price
                edge = abs(mispricing) - fee_cost
                fair_no = 1.0 - yes_price
                reason = f"NO underpriced: market={no_price:.3f}, fair={fair_no:.3f}"
            else:
                return None
        else:
            return None

        # Arbitrage positions should be conservative
        position_usdc = max(self.min_size, min(self.default_size, self.max_size * 0.5))

        confidence = min(1.0, edge / self.min_spread * 0.5 + 0.4)

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
                "yes_price": yes_price,
                "no_price": no_price,
                "total": total,
                "deviation": deviation,
                "strategy": "arbitrage",
            },
        )
