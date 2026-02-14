"""LLM-powered market intelligence analyzer.

Uses Claude or OpenAI to assess how breaking news impacts
prediction market probabilities. This is the core information
edge — turning news into actionable probability shifts.

Supports:
- Anthropic Claude API (preferred)
- OpenAI API (fallback)
- Keyword heuristic (free, no API needed)
"""

import json
import logging
import os
import re
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

logger = logging.getLogger("trading_bot.intelligence")


class MarketAnalyzer:
    """Analyze news impact on prediction market probabilities using LLMs."""

    def __init__(self, config: dict):
        self.config = config
        intel_cfg = config.get("intelligence", {})

        self.anthropic_key = os.environ.get(
            "ANTHROPIC_API_KEY", intel_cfg.get("anthropic_api_key", "")
        )
        self.openai_key = os.environ.get(
            "OPENAI_API_KEY", intel_cfg.get("openai_api_key", "")
        )

        self.model = intel_cfg.get("model", "claude-haiku-4-5-20251001")
        self.openai_model = intel_cfg.get("openai_model", "gpt-4o-mini")
        self.max_tokens = intel_cfg.get("max_tokens", 300)
        self.temperature = intel_cfg.get("temperature", 0.1)

        # Rate limiting
        self._last_call = 0.0
        self._min_interval = intel_cfg.get("min_interval_seconds", 1.0)

        # Determine available backend
        if self.anthropic_key:
            self.backend = "anthropic"
        elif self.openai_key:
            self.backend = "openai"
        else:
            self.backend = "heuristic"

        logger.info(f"MarketAnalyzer initialized with backend: {self.backend}")

    def _rate_limit(self):
        """Enforce minimum interval between API calls."""
        elapsed = time.time() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.time()

    # ── Anthropic Claude API ────────────────────────────────────────────

    def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic Messages API."""
        self._rate_limit()

        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                text = data.get("content", [{}])[0].get("text", "")
                return text
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            logger.error(f"Anthropic API error {e.code}: {body[:200]}")
            return ""
        except Exception as e:
            logger.error(f"Anthropic API call failed: {e}")
            return ""

    # ── OpenAI API ──────────────────────────────────────────────────────

    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI Chat Completions API."""
        self._rate_limit()

        body = json.dumps({
            "model": self.openai_model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()

        req = Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.openai_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except HTTPError as e:
            body = e.read().decode() if e.fp else ""
            logger.error(f"OpenAI API error {e.code}: {body[:200]}")
            return ""
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            return ""

    # ── Core analysis ───────────────────────────────────────────────────

    def analyze_news_impact(
        self,
        market_question: str,
        current_yes_price: float,
        articles: list[dict],
    ) -> dict:
        """Analyze how news articles impact a market's probability.

        Args:
            market_question: The prediction market question.
            current_yes_price: Current YES price (0-1).
            articles: List of article dicts with 'title' and 'summary'.

        Returns:
            {
                "probability_shift": float,  # -0.3 to +0.3
                "confidence": float,         # 0.0 to 1.0
                "direction": str,            # "yes_more_likely", "no_more_likely", "neutral"
                "reasoning": str,            # Short explanation
                "news_signal_strength": float,  # 0.0 to 1.0
            }
        """
        if not articles:
            return self._neutral_result("No relevant news articles found")

        # Build article text for prompt
        article_text = ""
        for i, a in enumerate(articles[:5], 1):
            title = a.get("title", "") if isinstance(a, dict) else a.title
            summary = a.get("summary", "") if isinstance(a, dict) else a.summary
            article_text += f"{i}. {title}\n   {summary}\n\n"

        prompt = self._build_prompt(market_question, current_yes_price, article_text)

        # Call the appropriate backend
        if self.backend == "anthropic":
            raw = self._call_anthropic(prompt)
        elif self.backend == "openai":
            raw = self._call_openai(prompt)
        else:
            return self._heuristic_analysis(market_question, current_yes_price, articles)

        if not raw:
            logger.warning("LLM returned empty response, falling back to heuristic")
            return self._heuristic_analysis(market_question, current_yes_price, articles)

        return self._parse_llm_response(raw)

    def _build_prompt(
        self,
        market_question: str,
        current_yes_price: float,
        article_text: str,
    ) -> str:
        """Build the analysis prompt for the LLM."""
        return f"""You are a prediction market analyst. Analyze how recent news impacts this market.

MARKET: {market_question}
CURRENT YES PRICE: {current_yes_price:.3f} (implies {current_yes_price*100:.1f}% probability)

RECENT NEWS:
{article_text}

Based on the news above, assess the impact on this market's probability.

Respond ONLY with valid JSON (no markdown, no explanation outside the JSON):
{{
    "probability_shift": <float from -0.30 to +0.30, positive means YES more likely>,
    "confidence": <float from 0.0 to 1.0, how confident you are in this shift>,
    "direction": "<yes_more_likely|no_more_likely|neutral>",
    "reasoning": "<one sentence explaining why>",
    "news_signal_strength": <float from 0.0 to 1.0, how strong/relevant the news signal is>
}}

Rules:
- If news is unrelated or ambiguous, use probability_shift=0, direction=neutral
- Small shifts (0.01-0.05) for indirect or weak signals
- Medium shifts (0.05-0.15) for relevant but not decisive news
- Large shifts (0.15-0.30) ONLY for very strong, direct evidence
- confidence should reflect how certain you are, not the shift magnitude
- news_signal_strength reflects how relevant and impactful the news is overall"""

    def _parse_llm_response(self, raw: str) -> dict:
        """Parse the LLM's JSON response."""
        # Try to extract JSON from the response
        try:
            # Handle potential markdown wrapping
            clean = raw.strip()
            if clean.startswith("```"):
                clean = re.sub(r"^```(?:json)?\s*", "", clean)
                clean = re.sub(r"\s*```$", "", clean)

            data = json.loads(clean)

            # Validate and clamp values
            shift = max(-0.30, min(0.30, float(data.get("probability_shift", 0))))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
            strength = max(0.0, min(1.0, float(data.get("news_signal_strength", 0.5))))

            direction = data.get("direction", "neutral")
            if direction not in ("yes_more_likely", "no_more_likely", "neutral"):
                direction = "neutral"

            return {
                "probability_shift": round(shift, 4),
                "confidence": round(confidence, 3),
                "direction": direction,
                "reasoning": str(data.get("reasoning", ""))[:200],
                "news_signal_strength": round(strength, 3),
            }
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse LLM response: {e}, raw: {raw[:200]}")
            return self._neutral_result("Failed to parse LLM analysis")

    # ── Heuristic fallback ──────────────────────────────────────────────

    def _heuristic_analysis(
        self,
        market_question: str,
        current_yes_price: float,
        articles: list,
    ) -> dict:
        """Keyword-based heuristic when no LLM API is available.

        Less accurate than LLM analysis but free and always available.
        Looks for sentiment keywords and directional cues.
        """
        positive_words = {
            "confirmed", "approved", "passed", "won", "wins", "victory",
            "breakthrough", "surges", "soars", "rises", "jumps", "gains",
            "succeeds", "achieves", "agrees", "deal", "signs", "launches",
            "announces", "supports", "backs", "endorses", "leads", "ahead",
            "likely", "expected", "set to", "poised", "favored",
        }
        negative_words = {
            "denied", "rejected", "failed", "lost", "loses", "defeat",
            "crashes", "drops", "falls", "plunges", "declines", "collapses",
            "blocks", "opposes", "cancels", "delays", "threatens",
            "unlikely", "doubt", "uncertain", "struggles", "behind",
            "suspended", "withdrawn", "reverses", "vetoes",
        }

        question_lower = market_question.lower()
        pos_score = 0
        neg_score = 0
        total_relevance = 0

        for a in articles[:5]:
            title = (a.get("title", "") if isinstance(a, dict) else a.title).lower()
            summary = (a.get("summary", "") if isinstance(a, dict) else a.summary).lower()
            text = f"{title} {summary}"

            # Count sentiment words
            for w in positive_words:
                if w in text:
                    pos_score += 1
            for w in negative_words:
                if w in text:
                    neg_score += 1

            # Relevance: how much keyword overlap with market question
            q_words = set(question_lower.split())
            t_words = set(text.split())
            overlap = len(q_words & t_words)
            total_relevance += overlap

        net = pos_score - neg_score
        strength = min(1.0, total_relevance / 20.0)

        if abs(net) < 2 or strength < 0.1:
            return self._neutral_result("Heuristic: insufficient signal")

        shift = min(0.10, abs(net) * 0.015) * (1 if net > 0 else -1)
        confidence = min(0.6, strength * 0.5)

        if net > 0:
            direction = "yes_more_likely"
        elif net < 0:
            direction = "no_more_likely"
        else:
            direction = "neutral"

        return {
            "probability_shift": round(shift, 4),
            "confidence": round(confidence, 3),
            "direction": direction,
            "reasoning": f"Heuristic: pos={pos_score}, neg={neg_score}, relevance={total_relevance}",
            "news_signal_strength": round(strength, 3),
        }

    def _neutral_result(self, reason: str = "") -> dict:
        """Return a neutral/no-signal result."""
        return {
            "probability_shift": 0.0,
            "confidence": 0.0,
            "direction": "neutral",
            "reasoning": reason,
            "news_signal_strength": 0.0,
        }

    # ── Batch analysis ──────────────────────────────────────────────────

    def analyze_markets_batch(
        self,
        markets: list[dict],
        news_articles: list,
    ) -> dict[str, dict]:
        """Analyze news impact for multiple markets efficiently.

        Pre-matches articles to each market, then runs analysis only
        on markets with relevant news (saves API calls).

        Args:
            markets: List of market snapshot dicts with 'market_id' and 'question'.
            news_articles: Pre-fetched list of NewsArticle objects.

        Returns:
            Dict mapping market_id -> analysis result.
        """
        from ..data.news_feed import NewsFeed

        # Create a temporary NewsFeed for matching
        news_feed = NewsFeed(self.config)

        results = {}
        analyzed = 0

        for market in markets:
            market_id = market.get("market_id", "")
            question = market.get("question", "")
            yes_price = market.get("yes_price", 0.5)

            # Match articles to this market
            matched = news_feed.match_articles_to_market(news_articles, question)

            if not matched or matched[0].relevance_score < 0.15:
                results[market_id] = self._neutral_result("No relevant news")
                continue

            # Only analyze markets with reasonably relevant news
            article_dicts = [a.to_dict() for a in matched[:5]]
            result = self.analyze_news_impact(question, yes_price, article_dicts)
            results[market_id] = result
            analyzed += 1

            logger.debug(
                f"Analyzed {question[:50]}... -> shift={result['probability_shift']:+.3f}"
            )

        logger.info(
            f"Batch analysis complete: {analyzed}/{len(markets)} markets analyzed"
        )
        return results
