"""Crypto price probability model using Geometric Brownian Motion.

Given a current price, target price, time horizon, and historical
volatility, estimates the probability of a crypto asset hitting
a price target. This gives us a data-driven edge estimate to
compare against Polymarket's implied probability.

Two models:
1. Terminal probability: P(S_T > target) — price at expiry
2. Barrier probability: P(max(S_t) > target for any t in [0,T])
   — price hits target at ANY point before expiry (more relevant
   for "will X hit $Y" markets)
"""

import logging
import math
import re

import numpy as np
from scipy import stats

from .crypto_prices import CryptoPriceClient, COIN_IDS

logger = logging.getLogger("trading_bot.crypto_model")

# Regex patterns to extract price targets and coins from market questions
_PRICE_PATTERNS = [
    # "Will Bitcoin hit $150k by December 31, 2026?"
    re.compile(
        r"will\s+(\w+)\s+hit\s+\$?([\d,.]+)\s*(k|m|b)?\b",
        re.IGNORECASE,
    ),
    # "Bitcoin above $100,000 by..."
    re.compile(
        r"(\w+)\s+(?:above|over|reach|surpass|exceed)\s+\$?([\d,.]+)\s*(k|m|b)?\b",
        re.IGNORECASE,
    ),
    # "Will BTC be above $150k..."
    re.compile(
        r"will\s+(\w+)\s+be\s+(?:above|over)\s+\$?([\d,.]+)\s*(k|m|b)?\b",
        re.IGNORECASE,
    ),
    # "$1m" style - "bitcoin hit $1m"
    re.compile(
        r"(\w+)\s+hit\s+\$?([\d,.]+)\s*(k|m|b)\b",
        re.IGNORECASE,
    ),
]

_MULTIPLIERS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def _parse_price_target(question: str) -> tuple[str | None, float | None]:
    """Extract coin name and price target from a market question.

    Returns (coin_name, target_price) or (None, None) if not a price market.
    """
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(question)
        if match:
            coin = match.group(1).lower()
            price_str = match.group(2).replace(",", "")
            multiplier_key = match.group(3)

            try:
                price = float(price_str)
            except ValueError:
                continue

            if multiplier_key:
                price *= _MULTIPLIERS.get(multiplier_key.lower(), 1)

            # Validate coin is known
            if coin in COIN_IDS:
                return coin, price

    return None, None


