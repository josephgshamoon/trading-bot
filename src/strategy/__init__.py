from .base import BaseStrategy, Signal
from .value_betting import ValueBettingStrategy
from .momentum import MomentumStrategy
from .arbitrage import ArbitrageStrategy
from .news_enhanced import NewsEnhancedStrategy

STRATEGIES = {
    "value_betting": ValueBettingStrategy,
    "momentum": MomentumStrategy,
    "arbitrage": ArbitrageStrategy,
    "news_enhanced": NewsEnhancedStrategy,
}

__all__ = [
    "BaseStrategy",
    "Signal",
    "ValueBettingStrategy",
    "MomentumStrategy",
    "ArbitrageStrategy",
    "NewsEnhancedStrategy",
    "STRATEGIES",
]
