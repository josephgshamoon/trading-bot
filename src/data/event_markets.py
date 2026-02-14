"""Discover and fetch short-duration Polymarket event markets.

These markets are NOT returned by the standard Gamma API pagination.
They use slug-based discovery with predictable naming patterns:

  15-min crypto up/down:
    btc-updown-15m-{unix_ts}
    eth-updown-15m-{unix_ts}
    sol-updown-15m-{unix_ts}

  Elon Musk tweet brackets:
    elon-musk-of-tweets-{month}-{day}-{month}-{day}

Resolution:
  - Crypto 15-min: Chainlink ETH/USD or BTC/USD data stream
  - Elon tweets: xtracker.polymarket.com post counter
"""

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger("trading_bot.event_markets")

GAMMA_API = "https://gamma-api.polymarket.com"
XTRACKER_URL = "https://xtracker.polymarket.com"

# 15-min slot duration in seconds
SLOT_SECONDS = 900

# Coins supported for 15-min up/down markets
UPDOWN_COINS = {
    "btc": "btc-updown-15m",
    "eth": "eth-updown-15m",
    "sol": "sol-updown-15m",
}


def _get_json(url: str, timeout: int = 12) -> list | dict:
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── 15-min Crypto Up / Down ────────────────────────────────────────


class UpDownSlot:
    """A single 15-minute crypto up/down market window."""

    def __init__(self, coin: str, start_ts: int, event_data: dict, market_data: dict):
        self.coin = coin
        self.start_ts = start_ts
        self.end_ts = start_ts + SLOT_SECONDS
        self.slug = f"{UPDOWN_COINS[coin]}-{start_ts}"
        self.event = event_data
        self.market = market_data

        # Parse prices
        prices = market_data.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, ValueError):
                prices = []
        self.up_price = float(prices[0]) if prices else 0.5
        self.down_price = float(prices[1]) if len(prices) > 1 else 0.5

        self.liquidity = float(market_data.get("liquidity", 0) or 0)
        self.volume = float(market_data.get("volume", 0) or 0)

        # Token IDs for order placement
        clob_ids = market_data.get("clobTokenIds", "[]")
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except (json.JSONDecodeError, ValueError):
                clob_ids = []
        self.up_token_id = clob_ids[0] if clob_ids else ""
        self.down_token_id = clob_ids[1] if len(clob_ids) > 1 else ""

        self.condition_id = market_data.get("conditionId", "")
        self.market_id = market_data.get("id", "")
        self.neg_risk = event_data.get("negRisk", False)

    @property
    def start_dt(self) -> datetime:
        return datetime.fromtimestamp(self.start_ts, tz=timezone.utc)

    @property
    def end_dt(self) -> datetime:
        return datetime.fromtimestamp(self.end_ts, tz=timezone.utc)

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_ts <= now < self.end_ts

    @property
    def is_upcoming(self) -> bool:
        return time.time() < self.start_ts

    @property
    def minutes_until_start(self) -> float:
        return max(0, (self.start_ts - time.time()) / 60)

    @property
    def question(self) -> str:
        return self.market.get("question", f"{self.coin.upper()} Up or Down")

    def __repr__(self):
        return (
            f"UpDownSlot({self.coin.upper()} {self.start_dt.strftime('%H:%M')}-"
            f"{self.end_dt.strftime('%H:%M')} UTC, up={self.up_price:.3f}, "
            f"liq=${self.liquidity:,.0f})"
        )


def discover_updown_slots(
    coins: list[str] | None = None,
    look_ahead_slots: int = 8,
    look_back_slots: int = 2,
) -> list[UpDownSlot]:
    """Discover current and upcoming 15-min crypto up/down markets.

    Returns a list of UpDownSlot objects, sorted by start time.
    """
    if coins is None:
        coins = list(UPDOWN_COINS.keys())

    now_ts = int(time.time())
    base_ts = now_ts - (now_ts % SLOT_SECONDS)

    slots = []
    for coin in coins:
        prefix = UPDOWN_COINS.get(coin)
        if not prefix:
            continue

        for offset in range(-look_back_slots, look_ahead_slots + 1):
            ts = base_ts + (offset * SLOT_SECONDS)
            slug = f"{prefix}-{ts}"
            url = f"{GAMMA_API}/events?slug={slug}"
            try:
                data = _get_json(url)
                if not data:
                    continue
                ev = data[0]
                mkts = ev.get("markets", [])
                if not mkts:
                    continue
                slot = UpDownSlot(coin, ts, ev, mkts[0])
                slots.append(slot)
            except (HTTPError, URLError, IndexError, KeyError):
                continue
            except Exception as e:
                logger.debug(f"Error fetching {slug}: {e}")
                continue

    slots.sort(key=lambda s: (s.start_ts, s.coin))
    return slots


def get_next_upcoming_slot(coin: str = "btc") -> Optional[UpDownSlot]:
    """Get the next upcoming (not yet started) 15-min slot for a coin."""
    slots = discover_updown_slots(coins=[coin], look_ahead_slots=4, look_back_slots=0)
    for s in slots:
        if s.is_upcoming and s.liquidity > 0:
            return s
    return None


def get_active_slot(coin: str = "btc") -> Optional[UpDownSlot]:
    """Get the currently active 15-min slot for a coin."""
    slots = discover_updown_slots(coins=[coin], look_ahead_slots=0, look_back_slots=1)
    for s in slots:
        if s.is_active:
            return s
    return None


