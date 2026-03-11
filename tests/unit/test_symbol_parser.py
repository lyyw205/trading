"""Unit tests for app.utils.symbol_parser."""

from __future__ import annotations

import pytest

from app.utils.symbol_parser import QUOTE_ASSETS, parse_symbol

pytestmark = pytest.mark.unit


class TestParseSymbol:
    """parse_symbol() happy-path tests."""

    def test_eth_usdt(self):
        assert parse_symbol("ETHUSDT") == ("ETH", "USDT")

    def test_btc_usdt(self):
        assert parse_symbol("BTCUSDT") == ("BTC", "USDT")

    def test_sol_btc(self):
        assert parse_symbol("SOLBTC") == ("SOL", "BTC")

    def test_bnb_usdt(self):
        assert parse_symbol("BNBUSDT") == ("BNB", "USDT")

    def test_eth_btc(self):
        assert parse_symbol("ETHBTC") == ("ETH", "BTC")

    def test_usdc_quote(self):
        assert parse_symbol("BTCUSDC") == ("BTC", "USDC")

    def test_bnb_quote(self):
        assert parse_symbol("XRPBNB") == ("XRP", "BNB")

    def test_eth_quote(self):
        assert parse_symbol("DOGEETH") == ("DOGE", "ETH")


class TestParseSymbolCaseInsensitive:
    """parse_symbol() normalises input to uppercase."""

    def test_lowercase(self):
        assert parse_symbol("ethusdt") == ("ETH", "USDT")

    def test_mixed_case(self):
        assert parse_symbol("EthUsdt") == ("ETH", "USDT")


class TestParseSymbolLongestMatch:
    """Longest-quote-first matching prevents ambiguity."""

    def test_usdt_over_ust(self):
        """USDT (4 chars) should match before shorter suffixes."""
        base, quote = parse_symbol("LUNAUSDT")
        assert base == "LUNA"
        assert quote == "USDT"

    def test_usdc_over_usd(self):
        base, quote = parse_symbol("BTCUSDC")
        assert base == "BTC"
        assert quote == "USDC"


class TestParseSymbolErrors:
    """parse_symbol() error cases."""

    def test_unknown_quote(self):
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            parse_symbol("ETHEUR")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            parse_symbol("")

    def test_quote_only(self):
        """Quote asset alone has no base → should raise."""
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            parse_symbol("USDT")

    def test_whitespace(self):
        with pytest.raises(ValueError, match="Cannot parse symbol"):
            parse_symbol("  ")


class TestQuoteAssets:
    """QUOTE_ASSETS constant sanity check."""

    def test_contains_usdt(self):
        assert "USDT" in QUOTE_ASSETS

    def test_contains_btc(self):
        assert "BTC" in QUOTE_ASSETS
