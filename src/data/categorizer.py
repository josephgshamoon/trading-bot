"""Market category detection for targeted news routing and strategy tuning.

Classifies Polymarket questions into categories like politics, crypto, sports, etc.
Used by:
- News intelligence (Phase 1): Route to category-specific RSS feeds
- Edge model (Phase 2): Category-aware parameter tuning
"""

import re


# Keyword patterns for each category (checked against lowercased question text)
_CATEGORY_PATTERNS: dict[str, list[str]] = {
    "politics": [
        "president", "congress", "senate", "senator", "house of representatives",
        "republican", "democrat", "gop", "dnc", "rnc", "election", "electoral",
        "governor", "mayor", "cabinet", "impeach", "legislation", "bill pass",
        "executive order", "supreme court", "scotus", "attorney general",
        "white house", "oval office", "primary", "caucus", "ballot",
        "trump", "biden", "desantis", "newsom", "haley", "pence",
        "vance", "harris", "pelosi", "mcconnell", "schumer",
        "political party", "midterm", "inaugurat", "veto",
        "confirmation hearing", "filibuster", "gerrymander",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "solana",
        "crypto", "cryptocurrency", "blockchain", "defi",
        "airdrop", "nft", "stablecoin", "usdc", "usdt", "tether",
        "binance", "coinbase", "uniswap", "opensea",
        "altcoin", "memecoin", "dogecoin", "shiba",
        "halving", "staking", "layer 2",
        "cardano", "polkadot", "avalanche", "avax",
        "ripple", "xrp", "chainlink",
        "web3", "smart contract", "gas fee",
        "megaeth", "market cap", "token",
    ],
    "sports": [
        "nba", "nfl", "mlb", "nhl", "mls", "fifa", "uefa",
        "super bowl", "world series", "stanley cup", "world cup",
        "mvp", "playoff", "championship", "finals",
        "touchdown", "home run", "goal", "slam dunk",
        "lakers", "celtics", "yankees", "cowboys", "chiefs",
        "olympics", "medal", "athlete", "coach", "draft pick",
        "premier league", "la liga", "bundesliga", "serie a",
        "tennis", "wimbledon", "us open", "grand slam",
        "boxing", "ufc", "mma", "formula 1", "f1", "nascar",
        "golf", "pga", "masters tournament",
    ],
    "entertainment": [
        "oscar", "grammy", "emmy", "golden globe", "academy award",
        "box office", "movie", "film", "tv show", "series",
        "album", "song", "music", "concert", "tour",
        "netflix", "disney", "hbo", "streaming",
        "celebrity", "actor", "actress", "director",
        "gta vi", "gta 6", "video game", "gaming",
        "taylor swift", "beyonce", "drake", "kanye",
        "marvel", "dc", "star wars", "sequel", "prequel",
        "billboard", "spotify", "youtube", "tiktok",
        "reality tv", "bachelor", "survivor",
    ],
    "economics": [
        "gdp", "inflation", "interest rate", "fed ", "federal reserve",
        "recession", "unemployment", "jobs report", "nonfarm",
        "stock market", "s&p 500", "nasdaq", "dow jones",
        "bond yield", "treasury", "fiscal", "monetary policy",
        "trade deficit", "tariff", "sanctions", "oil price",
        "commodity", "gold price", "silver", "crude oil",
        "housing market", "mortgage rate", "consumer price",
        "cpi", "ppi", "fomc", "rate cut", "rate hike",
        "debt ceiling", "government shutdown", "stimulus",
    ],
    "science": [
        "nasa", "spacex", "rocket", "launch", "orbit",
        "mars", "moon", "asteroid", "satellite",
        "climate change", "global warming", "carbon",
        "vaccine", "fda", "drug approval", "clinical trial",
        "ai ", "artificial intelligence", "machine learning",
        "quantum", "fusion", "renewable energy", "solar",
        "pandemic", "epidemic", "virus", "who ",
        "research", "discovery", "nobel prize",
        "gene editing", "crispr", "biotech",
    ],
    "world": [
        "ukraine", "russia", "china", "taiwan", "nato",
        "middle east", "israel", "palestine", "gaza", "iran",
        "north korea", "south korea", "japan",
        "united nations", "un ", "eu ", "european union",
        "brexit", "trade war", "sanctions", "embargo",
        "coup", "civil war", "conflict", "ceasefire",
        "refugee", "migration", "border",
        "g7", "g20", "brics", "summit",
        "india", "modi", "xi jinping", "putin", "zelensky",
    ],
}


class MarketCategorizer:
    """Classify prediction market questions into categories."""

    @staticmethod
    def categorize(question: str, raw_category: str = "") -> str:
        """Categorize a market question.

        Args:
            question: The market question text.
            raw_category: Optional raw category from Polymarket (groupItemTitle).

        Returns:
            One of: politics, crypto, sports, entertainment, economics,
            science, world, other
        """
        q_lower = question.lower()

        # Strong crypto signals — if the question is fundamentally about
        # a crypto asset, classify as crypto regardless of framing
        _STRONG_CRYPTO = [
            "bitcoin", "btc", "ethereum", "eth ", "solana", "sol ",
            "crypto", "cryptocurrency", "blockchain", "defi",
            "megaeth", "altcoin", "memecoin", "xrp", "dogecoin",
        ]
        if any(kw in q_lower for kw in _STRONG_CRYPTO):
            # Exclude false positives
            false_positives = ["netherlands", "netherton", "elon and doge",
                               "doge cut", "federal spending"]
            if not any(fp in q_lower for fp in false_positives):
                return "crypto"

        # Score each category by keyword matches
        scores: dict[str, int] = {}
        for category, patterns in _CATEGORY_PATTERNS.items():
            score = 0
            for pattern in patterns:
                if pattern in q_lower:
                    # Multi-word patterns are worth more
                    score += 2 if " " in pattern else 1
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)

        # Fall back to raw Polymarket category if available
        if raw_category:
            raw_lower = raw_category.lower()
            for category in _CATEGORY_PATTERNS:
                if category in raw_lower:
                    return category
            # Common Polymarket category mappings
            if any(w in raw_lower for w in ("politic", "election", "government")):
                return "politics"
            if any(w in raw_lower for w in ("crypto", "bitcoin", "defi", "web3")):
                return "crypto"
            if any(w in raw_lower for w in ("sport", "nba", "nfl", "soccer")):
                return "sports"
            if any(w in raw_lower for w in ("pop culture", "entertainment", "celebrity")):
                return "entertainment"
            if any(w in raw_lower for w in ("business", "economy", "finance", "market")):
                return "economics"
            if any(w in raw_lower for w in ("science", "tech", "space", "health")):
                return "science"

        return "other"
