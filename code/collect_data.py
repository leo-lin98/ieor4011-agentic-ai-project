
from pathlib import Path
from datetime import datetime, timezone
import json
import time

import pandas as pd
import requests

from config import (
    KALSHI_REGULAR_LIMIT,
    KALSHI_MVE_LIMIT,
    POLYMARKET_MARKET_LIMIT
)

from src.kalshi_api import (
    get_kalshi_markets,
    get_kalshi_event
)

from src.polymarket_api import (
    get_polymarket_markets,
    get_polymarket_event
)


# ------------------------------------
# 1. CONFIGURATION
# ------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


# ------------------------------------
# 2. SAVE DATA (JSON + CSV)
# ------------------------------------

def save_data(data, filename):
    """Save data as both JSON and CSV."""

    json_path = DATA_DIR / f"{filename}_{timestamp}.json"
    csv_path = DATA_DIR / f"{filename}_{timestamp}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    pd.json_normalize(data).to_csv(csv_path, index=False)

    print(f"Saved {filename}: {len(data)} records")


# ------------------------------------
# 3. FETCH KALSHI MARKETS
# ------------------------------------

print("\nFetching regular Kalshi markets...")

regular_markets = get_kalshi_markets(
    limit=KALSHI_REGULAR_LIMIT,
    mve_filter="exclude"
)

print("\nFetching multivariate Kalshi markets...")

mve_markets = get_kalshi_markets(
    limit=KALSHI_MVE_LIMIT,
    mve_filter="only"
)

# Combine and remove duplicate market tickers
kalshi_markets = list({
    market["ticker"]: market
    for market in regular_markets + mve_markets
}.values())

print(f"\nRegular Kalshi markets: {len(regular_markets)}")
print(f"Multivariate Kalshi markets: {len(mve_markets)}")
print(f"Total unique Kalshi markets: {len(kalshi_markets)}")

save_data(kalshi_markets, "kalshi_markets")


# ------------------------------------
# 4. FETCH KALSHI EVENTS
# ------------------------------------

kalshi_event_ids = {
    market["event_ticker"]
    for market in kalshi_markets
    if market.get("event_ticker")
}

print(f"\nFound {len(kalshi_event_ids)} unique Kalshi events")

kalshi_events = []

for i, event_id in enumerate(sorted(kalshi_event_ids), 1):
    try:
        event_data = get_kalshi_event(event_id)
        kalshi_events.append(event_data)

    except requests.RequestException as e:
        print(f"Kalshi event error ({event_id}): {e}")

    if i % 50 == 0:
        print(
            f"Processed {i}/{len(kalshi_event_ids)} "
            "Kalshi events"
        )

    time.sleep(0.1)

save_data(kalshi_events, "kalshi_events")


# ------------------------------------
# 5. FETCH POLYMARKET MARKETS
# ------------------------------------

print("\nFetching Polymarket markets...")

polymarket_markets = get_polymarket_markets(
    limit=POLYMARKET_MARKET_LIMIT
)

save_data(polymarket_markets, "polymarket_markets")


# ------------------------------------
# 6. FETCH POLYMARKET EVENTS
# ------------------------------------

polymarket_event_ids = set()

for market in polymarket_markets:
    for event in market.get("events", []):
        event_id = event.get("id")

        if event_id is not None:
            polymarket_event_ids.add(str(event_id))

print(
    f"\nFound {len(polymarket_event_ids)} unique Polymarket events"
)

polymarket_events = []

for i, event_id in enumerate(sorted(polymarket_event_ids), 1):
    try:
        event_data = get_polymarket_event(event_id)
        polymarket_events.append(event_data)

    except requests.RequestException as e:
        print(f"Polymarket event error ({event_id}): {e}")

    if i % 50 == 0:
        print(
            f"Processed {i}/{len(polymarket_event_ids)} "
            "Polymarket events"
        )

    time.sleep(0.1)

save_data(polymarket_events, "polymarket_events")


# ------------------------------------
# 7. SUMMARY
# ------------------------------------

print("\n========== DATA COLLECTION SUMMARY ==========")

print(f"Kalshi regular markets: {len(regular_markets)}")
print(f"Kalshi multivariate markets: {len(mve_markets)}")
print(f"Kalshi total unique markets: {len(kalshi_markets)}")
print(f"Kalshi events: {len(kalshi_events)}/{len(kalshi_event_ids)}")

print(f"Polymarket markets: {len(polymarket_markets)}")
print(
    f"Polymarket events: "
    f"{len(polymarket_events)}/{len(polymarket_event_ids)}"
)

print(f"\nFiles saved to: {DATA_DIR}")
print("=============================================")
