"""Tests for news intelligence: entity extraction, article matching, and category detection."""

import pytest

from src.data.news_feed import NewsFeed, NewsArticle
from src.data.categorizer import MarketCategorizer


@pytest.fixture
def news_feed():
    return NewsFeed({"news": {}})


class TestEntityExtraction:
    def test_extracts_multi_word_proper_nouns(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will Ken Paxton be acquitted by the Texas Senate?"
        )
        entities = [e.lower() for e in kw["entities"]]
        assert any("ken paxton" in e for e in entities)
        assert any("texas senate" in e for e in entities)

    def test_extracts_acronyms(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will the NBA MVP be from the Western Conference?"
        )
        entities = [e.upper() for e in kw["entities"]]
        assert "NBA" in entities or "MVP" in entities

    def test_extracts_quantities(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will Bitcoin reach $100k by March 2026?"
        )
        entities = kw["entities"]
        entity_str = " ".join(entities)
        assert "$100k" in entity_str or "2026" in entity_str

    def test_extracts_single_proper_nouns(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will Trump win the Republican primary?"
        )
        entities = [e.lower() for e in kw["entities"]]
        assert any("trump" in e for e in entities)
        assert any("republican" in e for e in entities)

    def test_generic_keywords_separate_from_entities(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will Bitcoin crash below $50k in 2026?"
        )
        assert "entities" in kw
        assert "generic" in kw
        # "crash" should be in generic, not entities
        generic_lower = [g.lower() for g in kw["generic"]]
        assert "crash" in generic_lower or "below" in generic_lower

    def test_backward_compat_flat_list(self, news_feed):
        result = news_feed.extract_keywords_flat(
            "Will Bitcoin reach $100k?"
        )
        assert isinstance(result, list)
        assert len(result) > 0

    def test_caps_at_limits(self, news_feed):
        kw = news_feed.extract_keywords(
            "Will the United States Congress pass the Infrastructure Bill "
            "supported by President Biden and Vice President Harris with "
            "Democratic Party approval before the 2026 deadline?"
        )
        assert len(kw["entities"]) <= 7
        assert len(kw["generic"]) <= 5


class TestArticleMatching:
    def test_entity_match_in_title_guarantees_score(self, news_feed):
        articles = [
            NewsArticle(title="Ken Paxton faces new charges", summary="Details of the case."),
        ]
        matched = news_feed.match_articles_to_market(
            articles, "Will Ken Paxton be acquitted?"
        )
        assert len(matched) == 1
        assert matched[0].relevance_score >= 0.20

    def test_entity_match_scores_higher_than_generic(self, news_feed):
        entity_article = NewsArticle(
            title="Bitcoin surges past $90k",
            summary="The cryptocurrency market rallied today.",
        )
        generic_article = NewsArticle(
            title="Market update for the week",
            summary="Prices moved across various assets today.",
        )
        articles = [entity_article, generic_article]
        matched = news_feed.match_articles_to_market(
            articles, "Will Bitcoin reach $100k by March?"
        )
        # Entity article should score higher
        if len(matched) >= 2:
            assert matched[0].title == "Bitcoin surges past $90k"

    def test_zero_entity_less_than_2_generic_skipped(self, news_feed):
        articles = [
            NewsArticle(title="Weather forecast for tomorrow", summary="Rain expected."),
        ]
        matched = news_feed.match_articles_to_market(
            articles, "Will Bitcoin reach $100k?"
        )
        assert len(matched) == 0

    def test_body_matches_score_lower_than_title(self, news_feed):
        title_match = NewsArticle(
            title="Trump leads in polls",
            summary="Other candidates trail behind.",
        )
        body_match = NewsArticle(
            title="Election update today",
            summary="According to Trump campaign sources.",
        )
        articles = [title_match, body_match]
        matched = news_feed.match_articles_to_market(
            articles, "Will Trump win the election?"
        )
        if len(matched) >= 2:
            assert matched[0].relevance_score >= matched[1].relevance_score

    def test_multiple_entity_matches_boost_score(self, news_feed):
        one_match = NewsArticle(
            title="Bitcoin price update",
            summary="General crypto market news.",
        )
        multi_match = NewsArticle(
            title="Bitcoin reaches $95k as Ethereum follows",
            summary="Both major cryptocurrencies surging this week.",
        )
        articles = [one_match, multi_match]
        matched = news_feed.match_articles_to_market(
            articles, "Will Bitcoin or Ethereum reach new highs in 2026?"
        )
        # Multi-match article should score higher
        if len(matched) >= 2:
            assert matched[0].title == multi_match.title


class TestCategoryDetection:
    def test_politics_detection(self):
        assert MarketCategorizer.categorize(
            "Will Trump win the 2028 presidential election?"
        ) == "politics"

    def test_crypto_detection(self):
        assert MarketCategorizer.categorize(
            "Will Bitcoin reach $200k by end of 2026?"
        ) == "crypto"

    def test_sports_detection(self):
        assert MarketCategorizer.categorize(
            "Will the Lakers win the NBA championship?"
        ) == "sports"

    def test_entertainment_detection(self):
        assert MarketCategorizer.categorize(
            "Will GTA VI release before June 2026?"
        ) == "entertainment"

    def test_economics_detection(self):
        assert MarketCategorizer.categorize(
            "Will the Fed cut interest rates in Q1 2026?"
        ) == "economics"

    def test_world_detection(self):
        assert MarketCategorizer.categorize(
            "Will there be a ceasefire in Ukraine by March?"
        ) == "world"

    def test_science_detection(self):
        assert MarketCategorizer.categorize(
            "Will SpaceX successfully land on Mars?"
        ) == "science"

    def test_fallback_to_raw_category(self):
        result = MarketCategorizer.categorize(
            "Will something happen?", "Pop Culture & Entertainment"
        )
        assert result == "entertainment"

    def test_unknown_returns_other(self):
        assert MarketCategorizer.categorize(
            "Will something unusual occur?"
        ) == "other"
