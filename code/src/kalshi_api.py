
import requests

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


def get_kalshi_markets(limit, status="open", mve_filter=None):
    """Fetch Kalshi markets using cursor-based pagination."""

    markets = []
    cursor = None

    with requests.Session() as session:
        while len(markets) < limit:
            params = {
                "limit": min(200, limit - len(markets)),
                "status": status
            }

            if mve_filter is not None:
                params["mve_filter"] = mve_filter

            if cursor:
                params["cursor"] = cursor

            response = session.get(
                f"{BASE_URL}/markets",
                params=params,
                timeout=30
            )

            response.raise_for_status()

            data = response.json()
            batch = data.get("markets", [])

            if not batch:
                break

            markets.extend(batch)
            cursor = data.get("cursor")

            print(f"Kalshi ({mve_filter}): fetched {len(markets)} markets")

            if not cursor:
                break

    return markets


def get_kalshi_market(ticker):
    """Fetch details for one market."""
    response = requests.get(
        f"{BASE_URL}/markets/{ticker}",
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("market", {})


def get_kalshi_event(event_ticker):
    """Fetch event details and associated rules."""
    response = requests.get(
        f"{BASE_URL}/events/{event_ticker}",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_kalshi_series(series_ticker):
    """Fetch series information and settlement sources."""
    response = requests.get(
        f"{BASE_URL}/series/{series_ticker}",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_kalshi_orderbook(ticker):
    """
    Fetch current order book.

    Note: Kalshi may require signed API authentication
    for this endpoint. Authentication is not yet added.
    """
    response = requests.get(
        f"{BASE_URL}/markets/{ticker}/orderbook",
        timeout=30
    )
    response.raise_for_status()
    return response.json()



