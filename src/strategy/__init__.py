from .base import BaseStrategy, Signal
from .value_betting import ValueBettingStrategy
from .momentum import MomentumStrategy
from .arbitrage import ArbitrageStrategy

STRATEGIES = {
    "value_betting": ValueBettingStrategy,
    "momentum": MomentumStrategy,
    "arbitrage": ArbitrageStrategy,
}

__all__ = [
    "BaseStrategy",
    "Signal",
    "ValueBettingStrategy",
    "MomentumStrategy",
    "ArbitrageStrategy",
    "STRATEGIES",
]
