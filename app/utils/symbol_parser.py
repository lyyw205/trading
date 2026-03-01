"""Symbol parsing utility for multi-symbol support."""

QUOTE_ASSETS = ["USDT", "BUSD", "USDC", "TUSD", "BTC", "ETH", "BNB"]


def parse_symbol(symbol: str) -> tuple[str, str]:
    """Parse a trading pair symbol into (base_asset, quote_asset).

    Examples:
        parse_symbol("ETHUSDT") -> ("ETH", "USDT")
        parse_symbol("BTCUSDT") -> ("BTC", "USDT")
        parse_symbol("SOLBTC") -> ("SOL", "BTC")
    """
    symbol = symbol.upper()
    for quote in sorted(QUOTE_ASSETS, key=len, reverse=True):
        if symbol.endswith(quote):
            base = symbol[:-len(quote)]
            if base:
                return base, quote
    raise ValueError(f"Cannot parse symbol: {symbol}")
