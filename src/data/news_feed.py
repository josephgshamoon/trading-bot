"""Multi-source news feed for prediction market intelligence.

Aggregates news from multiple free sources and matches headlines
to active Polymarket markets for real-time information edge.

Sources:
- NewsAPI (free tier: 100 req/day, headlines)
- RSS feeds (unlimited, major news outlets)
- GNews API (free tier: 100 req/day)
"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

logger = logging.getLogger("trading_bot.news")

# Major RSS feeds covering prediction market categories
RSS_FEEDS = {
    "politics": [
        "https://feeds.bbci.co.uk/news/politics/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
        "https://feeds.reuters.com/Reuters/PoliticsNews",
    ],
    "world": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        "https://feeds.reuters.com/Reuters/worldNews",
    ],
    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
        "https://feeds.reuters.com/reuters/businessNews",
    ],
    "crypto": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ],
    "sports": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",
        "https://feeds.bbci.co.uk/sport/rss.xml",
    ],
    "science": [
        "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    ],
}


class NewsArticle:
    """A normalized news article from any source."""

    def __init__(
        self,
        title: str,
        summary: str = "",
        source: str = "",
        url: str = "",
        published: str = "",
        category: str = "",
    ):
        self.title = title
        self.summary = summary
        self.source = source
        self.url = url
        self.published = published
        self.category = category
        self.relevance_score: float = 0.0  # Set during market matching

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "url": self.url,
            "published": self.published,
            "category": self.category,
            "relevance_score": self.relevance_score,
        }

    def __repr__(self):
        return f"NewsArticle('{self.title[:60]}...', source='{self.source}')"


class NewsFeed:
    """Aggregates news from multiple sources for market intelligence."""

    def __init__(self, config: dict):
        self.config = config
        news_cfg = config.get("news", {})
        self.newsapi_key = os.environ.get("NEWSAPI_KEY", news_cfg.get("newsapi_key", ""))
        self.gnews_key = os.environ.get("GNEWS_API_KEY", news_cfg.get("gnews_key", ""))
        self.max_age_hours = news_cfg.get("max_age_hours", 24)
        self.max_articles = news_cfg.get("max_articles_per_market", 10)

        self._cache: dict[str, dict] = {}
        self._cache_ttl = 300  # 5 min cache

        self._headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TradingBot/2.0)",
            "Accept": "application/json, application/xml, text/xml",
        }

        sources = []
        if self.newsapi_key:
            sources.append("NewsAPI")
        if self.gnews_key:
            sources.append("GNews")
        sources.append("RSS")
        logger.info(f"NewsFeed initialized with sources: {', '.join(sources)}")

    def _fetch_url(self, url: str, timeout: int = 10) -> bytes:
        """Fetch raw bytes from a URL."""
        cache_key = url
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < self._cache_ttl:
            return cached["data"]

        req = Request(url, headers=self._headers)
        try:
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                self._cache[cache_key] = {"data": data, "ts": time.time()}
                return data
        except (HTTPError, URLError, TimeoutError) as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return b""

    # ── NewsAPI ─────────────────────────────────────────────────────────

    def fetch_newsapi(self, query: str, max_results: int = 10) -> list[NewsArticle]:
        """Fetch headlines from NewsAPI matching a query."""
        if not self.newsapi_key:
            return []

        params = urlencode({
            "q": query,
            "apiKey": self.newsapi_key,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": min(max_results, 100),
        })
        url = f"https://newsapi.org/v2/everything?{params}"

        try:
            raw = self._fetch_url(url)
            if not raw:
                return []
            data = json.loads(raw)
            articles = []
            for item in data.get("articles", []):
                articles.append(NewsArticle(
                    title=item.get("title", ""),
                    summary=item.get("description", ""),
                    source=item.get("source", {}).get("name", "NewsAPI"),
                    url=item.get("url", ""),
                    published=item.get("publishedAt", ""),
                    category="newsapi",
                ))
            logger.debug(f"NewsAPI returned {len(articles)} articles for '{query}'")
            return articles
        except Exception as e:
            logger.warning(f"NewsAPI error: {e}")
            return []

    # ── GNews API ───────────────────────────────────────────────────────

    def fetch_gnews(self, query: str, max_results: int = 10) -> list[NewsArticle]:
        """Fetch news from GNews API."""
        if not self.gnews_key:
            return []

        params = urlencode({
            "q": query,
            "token": self.gnews_key,
            "lang": "en",
            "max": min(max_results, 10),
            "sortby": "publishedAt",
        })
        url = f"https://gnews.io/api/v4/search?{params}"

        try:
            raw = self._fetch_url(url)
            if not raw:
                return []
            data = json.loads(raw)
            articles = []
            for item in data.get("articles", []):
                articles.append(NewsArticle(
                    title=item.get("title", ""),
                    summary=item.get("description", ""),
                    source=item.get("source", {}).get("name", "GNews"),
                    url=item.get("url", ""),
                    published=item.get("publishedAt", ""),
                    category="gnews",
                ))
            logger.debug(f"GNews returned {len(articles)} articles for '{query}'")
            return articles
        except Exception as e:
            logger.warning(f"GNews error: {e}")
            return []

    # ── RSS Feeds ───────────────────────────────────────────────────────

    def fetch_rss(self, feed_url: str) -> list[NewsArticle]:
        """Parse an RSS feed and return articles."""
        raw = self._fetch_url(feed_url)
        if not raw:
            return []

        articles = []
        try:
            root = ET.fromstring(raw)
            # Handle both RSS 2.0 and Atom formats
            items = root.findall(".//item") or root.findall(
                ".//{http://www.w3.org/2005/Atom}entry"
            )

            for item in items[:20]:  # Cap per feed
                title = ""
                summary = ""
                link = ""
                pub_date = ""

                # RSS 2.0 format
                t = item.find("title")
                if t is not None and t.text:
                    title = t.text.strip()
                d = item.find("description")
                if d is not None and d.text:
                    summary = d.text.strip()[:500]
                l = item.find("link")
                if l is not None and l.text:
                    link = l.text.strip()
                p = item.find("pubDate")
                if p is not None and p.text:
                    pub_date = p.text.strip()

                # Atom format fallback
                if not title:
                    t = item.find("{http://www.w3.org/2005/Atom}title")
                    if t is not None and t.text:
                        title = t.text.strip()
                if not link:
                    l = item.find("{http://www.w3.org/2005/Atom}link")
                    if l is not None:
                        link = l.get("href", "")

                if title:
                    articles.append(NewsArticle(
                        title=title,
                        summary=summary,
                        source=feed_url.split("/")[2],  # domain as source
                        url=link,
                        published=pub_date,
                        category="rss",
                    ))
        except ET.ParseError as e:
            logger.warning(f"RSS parse error for {feed_url}: {e}")

        return articles

    def fetch_all_rss(self, categories: list[str] | None = None) -> list[NewsArticle]:
        """Fetch from all configured RSS feeds."""
        target_categories = categories or list(RSS_FEEDS.keys())
        all_articles = []

        for cat in target_categories:
            feeds = RSS_FEEDS.get(cat, [])
            for feed_url in feeds:
                articles = self.fetch_rss(feed_url)
                for a in articles:
                    a.category = cat
                all_articles.extend(articles)

        logger.info(f"RSS feeds returned {len(all_articles)} total articles")
        return all_articles

    # ── Market matching ─────────────────────────────────────────────────

    def extract_keywords(self, question: str) -> list[str]:
        """Extract searchable keywords from a market question.

        Turns 'Will Bitcoin reach $100k by March 2026?' into
        relevant search terms like ['Bitcoin', '$100k', 'March 2026'].
        """
        # Remove common question words and short words
        stop_words = {
            "will", "the", "a", "an", "be", "is", "are", "was", "were",
            "has", "have", "had", "do", "does", "did", "can", "could",
            "would", "should", "may", "might", "shall", "to", "of", "in",
            "on", "at", "by", "for", "with", "from", "up", "about", "into",
            "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further",
            "then", "once", "here", "there", "when", "where", "why", "how",
            "all", "each", "every", "both", "few", "more", "most", "other",
            "some", "such", "no", "nor", "not", "only", "own", "same", "so",
            "than", "too", "very", "just", "because", "as", "until", "while",
            "and", "but", "or", "if", "what", "which", "who", "whom", "this",
            "that", "these", "those", "it", "its", "his", "her", "their",
            "our", "my", "your", "any", "end", "yes", "no",
        }

        # Clean the question
        clean = re.sub(r"[?!.,;:'\"]", " ", question)
        words = clean.split()

        # Keep meaningful words (proper nouns, numbers, key terms)
        keywords = []
        for w in words:
            lower = w.lower()
            if lower in stop_words:
                continue
            if len(w) < 3:
                continue
            keywords.append(w)

        return keywords[:8]  # Cap at 8 keywords

    def match_articles_to_market(
        self,
        articles: list[NewsArticle],
        market_question: str,
    ) -> list[NewsArticle]:
        """Score and rank articles by relevance to a market question.

        Uses keyword overlap scoring to find the most relevant news
        for a given prediction market.
        """
        keywords = self.extract_keywords(market_question)
        if not keywords:
            return []

        keyword_set = {k.lower() for k in keywords}

        scored = []
        for article in articles:
            text = f"{article.title} {article.summary}".lower()
            matches = sum(1 for kw in keyword_set if kw in text)

            if matches == 0:
                continue

            # Score: fraction of keywords found, boosted by title matches
            title_lower = article.title.lower()
            title_matches = sum(1 for kw in keyword_set if kw in title_lower)

            score = (matches / len(keyword_set)) * 0.6 + (
                title_matches / len(keyword_set)
            ) * 0.4

            article.relevance_score = round(score, 3)
            scored.append(article)

        # Sort by relevance descending
        scored.sort(key=lambda a: a.relevance_score, reverse=True)
        return scored[: self.max_articles]

    # ── Main interface ──────────────────────────────────────────────────

    def get_news_for_market(self, market_question: str) -> list[NewsArticle]:
        """Get relevant news articles for a specific market question.

        This is the primary method called by strategies.
        Aggregates from all available sources and ranks by relevance.
        """
        keywords = self.extract_keywords(market_question)
        if not keywords:
            return []

        query = " ".join(keywords[:5])
        all_articles = []

        # Fetch from API sources with the market-specific query
        if self.newsapi_key:
            all_articles.extend(self.fetch_newsapi(query, max_results=10))

        if self.gnews_key:
            all_articles.extend(self.fetch_gnews(query, max_results=10))

        # Fetch from RSS (broad, then filter)
        rss_articles = self.fetch_all_rss()
        all_articles.extend(rss_articles)

        # Deduplicate by title similarity
        seen_titles = set()
        unique = []
        for a in all_articles:
            title_key = a.title.lower()[:50]
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique.append(a)

        # Match and rank
        relevant = self.match_articles_to_market(unique, market_question)

        logger.info(
            f"Found {len(relevant)} relevant articles for: "
            f"'{market_question[:50]}...'"
        )
        return relevant

    def get_bulk_news(self) -> list[NewsArticle]:
        """Fetch all available news without market-specific filtering.

        Used to pre-fetch news once, then match against multiple markets.
        More efficient when scanning many markets.
        """
        all_articles = []

        # Top headlines from APIs
        if self.newsapi_key:
            params = urlencode({
                "apiKey": self.newsapi_key,
                "language": "en",
                "pageSize": 100,
            })
            url = f"https://newsapi.org/v2/top-headlines?{params}"
            try:
                raw = self._fetch_url(url)
                if raw:
                    data = json.loads(raw)
                    for item in data.get("articles", []):
                        all_articles.append(NewsArticle(
                            title=item.get("title", ""),
                            summary=item.get("description", ""),
                            source=item.get("source", {}).get("name", "NewsAPI"),
                            url=item.get("url", ""),
                            published=item.get("publishedAt", ""),
                            category="headlines",
                        ))
            except Exception as e:
                logger.warning(f"NewsAPI headlines error: {e}")

        if self.gnews_key:
            params = urlencode({
                "token": self.gnews_key,
                "lang": "en",
                "max": 10,
            })
            url = f"https://gnews.io/api/v4/top-headlines?{params}"
            try:
                raw = self._fetch_url(url)
                if raw:
                    data = json.loads(raw)
                    for item in data.get("articles", []):
                        all_articles.append(NewsArticle(
                            title=item.get("title", ""),
                            summary=item.get("description", ""),
                            source=item.get("source", {}).get("name", "GNews"),
                            url=item.get("url", ""),
                            published=item.get("publishedAt", ""),
                            category="headlines",
                        ))
            except Exception as e:
                logger.warning(f"GNews headlines error: {e}")

        # RSS feeds
        all_articles.extend(self.fetch_all_rss())

        # Deduplicate
        seen = set()
        unique = []
        for a in all_articles:
            key = a.title.lower()[:50]
            if key and key not in seen:
                seen.add(key)
                unique.append(a)

        logger.info(f"Bulk news fetch: {len(unique)} unique articles")
        return unique
