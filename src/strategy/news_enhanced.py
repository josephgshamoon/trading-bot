"""News-Enhanced Value Betting Strategy.

Combines the statistical edge estimation from ValueBettingStrategy
with real-time news intelligence from the LLM analyzer. This is
the Phase 2 upgrade — information edge on top of statistical edge.

The strategy works in 3 stages:
1. Statistical edge: Same as value_betting (liquidity, momentum, mean reversion)
2. News edge: LLM analyzes relevant news for probability shifts
3. Combined edge: Weighted combination with confidence-based sizing

When news strongly supports a direction, position size scales up.
When news contradicts statistical signal, position shrinks or skips.
"""

import logging
from .base import BaseStrategy, Signal, TradeSignal
from ..data.indicators import MarketIndicators

logger = logging.getLogger("trading_bot.strategy.news_enhanced")


class NewsEnhancedStrategy(BaseStrategy):
    """Value betting enhanced with real-time news intelligence."""

    def __init__(self, config: dict):
        super().__init__(config)
        strat_cfg = config.get("strategy", {}).get("news_enhanced", {})

        # Statistical edge params (inherited from value betting)
        self.min_edge = strat_cfg.get("min_edge", 0.03)
        self.min_volume = strat_cfg.get("min_volume_usd", 10000)
        self.min_liquidity = strat_cfg.get("min_liquidity_usd", 5000)
        self.prob_low = strat_cfg.get("prob_range_low", 0.20)
        self.prob_high = strat_cfg.get("prob_range_high", 0.80)
        self.kelly_frac = strat_cfg.get("kelly_fraction", 0.25)

        # News enhancement params
        self.news_weight = strat_cfg.get("news_weight", 0.4)
        self.stat_weight = strat_cfg.get("stat_weight", 0.6)
        self.min_news_strength = strat_cfg.get("min_news_signal_strength", 0.1)
        self.news_boost_factor = strat_cfg.get("news_boost_factor", 2.0)
        self.contradiction_penalty = strat_cfg.get("contradiction_penalty", 0.5)

        # Position sizing
        trading_cfg = config.get("trading", {})
        self.default_size = trading_cfg.get("default_position_usdc", 10.0)
        self.max_size = trading_cfg.get("max_position_usdc", 50.0)
        self.min_size = trading_cfg.get("min_position_usdc", 2.0)

    def evaluate(
        self,
        snapshot: dict,
        indicators: dict,
        news_analysis: dict | None = None,
    ) -> TradeSignal | None:
        """Evaluate a market combining statistical and news signals.

        Args:
            snapshot: Market snapshot from DataFeed.
            indicators: Computed indicators from MarketIndicators.
            news_analysis: Optional news analysis from MarketAnalyzer.
                If None, falls back to pure statistical analysis.
        """
        if not self.passes_filters(snapshot):
            return None

        yes_price = indicators.get("yes_price", snapshot["yes_price"])
        no_price = indicators.get("no_price", snapshot["no_price"])
        volume = indicators.get("volume", snapshot["volume"])
        liquidity = indicators.get("liquidity", snapshot["liquidity"])
        momentum = indicators.get("momentum_6", 0.0)

        if volume < self.min_volume or liquidity < self.min_liquidity:
            return None

        # ── Stage 1: Statistical edge ───────────────────────────────────
        stat_prob = MarketIndicators.edge_estimate(
            yes_price, volume, liquidity, momentum
        )

        stat_yes_edge = stat_prob - yes_price
        stat_no_edge = (1.0 - stat_prob) - no_price

        # ── Stage 2: News edge ──────────────────────────────────────────
        news_shift = 0.0
        news_confidence = 0.0
        news_strength = 0.0
        news_direction = "neutral"
        news_reasoning = "No news data"

        if news_analysis and news_analysis.get("news_signal_strength", 0) > self.min_news_strength:
            news_shift = news_analysis.get("probability_shift", 0.0)
            news_confidence = news_analysis.get("confidence", 0.0)
            news_strength = news_analysis.get("news_signal_strength", 0.0)
            news_direction = news_analysis.get("direction", "neutral")
            news_reasoning = news_analysis.get("reasoning", "")

        # ── Stage 3: Combined probability estimate ──────────────────────
        # Blend statistical and news-based estimates
        if news_strength > self.min_news_strength and news_direction != "neutral":
            # Dynamic weighting: stronger news signal = more weight to news
            effective_news_weight = self.news_weight * news_strength * news_confidence
            effective_stat_weight = self.stat_weight

            # Normalize weights
            total_weight = effective_news_weight + effective_stat_weight
            nw = effective_news_weight / total_weight
            sw = effective_stat_weight / total_weight

            combined_prob = (stat_prob * sw) + ((yes_price + news_shift) * nw)
        else:
            combined_prob = stat_prob

        combined_prob = max(0.01, min(0.99, combined_prob))

        # Calculate combined edges
        yes_edge = combined_prob - yes_price
        no_edge = (1.0 - combined_prob) - no_price

        # ── Signal determination ────────────────────────────────────────
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

        # ── Check for news contradiction ────────────────────────────────
        # If news says YES more likely but we're buying NO (or vice versa),
        # reduce confidence
        contradicted = False
        if news_direction == "yes_more_likely" and signal == Signal.BUY_NO:
            contradicted = True
        elif news_direction == "no_more_likely" and signal == Signal.BUY_YES:
            contradicted = True

        # ── Position sizing ─────────────────────────────────────────────
        if signal == Signal.BUY_YES:
            kelly_size = MarketIndicators.kelly_criterion(
                combined_prob, yes_price, self.kelly_frac
            )
        else:
            kelly_size = MarketIndicators.kelly_criterion(
                1.0 - combined_prob, no_price, self.kelly_frac
            )

        # Base position from Kelly
        position_usdc = max(
            self.min_size,
            min(self.max_size, self.default_size * (1 + kelly_size * 10)),
        )

        # News-based position adjustment
        if news_strength > self.min_news_strength and not contradicted:
            # Boost position when news confirms direction
            news_multiplier = 1.0 + (news_strength * news_confidence * (self.news_boost_factor - 1.0))
            position_usdc *= news_multiplier
        elif contradicted:
            # Shrink position when news contradicts
            position_usdc *= self.contradiction_penalty

        position_usdc = max(self.min_size, min(self.max_size, position_usdc))

        # ── Confidence scoring ──────────────────────────────────────────
        # Base confidence from edge
        base_conf = min(1.0, edge / self.min_edge * 0.4 + 0.2)

        # News confidence boost/penalty
        if news_strength > self.min_news_strength:
            if not contradicted:
                news_conf_boost = news_confidence * news_strength * 0.3
                confidence = min(1.0, base_conf + news_conf_boost)
            else:
                confidence = base_conf * self.contradiction_penalty
        else:
            confidence = base_conf

        # Build reason string
        reason_parts = [
            f"combined_edge={edge:.3f}",
            f"stat_prob={stat_prob:.3f}",
            f"combined_prob={combined_prob:.3f}",
        ]
        if news_strength > self.min_news_strength:
            reason_parts.append(f"news={news_direction}")
            reason_parts.append(f"news_shift={news_shift:+.3f}")
            reason_parts.append(f"news_str={news_strength:.2f}")
            if contradicted:
                reason_parts.append("CONTRADICTED")
            if news_reasoning:
                reason_parts.append(news_reasoning[:80])

        return TradeSignal(
            signal=signal,
            market_id=snapshot["market_id"],
            question=snapshot["question"],
            confidence=round(confidence, 3),
            entry_price=entry_price,
            position_size_usdc=round(position_usdc, 2),
            edge=round(edge, 4),
            reason=" | ".join(reason_parts),
            metadata={
                "strategy": "news_enhanced",
                "statistical_probability": stat_prob,
                "combined_probability": combined_prob,
                "news_shift": news_shift,
                "news_confidence": news_confidence,
                "news_strength": news_strength,
                "news_direction": news_direction,
                "news_contradicted": contradicted,
                "kelly_fraction": kelly_size,
            },
        )
