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
from ..data.binance_client import BinanceClient
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

    MIN_LIQUIDITY = 1000  # SOL slots often have lower liquidity
    MIN_EDGE = 0.001  # Near-zero — always trade, let Binance TA pick direction

    def __init__(self, config: dict):
        self.config = config
        self.crypto_prices = CryptoPrices()
        self.binance = BinanceClient()
        self._last_ta: dict | None = None  # Last technical analysis for logging
        self.position_pct = config.get("short_term", {}).get(
            "position_pct", 0.05
        )  # 5% of balance per trade

    def evaluate_slots(
        self, balance: float, coins: list[str] | None = None
    ) -> list[TradeSignal]:
        """Evaluate all upcoming 15-min slots and return signals."""
        if coins is None:
            coins = ["sol"]  # Focus exclusively on Solana

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

        # Separate slots into categories
        now = time.time()
        recent = [s for s in slots if s.start_ts + 900 <= now]

        # Target the ACTIVE slot (just started, first few minutes)
        # instead of upcoming slots. This way we wait for the previous
        # 15-min candle to close before deciding direction.
        active = [
            s for s in slots
            if s.start_ts <= now < s.start_ts + 900  # currently running
            and now - s.start_ts < 180  # only first 3 minutes (good pricing)
            and s.liquidity >= self.MIN_LIQUIDITY
        ]

        if not active:
            # Fallback: if no active slot in its first 3 min, check upcoming
            # that start within 60 seconds (about to begin, candle nearly closed)
            upcoming = [
                s for s in slots
                if s.is_upcoming
                and s.start_ts - now < 60  # starts within 1 min
                and s.liquidity >= self.MIN_LIQUIDITY
            ]
            if not upcoming:
                logger.debug(f"{coin}: no tradeable slots (waiting for candle close)")
                return None
            target = upcoming[0]
        else:
            target = active[0]

        logger.info(
            f"{coin.upper()}: targeting {target.slug} "
            f"(started {now - target.start_ts:.0f}s ago)"
        )

        # Calculate momentum from recent resolved windows
        momentum_score = self._calculate_momentum(coin, recent)

        # Wait for a clear signal — don't trade neutral/coin-flip situations
        if abs(momentum_score) < 0.03:
            logger.info(
                f"{coin}: signal too weak ({momentum_score:+.3f}), waiting for clearer entry"
            )
            return None

        # Determine direction and edge
        if momentum_score > 0:
            # Bullish momentum — bet UP
            direction = "up"
            our_prob = 0.5 + (abs(momentum_score) * 0.30)
            market_price = target.up_price
            token_id = target.up_token_id
        else:
            # Bearish momentum — bet DOWN
            direction = "down"
            our_prob = 0.5 + (abs(momentum_score) * 0.30)
            market_price = target.down_price
            token_id = target.down_token_id

        # Cap probability — don't get overconfident
        our_prob = min(0.70, our_prob)

        edge = our_prob - market_price
        # Always trade every slot — even if edge is small or slightly negative.
        # If the market is ahead of our estimate, we still trust Binance TA
        # for direction, just size down.
        if edge < -0.10:
            # Only skip if the market strongly disagrees (>10% against us)
            logger.debug(
                f"{coin}: market strongly against us ({edge:.3f}), skipping"
            )
            return None
        # Floor edge at a tiny positive for signal generation
        edge = max(0.005, edge)

        # Position sizing — fewer trades, bigger size
        # Strong signal (momentum > 0.3): 8% of balance
        # Weak signal: 4% of balance
        conviction = min(1.0, abs(momentum_score) / 0.3)
        size_pct = 0.04 + 0.04 * conviction  # 4-8% of balance
        size_usdc = balance * size_pct
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
        """Calculate momentum from Binance technical analysis.

        Primary data: Binance 1-min candles with RSI, VWAP, volume.
        Secondary: slot outcomes for market-specific confirmation.
        Fallback: CoinGecko if Binance fails.

        Returns: float in [-1, 1], positive = bullish, negative = bearish.
        """
        score = 0.0

        # 1. Binance technical analysis (primary — weight: 0.70)
        ta = None
        try:
            ta = self.binance.get_technical_analysis(coin)
        except Exception as e:
            logger.warning(f"Binance TA failed for {coin}: {e}")

        if ta:
            self._last_ta = ta

            # RSI component (weight: 0.20) — mean reversion + trend
            rsi = ta["rsi_14"]
            if rsi <= 25:
                score += 0.20   # Deeply oversold → likely bounce UP
            elif rsi <= 35:
                score += 0.10
            elif rsi >= 75:
                score -= 0.20   # Deeply overbought → likely pullback DOWN
            elif rsi >= 65:
                score -= 0.10

            # VWAP component (weight: 0.10) — institutional trend
            vwap_dist = (ta["price"] - ta["vwap"]) / ta["vwap"] * 100 if ta["vwap"] > 0 else 0
            score += max(-0.10, min(0.10, vwap_dist * 0.05))

            # 5-min trend (weight: 0.20) — immediate momentum
            score += max(-0.20, min(0.20, ta["trend_5m"] / 0.3 * 0.20))

            # 15-min trend (weight: 0.10) — confirms direction
            score += max(-0.10, min(0.10, ta["trend_15m"] / 0.5 * 0.10))

            # 1-hour trend (weight: 0.05) — broader context
            score += max(-0.05, min(0.05, ta["trend_1h"] / 1.0 * 0.05))

            # Volume confirmation (weight: 0.05) — amplify when volume agrees
            if ta["volume_ratio"] > 1.5 and abs(ta["trend_5m"]) > 0.03:
                direction = 1.0 if ta["trend_5m"] > 0 else -1.0
                score += direction * 0.05

            logger.info(
                f"{coin.upper()} Binance: ${ta['price']:.2f} "
                f"RSI={ta['rsi_14']:.0f}({ta['rsi_signal']}) "
                f"VWAP=${ta['vwap']:.2f}({ta['price_vs_vwap']}) "
                f"S=${ta['support']:.2f}/R=${ta['resistance']:.2f} "
                f"Vol={ta['volume_ratio']}x({ta['volume_trend']}) "
                f"5m={ta['trend_5m']:+.3f}% 15m={ta['trend_15m']:+.3f}% "
                f"1h={ta['trend_1h']:+.3f}% "
                f"Signal: {ta['signal_strength']}"
            )
        else:
            # Fallback to CoinGecko multi-timeframe
            try:
                coin_id_map = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}
                coin_id = coin_id_map.get(coin, coin)
                mtf = self.crypto_prices.get_multi_timeframe(coin_id)
                if mtf:
                    self._last_mtf = mtf
                    t30 = mtf["trend_30m"]["change_pct"]
                    score += max(-0.35, min(0.35, t30 / 1.0 * 0.35))
                    t4h = mtf["trend_4h"]["change_pct"]
                    score += max(-0.20, min(0.20, t4h / 3.0 * 0.20))
                    t24h = mtf["trend_24h"]["change_pct"]
                    score += max(-0.10, min(0.10, t24h / 5.0 * 0.10))
                    logger.info(f"{coin.upper()} CoinGecko fallback: 30m={t30:+.3f}% 4h={t4h:+.3f}%")
            except Exception as e:
                logger.warning(f"CoinGecko fallback also failed: {e}")

        # 2. Slot-based momentum (weight: 0.15) — market-specific confirmation
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
                    break

                if last_dir is None:
                    last_dir = dir_
                    consecutive = 1
                elif dir_ == last_dir:
                    consecutive += 1
                else:
                    break

            if consecutive >= 2:
                direction_mult = 1.0 if last_dir == "up" else -1.0
                slot_score = direction_mult * min(1.0, consecutive * 0.25)
                score += slot_score * 0.15

                # Log conflict but don't penalize — Binance real-time data
                # is more current than resolved slot history
                if ta and slot_score * score < 0:
                    logger.info(
                        f"{coin.upper()} Note: Binance vs slots disagree "
                        f"(score={score:+.3f}). Trusting Binance."
                    )

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

    MIN_EDGE = 0.10  # 10% edge minimum — only high-conviction bracket bets
    MIN_LIQUIDITY = 3000

    def __init__(self, config: dict):
        self.config = config
        self.position_pct = config.get("short_term", {}).get(
            "tweet_position_pct", 0.02
        )  # 2% of balance per bracket — conservative

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
        """Run all short-term strategies and return combined signals.

        Currently focused exclusively on SOL 15-min momentum.
        Tweet brackets are paused for new entries (existing positions
        still monitored for resolution and profit-taking).
        """
        signals = []

        logger.info("Evaluating 15-min SOL momentum...")
        try:
            crypto_signals = self.crypto.evaluate_slots(balance)
            signals.extend(crypto_signals)
            logger.info(f"SOL momentum: {len(crypto_signals)} signals")
        except Exception as e:
            logger.error(f"SOL momentum evaluation failed: {e}")

        # Tweet brackets paused — existing positions still monitored
        # in cmd_fast() resolution passes

        # Sort all by edge
        signals.sort(key=lambda s: s.edge, reverse=True)
        return signals