class CryptoModel:
    """Estimate probabilities for crypto price target markets."""

    def __init__(self):
        self.price_client = CryptoPriceClient()

    def is_price_market(self, question: str) -> bool:
        """Check if a market question is about a crypto price target."""
        coin, target = _parse_price_target(question)
        return coin is not None and target is not None

    def estimate_probability(
        self,
        question: str,
        days_to_expiry: float | None = None,
    ) -> dict | None:
        """Estimate the probability of a crypto price target being hit.

        Returns dict with model output, or None if not a price market
        or data is unavailable.
        """
        coin, target = _parse_price_target(question)
        if coin is None or target is None:
            return None

        # Get volatility data
        vol_data = self.price_client.get_volatility(coin)
        if vol_data is None:
            logger.warning(f"No volatility data for {coin}")
            return None

        current_price = vol_data["current_price"]
        ann_vol = vol_data["annualized_volatility"]
        ann_drift = vol_data["annualized_drift"]

        if days_to_expiry is None or days_to_expiry <= 0:
            days_to_expiry = 365.0  # Default 1 year

        T = days_to_expiry / 365.0  # Time in years

        # ── Terminal probability: P(S_T > target) ──
        # Under GBM: ln(S_T/S_0) ~ N((mu - sigma^2/2)*T, sigma^2*T)
        # P(S_T > K) = P(Z > d) = 1 - Phi(d)
        # where d = (ln(K/S_0) - (mu - sigma^2/2)*T) / (sigma * sqrt(T))
        log_ratio = math.log(target / current_price)
        drift_adj = (ann_drift - 0.5 * ann_vol**2) * T
        vol_term = ann_vol * math.sqrt(T)

        if vol_term < 1e-10:
            # Near-zero volatility — deterministic
            terminal_prob = 1.0 if current_price >= target else 0.0
        else:
            d = (log_ratio - drift_adj) / vol_term
            terminal_prob = 1.0 - stats.norm.cdf(d)

        # ── Barrier probability: P(max S_t > target at any t in [0,T]) ──
        # For a one-sided barrier in GBM, the probability of hitting
        # the barrier at any time before T is:
        # P = Phi(-d1) + exp(2*mu*ln(K/S0)/sigma^2) * Phi(-d2)
        # where mu_adj = (drift - sigma^2/2) / sigma^2
        # This is the "first passage time" probability.
        if current_price >= target:
            barrier_prob = 1.0
        elif vol_term < 1e-10:
            barrier_prob = 0.0
        else:
            mu_adj = ann_drift - 0.5 * ann_vol**2
            d1 = (log_ratio - mu_adj * T) / vol_term
            d2 = (log_ratio + mu_adj * T) / vol_term

            # Barrier crossing probability
            exp_term = 0.0
            exponent = 2 * mu_adj * log_ratio / (ann_vol**2)
            if exponent < 500:  # Avoid overflow
                exp_term = math.exp(exponent)

            barrier_prob = stats.norm.cdf(-d1) + exp_term * stats.norm.cdf(-d2)
            barrier_prob = max(0.0, min(1.0, barrier_prob))

        # Use barrier probability for "will X hit Y" style questions
        # (they resolve YES if the price touches the target at any point)
        # Use terminal probability for "will X be above Y on date Z"
        is_barrier = any(
            kw in question.lower()
            for kw in ["hit", "reach", "surpass", "exceed"]
        )
        model_prob = barrier_prob if is_barrier else terminal_prob

        # Monte Carlo cross-check for confidence
        mc_prob = self._monte_carlo(
            current_price, target, ann_drift, ann_vol, T,
            barrier=is_barrier, n_sims=50_000,
        )

        # Blend: 60% analytical, 40% MC (MC captures fat tails better)
        blended_prob = 0.6 * model_prob + 0.4 * mc_prob

        result = {
            "coin": coin,
            "target_price": target,
            "current_price": current_price,
            "days_to_expiry": days_to_expiry,
            "annualized_volatility": ann_vol,
            "annualized_drift": ann_drift,
            "terminal_probability": round(terminal_prob, 4),
            "barrier_probability": round(barrier_prob, 4),
            "monte_carlo_probability": round(mc_prob, 4),
            "model_probability": round(blended_prob, 4),
            "model_type": "barrier" if is_barrier else "terminal",
            "period_high": vol_data["period_high"],
            "period_low": vol_data["period_low"],
            "data_points": vol_data["data_points"],
        }

        logger.info(
            f"Crypto model: {coin} ${current_price:,.0f} -> ${target:,.0f} "
            f"in {days_to_expiry:.0f}d | vol={ann_vol:.1%} drift={ann_drift:.1%} "
            f"| P={blended_prob:.3f} (analytical={model_prob:.3f} MC={mc_prob:.3f})"
        )

        return result

    @staticmethod
    def _monte_carlo(
        s0: float,
        target: float,
        mu: float,
        sigma: float,
        T: float,
        barrier: bool = True,
        n_sims: int = 50_000,
    ) -> float:
        """Monte Carlo simulation for price target probability.

        Uses daily steps with log-normal returns. Adds mild fat tails
        via occasional jumps (jump-diffusion lite) to better model
        crypto behavior.
        """
        rng = np.random.default_rng(42)
        n_days = max(1, int(T * 365))

        dt = T / n_days
        drift_dt = (mu - 0.5 * sigma**2) * dt
        vol_sqrt_dt = sigma * math.sqrt(dt)

        # Generate paths
        z = rng.standard_normal((n_sims, n_days))

        # Add jump component: ~5% chance per day of a +-3-8% jump
        # This models crypto's fat tails
        jump_mask = rng.random((n_sims, n_days)) < 0.05
        jump_size = rng.normal(0, 0.05, (n_sims, n_days))
        z = z + jump_mask * jump_size / vol_sqrt_dt

        log_returns = drift_dt + vol_sqrt_dt * z
        log_prices = np.cumsum(log_returns, axis=1)
        log_prices = np.log(s0) + log_prices

        if barrier:
            # Check if max price along path exceeds target
            max_prices = np.max(log_prices, axis=1)
            hits = max_prices > math.log(target)
        else:
            # Check terminal price only
            final_prices = log_prices[:, -1]
            hits = final_prices > math.log(target)

        return float(np.mean(hits))
