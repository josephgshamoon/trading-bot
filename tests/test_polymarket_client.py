"""
Tests for Polymarket Client
"""

import pytest
from src.polymarket_client import PolymarketClient


class TestPolymarketClient:
    """Test cases for PolymarketClient"""
    
    @pytest.fixture
    def client(self):
        """Create a test client"""
        return PolymarketClient()
    
    def test_client_initialization(self, client):
        """Test client is properly initialized"""
        assert client.api_endpoint is not None
        assert client.cache == {}
    
    def test_fetch_markets(self, client):
        """Test fetching markets"""
        markets = client.get_markets(limit=10)
        assert isinstance(markets, list)
    
    def test_search_markets(self, client):
        """Test searching markets by keyword"""
        results = client.search_markets("bitcoin")
        assert isinstance(results, list)
    
    def test_clear_cache(self, client):
        """Test cache clearing"""
        client.cache["test"] = (1, {"data": "value"})
        client.clear_cache()
        assert client.cache == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
