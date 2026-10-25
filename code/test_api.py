from src.kalshi_api import (
    get_kalshi_markets,
    get_kalshi_market
)

from src.polymarket_api import (
    get_polymarket_markets,
    get_polymarket_tokens
)


# Kalshi
kalshi = get_kalshi_markets(limit=10)

print("\nKalshi markets:", len(kalshi))

if kalshi:
    print("First Kalshi market:")
    print(kalshi[0]["title"])

    ticker = kalshi[0]["ticker"]
    details = get_kalshi_market(ticker)
    print("Market detail fields:", list(details.keys()))


# Polymarket
poly = get_polymarket_markets(limit=10)

print("\nPolymarket markets:", len(poly))

if poly:
    print("First Polymarket market:")
    print(poly[0]["question"])

    print("Tokens:")
    print(get_polymarket_tokens(poly[0]))