
import json
import requests

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


def get_polymarket_markets(limit):
    """Fetch active markets using offset pagination."""
    markets = []
    offset = 0

    with requests.Session() as session:
        while len(markets) < limit:
            page_size = min(100, limit - len(markets))

            params = {
                "limit": page_size,
                "offset": offset,
                "active": "true",
                "closed": "false"
            }

            response = session.get(
                f"{GAMMA_URL}/markets",
                params=params,
                timeout=30
            )
            response.raise_for_status()

            batch = response.json()

            if not batch:
                break

            markets.extend(batch)
            offset += len(batch)

            print(
                f"Polymarket: fetched {len(markets)} markets"
            )

            if len(batch) < page_size:
                break

    return markets


def get_polymarket_market(market_id):
    """Fetch a single market's details."""
    response = requests.get(
        f"{GAMMA_URL}/markets/{market_id}",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_polymarket_event(event_id):
    """Fetch associated event information."""
    response = requests.get(
        f"{GAMMA_URL}/events/{event_id}",
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_polymarket_orderbook(token_id):
    """Fetch current order book for an outcome token."""
    response = requests.get(
        f"{CLOB_URL}/book",
        params={"token_id": str(token_id)},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_polymarket_tokens(market):
    """Map outcome labels to their CLOB token IDs."""
    outcomes = market.get("outcomes", [])
    token_ids = market.get("clobTokenIds", [])

    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)

    if isinstance(token_ids, str):
        token_ids = json.loads(token_ids)

    if len(outcomes) != len(token_ids):
        raise ValueError(
            "Outcomes and token IDs have different lengths"
        )

    return dict(zip(outcomes, token_ids))
