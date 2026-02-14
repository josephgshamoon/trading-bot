"""Short-term trading strategies for fast-resolving Polymarket events.

Two sub-strategies:
1. CryptoMomentum  — 15-min crypto up/down using real-time price momentum
2. TweetBrackets   — Elon Musk tweet count bracket trading
"""

import logging
import math
import time
from datetime import datetime, timezone

from ..data.event_markets import (
    UpDownSlot,
    TweetEvent,
    TweetBracket,
    discover_updown_slots,
    discover_tweet_events,
)
from ..data.crypto_prices import CryptoPriceClient as CryptoPrices
from .base import TradeSignal, Signal

logger = logging.getLogger("trading_bot.strategy.short_term")


# ── 15-min Crypto Momentum ─────────────────────────────────────────


class CryptoMomentumStrategy:
    """Trade 15-min crypto up/down markets using short-term momentum.

    The idea: if crypto has been trending in one direction over the
    recent windows, bet on continuation. The default 50/50 pricing
    of upcoming windows gives us edge when momentum is strong.

    Only trades when:
    - Recent windows show clear directional trend (2+ consecutive)
    - Upcoming window is still near 50/50 (market hasn't priced in)
    - Liquidity is sufficient (>$3K)
    """

    MIN_LIQUIDITY = 1000
    MIN_EDGE = 0.02  # 2% edge minimum — aggressive for 15-min windows

    def __init__(self, config: dict):
        self.config = config
        self.crypto_prices = CryptoPrices()
        self.position_pct = config.get("short_term", {}).get(
            "position_pct", 0.05
        )  # 5% of balance per trade

    def evaluate_slots(
        self, balance: float, coins: list[str] | None = None
    ) -> list[TradeSignal]:
        """Evaluate all upcoming 15-min slots and return signals."""
        if coins is None:
            coins = ["btc", "eth", "sol"]

        signals = []

        for coin in coins:
            try:
                signal = self._evaluate_coin(coin, balance)
                if signal:
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"Error evaluating {coin}: {e}")

        return signals

    def _evaluate_coin(self, coin: str, balance: float) -> TradeSignal | None:
        """Evaluate a single coin for momentum trading."""
        # Get upcoming and recent slots
        slots = discover_updown_slots(
            coins=[coin], look_ahead_slots=3, look_back_slots=4
        )

        if not slots:
            return None

        # Separate into recent (resolved/active) and upcoming
        now = time.time()
        recent = [s for s in slots if s.start_ts + 900 <= now]
        upcoming = [s for s in slots if s.is_upcoming and s.liquidity >= self.MIN_LIQUIDITY]

        if not upcoming:
            logger.debug(f"{coin}: no upcoming slots with sufficient liquidity")
            return None

        target = upcoming[0]  # Next upcoming slot

        # Calculate momentum from recent resolved windows
        momentum_score = self._calculate_momentum(coin, recent)

        if abs(momentum_score) < 0.05:
            logger.debug(
                f"{coin}: momentum too weak ({momentum_score:.2f}), skipping"
            )
            return None

        # Determine direction and edge
        if momentum_score > 0:
            # Bullish momentum — bet UP
            direction = "up"
            our_prob = 0.5 + (momentum_score * 0.25)  # Convert to probability
            market_price = target.up_price
            token_id = target.up_token_id
        else:
            # Bearish momentum — bet DOWN
            direction = "down"
            our_prob = 0.5 + (abs(momentum_score) * 0.25)
            market_price = target.down_price
            token_id = target.down_token_id

        # Cap our estimated probability
        our_prob = min(0.72, our_prob)

        edge = our_prob - market_price
        if edge < self.MIN_EDGE:
            logger.debug(
                f"{coin}: edge too small ({edge:.3f}), "
                f"our_prob={our_prob:.3f} market={market_price:.3f}"
            )
            return None

        # Position sizing — conservative for 15-min markets
        size_usdc = balance * self.position_pct
        size_usdc = min(size_usdc, target.liquidity * 0.1)  # Max 10% of liquidity
        size_usdc = max(5.0, size_usdc)  # Minimum $5

        # Build signal
        signal = TradeSignal(
            market_id=target.condition_id,
            question=target.question,
            signal=Signal.BUY_YES,  # YES = Up, or we flip for Down
            entry_price=target.up_price if direction == "up" else (1 - target.down_price),
            confidence=min(1.0, abs(momentum_score)),
            edge=edge,
            position_size_usdc=round(size_usdc, 2),
            reason=(
                f"{coin.upper()} {direction} momentum={momentum_score:+.2f}, "
                f"P={our_prob:.3f} vs market={market_price:.3f}, "
                f"{target.start_dt.strftime('%H:%M')}-{target.end_dt.strftime('%H:%M')} UTC"
            ),
            metadata={
                "strategy": "crypto_momentum_15m",
                "coin": coin,
                "direction": direction,
                "momentum_score": momentum_score,
                "slot_start": target.start_ts,
                "token_ids": [target.up_token_id, target.down_token_id],
                "neg_risk": target.neg_risk,
                "target_token_id": token_id,
                "market_price": market_price,
            },
        )

        logger.info(
            f"{coin.upper()} momentum signal: {direction} "
            f"momentum={momentum_score:+.2f} edge={edge:.3f} "
            f"size=${size_usdc:.2f}"
        )
        return signal

    def _calculate_momentum(
        self, coin: str, recent_slots: list[UpDownSlot]
    ) -> float:
        """Calculate momentum score from recent resolved windows.

        Also incorporates real-time crypto price trend from CoinGecko.

        Returns: float in [-1, 1], positive = bullish, negative = bearish.
        """
        score = 0.0

        # 1. Slot-based momentum: check resolved window outcomes
        if recent_slots:
            recent_sorted = sorted(recent_slots, key=lambda s: s.start_ts, reverse=True)
            consecutive = 0
            last_dir = None

            for slot in recent_sorted[:4]:
                if slot.up_price > 0.7:
                    dir_ = "up"
                elif slot.up_price < 0.3:
                    dir_ = "down"
                else:
                    break  # Ambiguous

                if last_dir is None:
                    last_dir = dir_
                    consecutive = 1
                elif dir_ == last_dir:
                    consecutive += 1
                else:
                    break

            if consecutive >= 1:
                direction_mult = 1.0 if last_dir == "up" else -1.0
                score += direction_mult * min(1.0, consecutive * 0.25)

        # 2. Real-time price momentum from CoinGecko
        try:
            coin_id_map = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}
            coin_id = coin_id_map.get(coin, coin)

            vol_data = self.crypto_prices.get_volatility(coin_id, days=1)
            if vol_data:
                current = vol_data["current_price"]
                high = vol_data["period_high"]
                low = vol_data["period_low"]

                if high > low:
                    # Position within today's range (0=at low, 1=at high)
                    range_pos = (current - low) / (high - low)
                    # Convert to momentum: >0.7 = bullish, <0.3 = bearish
                    price_momentum = (range_pos - 0.5) * 2
                    score += price_momentum * 0.4  # Weight
        except Exception as e:
            logger.debug(f"CoinGecko momentum check failed: {e}")

        return max(-1.0, min(1.0, score))


