"""Standardize Kalshi and Polymarket raw snapshots into one market-level table.

Run from project root:
    python code/src/standardize.py
    python code/src/standardize.py --timestamp 20261024_233425

Uses four JSON files from the SAME collection run. Never changes raw files.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DATASETS = ("kalshi_markets", "kalshi_events", "polymarket_markets", "polymarket_events")


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_snapshot(timestamp=None):
    """Find latest complete four-file run, or the explicitly requested run."""
    timestamps = []
    for path in RAW_DIR.glob("kalshi_markets_*.json"):
        stamp = path.stem.removeprefix("kalshi_markets_")
        if all((RAW_DIR / f"{name}_{stamp}.json").exists() for name in DATASETS):
            timestamps.append(stamp)

    if timestamp:
        if timestamp not in timestamps:
            raise FileNotFoundError(f"No complete four-file snapshot found for {timestamp} in {RAW_DIR}")
        return timestamp
    if not timestamps:
        raise FileNotFoundError(f"No complete four-file snapshot found in {RAW_DIR}")
    return max(timestamps)


def numeric(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parsed_list(value):
    """Gamma's outcomes and token IDs are often JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def clean_text(*parts):
    return " | ".join(str(part).strip() for part in parts if part and str(part).strip())


def kalshi_rows(markets, events):
    event_by_ticker = {
        item.get("event", {}).get("event_ticker"): item.get("event", {})
        for item in events
        if item.get("event", {}).get("event_ticker")
    }
    rows = []
    for market in markets:
        ticker = market.get("ticker")
        event = event_by_ticker.get(market.get("event_ticker"), {})
        legs = market.get("mve_selected_legs") or []
        event_title = event.get("title", "")
        market_title = market.get("title", "")
        subtitle = market.get("yes_sub_title") or market.get("subtitle")
        rules = clean_text(market.get("rules_primary"), market.get("rules_secondary"))
        rows.append({
            "platform": "kalshi",
            "market_id": ticker,
            "event_id": market.get("event_ticker"),
            "event_ids": [market.get("event_ticker")] if market.get("event_ticker") else [],
            "market_title": market_title,
            "event_title": event_title,
            "match_text": clean_text(event_title, market_title, subtitle),
            "resolution_rules": rules,
            "category": event.get("category"),
            "is_multivariate": bool(legs),
            "num_legs": len(legs),
            "mve_legs": legs,
            "outcomes": ["Yes", "No"],
            "token_ids": [],  # Kalshi uses tickers, not Polymarket CLOB token IDs
            "yes_bid": numeric(market.get("yes_bid_dollars")),
            "yes_ask": numeric(market.get("yes_ask_dollars")),
            "no_bid": numeric(market.get("no_bid_dollars")),
            "no_ask": numeric(market.get("no_ask_dollars")),
            "yes_bid_size": numeric(market.get("yes_bid_size_fp")),
            "yes_ask_size": numeric(market.get("yes_ask_size_fp")),
            "volume": numeric(market.get("volume_fp")),
            "volume_24h": numeric(market.get("volume_24h_fp")),
            "liquidity": None,  # Not equivalent to Polymarket's reported liquidity
            "status": market.get("status"),
            "accepting_orders": None,  # Kalshi market status does not guarantee order acceptance
            "close_time": market.get("close_time"),
            "event_time": market.get("occurrence_datetime"),
            "updated_at": market.get("updated_time"),
            "market_url": None,
        })
    return rows


def polymarket_rows(markets, events):
    event_by_id = {str(event.get("id")): event for event in events if event.get("id") is not None}
    rows = []
    for market in markets:
        links = market.get("events") or []
        event_ids = [str(e["id"]) for e in links if e.get("id") is not None]
        primary_event = event_by_id.get(event_ids[0], links[0] if links else {}) if event_ids else {}
        outcomes = parsed_list(market.get("outcomes"))
        tokens = parsed_list(market.get("clobTokenIds"))
        # Gamma 'bestBid'/'bestAsk' refer to the quoted outcome; treat as
        # YES quotes only when outcomes are exactly standard Yes/No.
        is_yes_no = outcomes == ["Yes", "No"]
        rows.append({
            "platform": "polymarket",
            "market_id": str(market.get("id")),
            "event_id": event_ids[0] if event_ids else None,
            "event_ids": event_ids,
            "market_title": market.get("question", ""),
            "event_title": primary_event.get("title", ""),
            "match_text": clean_text(primary_event.get("title"), market.get("question")),
            "resolution_rules": market.get("description") or primary_event.get("description") or "",
            "category": None,  # Polymarket categories/tags aren't Kalshi category equivalents
            "is_multivariate": False,  # No Kalshi-style selected legs in this market object
            "num_legs": 0,
            "mve_legs": [],
            "outcomes": outcomes,
            "token_ids": tokens,
            "yes_bid": numeric(market.get("bestBid")) if is_yes_no else None,
            "yes_ask": numeric(market.get("bestAsk")) if is_yes_no else None,
            "no_bid": None,
            "no_ask": None,
            "yes_bid_size": None,
            "yes_ask_size": None,
            "volume": numeric(market.get("volumeNum", market.get("volume"))),
            "volume_24h": numeric(market.get("volume24hr")),
            "liquidity": numeric(market.get("liquidityNum", market.get("liquidity"))),
            "status": "closed" if market.get("closed") else ("active" if market.get("active") else "inactive"),
            "accepting_orders": market.get("acceptingOrders"),
            "close_time": market.get("endDate"),
            "event_time": None,  # endDate is NOT necessarily the event occurrence time
            "updated_at": market.get("updatedAt"),
            "market_url": f"https://polymarket.com/event/{primary_event['slug']}" if primary_event.get("slug") else None,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp", help="Snapshot timestamp, e.g. 20261024_233425")
    args = parser.parse_args()
    stamp = find_snapshot(args.timestamp)
    print(f"Using raw snapshot: {stamp}")
    raw = {name: load_json(RAW_DIR / f"{name}_{stamp}.json") for name in DATASETS}

    rows = kalshi_rows(raw["kalshi_markets"], raw["kalshi_events"])
    rows += polymarket_rows(raw["polymarket_markets"], raw["polymarket_events"])
    df = pd.DataFrame(rows)
    if df.duplicated(["platform", "market_id"]).any():
        raise ValueError("Duplicate (platform, market_id) values detected")
    for column in ("event_ids", "outcomes", "token_ids", "mve_legs"):
        df[column] = df[column].apply(lambda value: json.dumps(value, ensure_ascii=False))

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / f"standardized_markets_{stamp}.csv"
    df.to_csv(path, index=False)
    print(f"Saved {len(df):,} rows, {len(df.columns)} columns to {path}")
    print(df.groupby("platform").size().to_string())
    print(f"Markets with resolution text: {(df['resolution_rules'].str.len() > 0).sum():,}")


if __name__ == "__main__":
    main()