def get_recent_momentum(coin: str = "btc", n_slots: int = 4) -> list[UpDownSlot]:
    """Fetch recent resolved slots to gauge momentum.

    Returns most recent slots (newest first). Check up_price:
    - up_price near 0 = resolved DOWN (price dropped)
    - up_price near 1 = resolved UP (price rose)
    """
    slots = discover_updown_slots(
        coins=[coin], look_ahead_slots=0, look_back_slots=n_slots + 2
    )
    closed = [s for s in slots if not s.is_active and not s.is_upcoming]
    closed.sort(key=lambda s: s.start_ts, reverse=True)
    return closed[:n_slots]


# ── Elon Musk Tweet Markets ────────────────────────────────────────


class TweetBracket:
    """A single bracket in an Elon Musk tweet count event."""

    def __init__(self, market_data: dict, event_data: dict):
        self.market = market_data
        self.event = event_data
        self.question = market_data.get("question", "")
        self.market_id = market_data.get("id", "")
        self.condition_id = market_data.get("conditionId", "")

        # Parse bracket range from question
        match = re.search(r"(\d+)-(\d+)", self.question)
        match_plus = re.search(r"(\d+)\+", self.question)
        if match:
            self.lo = int(match.group(1))
            self.hi = int(match.group(2))
        elif match_plus:
            self.lo = int(match_plus.group(1))
            self.hi = 99999
        else:
            self.lo = 0
            self.hi = 0

        # Parse prices
        prices = market_data.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, ValueError):
                prices = []
        self.yes_price = float(prices[0]) if prices else 0
        self.no_price = float(prices[1]) if len(prices) > 1 else 0

        self.liquidity = float(market_data.get("liquidity", 0) or 0)
        self.volume = float(market_data.get("volume", 0) or 0)

        # Token IDs
        clob_ids = market_data.get("clobTokenIds", "[]")
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except (json.JSONDecodeError, ValueError):
                clob_ids = []
        self.yes_token_id = clob_ids[0] if clob_ids else ""
        self.no_token_id = clob_ids[1] if len(clob_ids) > 1 else ""

        self.neg_risk = event_data.get("negRisk", False)

    @property
    def mid_point(self) -> float:
        return (self.lo + min(self.hi, self.lo + 19)) / 2

    def __repr__(self):
        hi_str = str(self.hi) if self.hi < 99999 else "+"
        return (
            f"TweetBracket({self.lo}-{hi_str}, "
            f"yes={self.yes_price:.3f}, liq=${self.liquidity:,.0f})"
        )


class TweetEvent:
    """An Elon Musk tweet count prediction event."""

    def __init__(self, event_data: dict):
        self.event = event_data
        self.title = event_data.get("title", "")
        self.slug = event_data.get("slug", "")
        self.end_date = event_data.get("endDate", "")
        self.total_liquidity = float(event_data.get("liquidity", 0) or 0)

        self.brackets: list[TweetBracket] = []
        for m in event_data.get("markets", []):
            b = TweetBracket(m, event_data)
            if b.lo > 0 or b.hi > 0:
                self.brackets.append(b)

        self.brackets.sort(key=lambda b: b.lo)

    @property
    def days_until_end(self) -> float:
        if not self.end_date:
            return 0
        try:
            end = datetime.fromisoformat(self.end_date.replace("Z", "+00:00"))
            return max(0, (end - datetime.now(timezone.utc)).total_seconds() / 86400)
        except Exception:
            return 0

    def __repr__(self):
        return (
            f"TweetEvent({self.title[:50]}, {len(self.brackets)} brackets, "
            f"liq=${self.total_liquidity:,.0f}, ends in {self.days_until_end:.1f}d)"
        )


def discover_tweet_events() -> list[TweetEvent]:
    """Find active Elon Musk tweet prediction events.

    Tries recent date ranges to find currently active events.
    """
    now = datetime.now(timezone.utc)
    events = []
    seen_slugs = set()

    # Try various date range patterns
    for day_offset in range(-7, 1):
        start_date = now + timedelta(days=day_offset)
        for duration in [2, 3, 7]:
            end_date = start_date + timedelta(days=duration)
            start_str = start_date.strftime("%B-%-d").lower()
            end_str = end_date.strftime("%B-%-d").lower()
            slug = f"elon-musk-of-tweets-{start_str}-{end_str}"

            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            url = f"{GAMMA_API}/events?slug={slug}"
            try:
                data = _get_json(url)
                if data:
                    ev = TweetEvent(data[0])
                    if ev.brackets and ev.days_until_end > 0:
                        events.append(ev)
            except Exception:
                continue

    events.sort(key=lambda e: e.days_until_end)
    return events


def get_elon_tweet_count() -> Optional[dict]:
    """Scrape current Elon Musk tweet/post count from xtracker.

    Returns dict with 'count' and 'period' or None on failure.
    The xtracker is a Next.js app — we fetch the page and try to
    extract the count from any embedded JSON data.
    """
    try:
        req = Request(XTRACKER_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Try to find JSON data in script tags
        # Next.js apps often embed data in __NEXT_DATA__
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
        if match:
            next_data = json.loads(match.group(1))
            return {"raw_data": next_data, "source": "xtracker"}

        return {"html_length": len(html), "source": "xtracker"}
    except Exception as e:
        logger.warning(f"Failed to fetch xtracker: {e}")
        return None