# ── Elon Musk Tweet Brackets ───────────────────────────────────────


class TweetBracketStrategy:
    """Trade Elon Musk tweet count bracket markets.

    Approach:
    1. Infer current tweet count from eliminated bracket prices
    2. Project final count using historical tweet rate
    3. Build a probability distribution over brackets
    4. Buy underpriced brackets with positive expected value

    Historical Elon tweet rates (posts on X per day):
    - Low activity:  20-40/day
    - Normal:        40-60/day
    - High activity:  60-100/day
    - Tweeting storm: 100+/day
    """

    # Historical daily tweet rate distribution (mean, std)
    DAILY_RATE_MEAN = 55
    DAILY_RATE_STD = 25

    MIN_EDGE = 0.02
    MIN_LIQUIDITY = 1000

    def __init__(self, config: dict):
        self.config = config
        self.position_pct = config.get("short_term", {}).get(
            "tweet_position_pct", 0.03
        )  # 3% of balance per bracket

    def evaluate_events(self, balance: float) -> list[TradeSignal]:
        """Evaluate all active Elon tweet events and return signals."""
        events = discover_tweet_events()
        signals = []

        for event in events:
            try:
                event_signals = self._evaluate_event(event, balance)
                signals.extend(event_signals)
            except Exception as e:
                logger.warning(f"Error evaluating tweet event: {e}")

        return signals

    def _evaluate_event(
        self, event: TweetEvent, balance: float
    ) -> list[TradeSignal]:
        """Evaluate a single tweet count event."""
        if event.days_until_end <= 0:
            return []

        brackets = event.brackets
        if not brackets:
            return []

        # Step 1: Infer current count from eliminated brackets
        current_count = self._infer_current_count(brackets)

        # Step 2: Estimate days elapsed and remaining
        days_total = self._estimate_total_days(event.title)
        days_remaining = event.days_until_end
        days_elapsed = max(0.5, days_total - days_remaining)  # At least half a day

        # Step 3: Calculate current daily rate (capped to realistic range)
        if days_elapsed > 0 and current_count > 0:
            daily_rate = current_count / days_elapsed
            # Sanity cap: Elon rarely exceeds 150 posts/day sustained
            daily_rate = min(daily_rate, 150.0)
            daily_rate = max(daily_rate, 10.0)
        else:
            daily_rate = self.DAILY_RATE_MEAN

        # Step 4: Project final count with uncertainty
        projected_final = current_count + (daily_rate * days_remaining)
        # Std grows with sqrt of remaining time (Poisson-like)
        projected_std = max(daily_rate * 0.3 * math.sqrt(max(days_remaining, 0.1)), 10.0)

        logger.info(
            f"Tweet tracker: count≈{current_count}, rate≈{daily_rate:.0f}/day, "
            f"projected={projected_final:.0f}±{projected_std:.0f}, "
            f"{days_remaining:.1f}d remaining"
        )

        # Step 5: Calculate probability for each bracket
        signals = []
        for bracket in brackets:
            if bracket.liquidity < self.MIN_LIQUIDITY:
                continue
            if bracket.yes_price <= 0.001:
                continue  # Already eliminated
            if bracket.yes_price >= 0.95:
                continue  # Too expensive — near-zero upside

            # Probability that final count lands in this bracket
            if bracket.hi < 99999:
                p_lo = self._normal_cdf(bracket.lo - 0.5, projected_final, projected_std)
                p_hi = self._normal_cdf(bracket.hi + 0.5, projected_final, projected_std)
                model_prob = p_hi - p_lo
            else:
                # X+ bracket
                p_lo = self._normal_cdf(bracket.lo - 0.5, projected_final, projected_std)
                model_prob = 1 - p_lo

            model_prob = max(0.001, min(0.999, model_prob))
            edge = model_prob - bracket.yes_price

            if edge < self.MIN_EDGE:
                continue

            # Position sizing
            size_usdc = balance * self.position_pct
            size_usdc = min(size_usdc, bracket.liquidity * 0.05)
            size_usdc = max(5.0, size_usdc)

            hi_str = str(bracket.hi) if bracket.hi < 99999 else "+"

            signal = TradeSignal(
                market_id=bracket.condition_id,
                question=bracket.question,
                signal=Signal.BUY_YES,
                entry_price=1 - bracket.yes_price,  # We're buying YES
                confidence=min(1.0, edge / 0.10),
                edge=edge,
                position_size_usdc=round(size_usdc, 2),
                reason=(
                    f"Tweets {bracket.lo}-{hi_str}: model P={model_prob:.3f} "
                    f"vs market {bracket.yes_price:.3f}, "
                    f"count≈{current_count} rate≈{daily_rate:.0f}/day "
                    f"proj={projected_final:.0f}"
                ),
                metadata={
                    "strategy": "tweet_brackets",
                    "bracket_lo": bracket.lo,
                    "bracket_hi": bracket.hi,
                    "current_count": current_count,
                    "daily_rate": daily_rate,
                    "projected_final": projected_final,
                    "model_prob": model_prob,
                    "token_ids": [bracket.yes_token_id, bracket.no_token_id],
                    "neg_risk": bracket.neg_risk,
                    "target_token_id": bracket.yes_token_id,
                },
            )
            signals.append(signal)

        # Sort by edge, take top 3
        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals[:3]

    def _infer_current_count(self, brackets: list[TweetBracket]) -> int:
        """Infer current tweet count from eliminated bracket prices.

        Brackets with yes_price ≈ 0 have been "eliminated" — the count
        has already surpassed them.
        """
        max_eliminated = 0
        for b in brackets:
            if b.yes_price < 0.005 and b.hi < 99999:
                max_eliminated = max(max_eliminated, b.hi)
        return max_eliminated

    def _estimate_total_days(self, title: str) -> float:
        """Estimate total event duration in days from the title."""
        import re
        # Try to find two dates
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        pattern = r"(\w+)\s+(\d+)\s*-\s*(\w+)\s+(\d+)"
        match = re.search(pattern, title, re.I)
        if match:
            m1, d1, m2, d2 = match.groups()
            m1_num = months.get(m1.lower(), 1)
            m2_num = months.get(m2.lower(), m1_num)
            from datetime import date
            try:
                year = datetime.now().year
                start = date(year, m1_num, int(d1))
                end = date(year, m2_num, int(d2))
                delta = (end - start).days
                if delta > 0:
                    return float(delta)
            except Exception:
                pass
        return 7.0  # Default to weekly

    @staticmethod
    def _normal_cdf(x: float, mu: float, sigma: float) -> float:
        """Normal CDF using the error function approximation."""
        if sigma <= 0:
            return 1.0 if x >= mu else 0.0
        return 0.5 * (1 + math.erf((x - mu) / (sigma * math.sqrt(2))))


# ── Combined Short-Term Strategy ───────────────────────────────────


class ShortTermStrategy:
    """Combined strategy running both crypto momentum and tweet brackets."""

    def __init__(self, config: dict):
        self.crypto = CryptoMomentumStrategy(config)
        self.tweets = TweetBracketStrategy(config)

    def evaluate_all(self, balance: float) -> list[TradeSignal]:
        """Run all short-term strategies and return combined signals."""
        signals = []

        logger.info("Evaluating 15-min crypto momentum...")
        try:
            crypto_signals = self.crypto.evaluate_slots(balance)
            signals.extend(crypto_signals)
            logger.info(f"Crypto momentum: {len(crypto_signals)} signals")
        except Exception as e:
            logger.error(f"Crypto momentum evaluation failed: {e}")

        logger.info("Evaluating Elon tweet brackets...")
        try:
            tweet_signals = self.tweets.evaluate_events(balance)
            signals.extend(tweet_signals)
            logger.info(f"Tweet brackets: {len(tweet_signals)} signals")
        except Exception as e:
            logger.error(f"Tweet bracket evaluation failed: {e}")

        # Sort all by edge
        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals
