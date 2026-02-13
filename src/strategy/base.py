"""Base strategy interface for prediction market trading."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Signal(Enum):
    """Trading signal types for prediction markets."""
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    """A concrete trade recommendation from a strategy."""
    signal: Signal
    market_id: str
    question: str
    confidence: float  # 0.0 to 1.0
    entry_price: float  # price to pay for the outcome token
    position_size_usdc: float  # recommended position size
    edge: float  # estimated probability edge
    reason: str  # human-readable explanation
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"{self.signal.value} | {self.question[:60]}... | "
            f"price={self.entry_price:.3f} | edge={self.edge:.3f} | "
            f"conf={self.confidence:.2f} | size=${self.position_size_usdc:.2f} | "
            f"{self.reason}"
        )


class BaseStrategy(ABC):
    """Abstract base class for all prediction market strategies."""

    def __init__(self, config: dict):
        self.config = config
        self.name = self.__class__.__name__

    @abstractmethod
    def evaluate(self, snapshot: dict, indicators: dict) -> TradeSignal | None:
        """Evaluate a market and return a trade signal or None.

        Args:
            snapshot: Market snapshot from DataFeed.get_market_snapshot()
            indicators: Computed indicators from MarketIndicators.compute_all()

        Returns:
            TradeSignal if a trade opportunity is found, None otherwise.
        """

    def passes_filters(self, snapshot: dict) -> bool:
        """Check if a market passes basic filters before strategy evaluation."""
        risk = self.config.get("risk", {})
        min_prob = risk.get("min_entry_probability", 0.15)
        max_prob = risk.get("max_entry_probability", 0.85)

        yes_p = snapshot.get("yes_price", 0)
        if yes_p < min_prob or yes_p > max_prob:
            return False

        if snapshot.get("closed", False):
            return False

        return True
