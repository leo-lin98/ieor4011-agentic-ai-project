"""Kalshi fetchers and normalisers.

Pricing and catalog both come from GET /events?status=open&with_nested_markets=true
(multivariate/combo events are excluded by this endpoint), so one sweep feeds both.
"""
import re
from datetime import datetime, timezone

from .util import b, cents, flip, iso_ms, js, num, s, sha

PLATFORM = "KALSHI"

STATUS_MAP = {
    "initialized": "unopened", "unopened": "unopened",
    "active": "open", "open": "open",
    "inactive": "halted", "paused": "halted",
    "closed": "closed",
    "determined": "resolving", "disputed": "resolving", "amended": "resolving",
    "finalized": "settled", "settled": "settled",
}

SERIES_KEEP = ("ticker", "title", "category", "tags", "fee_type", "fee_multiplier",
               "contract_terms_url", "contract_url", "settlement_sources", "frequency")


def uid(ticker):
    return f"KALSHI:{ticker}"


def canon(status):
    return STATUS_MAP.get(status or "", "unknown")


async def fetch_series(api, priority=2):
    r = await api.get("/series", priority=priority)
    out = {}
    for ser in (r.data or {}).get("series") or []:
        out[ser.get("ticker")] = {k: ser.get(k) for k in SERIES_KEEP}
    return out, r


async def iter_open_event_pages(api, priority=1):
    cursor = None
    while True:
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        r = await api.get("/events", params=params, priority=priority)
        d = r.data or {}
        events = d.get("events") or []
        yield events, r
        cursor = d.get("cursor")
        if not cursor or not events:
            return


async def fetch_milestones(api, min_ms, max_ms, priority=2):
    """Sports milestones (one per game) starting in [min_ms, max_ms].

    Returns {event_ticker: {"start_ms", "game_id", "title"}} for every event a milestone lists in
    related_event_tickers (a game's winner, spread, total, prop events ...). start_date is the scheduled start,
    the time Kalshi shows on the game; Kalshi event tickers carry only the date for some sports (e.g. football).
    """
    out, cursor = {}, None
    params = {"category": "Sports", "limit": 200,
              "minimum_start_date": datetime.fromtimestamp(min_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    while True:
        if cursor:
            params["cursor"] = cursor
        r = await api.get("/milestones", params=params, priority=priority)
        d = r.data or {}
        ms = d.get("milestones") or []
        for m in ms:
            start = iso_ms(m.get("start_date"))
            if start is None or start > max_ms:
                continue
            info = {"start_ms": start, "game_id": s(m.get("id")), "title": s(m.get("title"))}
            for t in (m.get("related_event_tickers") or []) + (m.get("primary_event_tickers") or []):
                out.setdefault(t, info)
        cursor = d.get("cursor")
        last = iso_ms(ms[-1].get("start_date")) if ms else None
        if not cursor or not ms or (last is not None and last > max_ms):
            return out


def _tick_size_c(m):
    steps = [num(p.get("step")) for p in m.get("price_ranges") or [] if isinstance(p, dict)]
    steps = [x for x in steps if x]
    return round(min(steps) * 100, 6) if steps else None


def pricing_row(e, m, ser, tick_ts, resp):
    ser = ser or {}
    st = m.get("status")
    return {
        "tick_ts": tick_ts, "platform": PLATFORM, "market_uid": uid(m.get("ticker")),
        "request_sent_ts": resp.sent_ms, "response_received_ts": resp.recv_ms,
        "yes_bid_c": cents(m.get("yes_bid_dollars")), "yes_ask_c": cents(m.get("yes_ask_dollars")),
        "no_bid_c": cents(m.get("no_bid_dollars")), "no_ask_c": cents(m.get("no_ask_dollars")),
        "last_price_c": cents(m.get("last_price_dollars")),
        "yes_bid_size": num(m.get("yes_bid_size_fp")), "yes_ask_size": num(m.get("yes_ask_size_fp")),
        "price_derived": False,
        "volume": num(m.get("volume_fp")), "volume_24h": num(m.get("volume_24h_fp")),
        "open_interest": num(m.get("open_interest_fp")),
        "status_raw": s(st), "status_canonical": canon(st),
        "fee_type": s(ser.get("fee_type")), "fee_multiplier": num(ser.get("fee_multiplier")),
        "fee_type_override": s(e.get("fee_type_override")),
        "fee_multiplier_override": num(e.get("fee_multiplier_override")),
        "fee_coefficient": None, "tick_size_c": _tick_size_c(m), "min_order_qty": None,
        "price_level_structure": s(m.get("price_level_structure")),
    }


def _team_code(m):
    """Team code for team-specific markets: the ticker suffix without its line digits (…-TB4 -> TB)."""
    cs = m.get("custom_strike")
    if not (isinstance(cs, dict) and any(k.endswith("_team") for k in cs)):
        return None
    suffix = (m.get("ticker") or "").rsplit("-", 1)[-1]
    return re.sub(r"\d+$", "", suffix) or None


def catalog_row(e, m, ser, pulled_ms, games=None):
    """games: fetch_milestones() output; sets game_start_ms / game_id for events that belong to a game."""
    ser = ser or {}
    game = (games or {}).get(e.get("event_ticker")) or {}
    st = m.get("status")
    series_ticker = e.get("series_ticker")
    pm = e.get("product_metadata") or {}
    row = {
        "market_uid": uid(m.get("ticker")), "event_uid": f"KALSHI:{e.get('event_ticker')}", "platform": PLATFORM,
        "native_market_id": s(m.get("ticker")), "native_event_id": s(e.get("event_ticker")),
        "market_slug": None, "event_slug": None,
        "series_id": s(series_ticker), "series_title": s(ser.get("title")),
        "event_title": s(e.get("title")), "event_subtitle": s(e.get("sub_title")),
        "market_title": s(m.get("title")), "market_question": s(m.get("title")),
        "yes_title": s(m.get("yes_sub_title")), "no_title": s(m.get("no_sub_title")),
        "yes_side_label": "Yes", "no_side_label": "No",
        "market_type": s(m.get("market_type")),
        "category": s(ser.get("category") or e.get("category")), "subcategory": None,
        "tags": js(ser.get("tags")),
        "competition": s(pm.get("competition")), "competition_scope": s(pm.get("competition_scope")),
        "sports_market_type": None, "line": None,
        "yes_team_code": _team_code(m), "no_team_code": None, "event_teams": None,
        "game_id": game.get("game_id"),
        "mutually_exclusive": b(e.get("mutually_exclusive")),
        "collateral_return_type": s(e.get("collateral_return_type")),
        "strike_type": s(m.get("strike_type")), "floor_strike": num(m.get("floor_strike")),
        "cap_strike": num(m.get("cap_strike")), "custom_strike": js(m.get("custom_strike")),
        "strike_date_ms": iso_ms(e.get("strike_date")), "strike_period": s(e.get("strike_period")),
        "open_time_ms": iso_ms(m.get("open_time")), "close_time_ms": iso_ms(m.get("close_time")),
        "expected_expiration_ms": iso_ms(m.get("expected_expiration_time")),
        "latest_expiration_ms": iso_ms(m.get("latest_expiration_time")),
        "occurrence_ms": iso_ms(m.get("occurrence_datetime")), "game_start_ms": game.get("start_ms"),
        "settlement_ts_ms": iso_ms(m.get("settlement_ts")),
        "created_time_ms": iso_ms(m.get("created_time")), "updated_time_ms": iso_ms(m.get("updated_time")),
        "event_start_ms": None, "event_end_ms": None, "event_closed_time_ms": None,
        "can_close_early": b(m.get("can_close_early")), "early_close_condition": s(m.get("early_close_condition")),
        "rules_primary": s(m.get("rules_primary")), "rules_secondary": s(m.get("rules_secondary")),
        "settlement_sources": js(e.get("settlement_sources") or ser.get("settlement_sources")),
        "contract_terms_url": s(ser.get("contract_terms_url")), "contract_url": s(ser.get("contract_url")),
        "description": None, "rules_disclaimer": None, "event_description": None, "resolution_source": None,
        "status_raw": s(st), "status_canonical": canon(st), "result": s(m.get("result")),
        "settlement_value_c": cents(m.get("settlement_value_dollars")),
        "expiration_value": s(m.get("expiration_value")),
        "ep3_status": None, "is_active": None, "is_closed": None, "is_archived": None, "is_hidden": None,
        "event_active": None, "event_closed": None, "event_archived": None, "event_live": None,
        "event_ended": None, "event_state": None,
        # NOTE: URL format unverified (kalshi.com blocks automated fetches); series pages redirect in-browser.
        "market_url": f"https://kalshi.com/markets/{series_ticker.lower()}" if series_ticker else None,
    }
    row["rules_hash"] = rules_hash(row)
    return row


RULES_FIELDS = ("event_title", "event_subtitle", "market_title", "market_question", "yes_title", "no_title",
                "rules_primary", "rules_secondary", "settlement_sources", "description", "rules_disclaimer",
                "event_description", "resolution_source", "close_time_ms", "expected_expiration_ms",
                "latest_expiration_ms", "strike_type", "floor_strike", "cap_strike", "custom_strike")


def rules_hash(row):
    return sha(*[row.get(k) for k in RULES_FIELDS])


def book_rows(market_uid, data, tick_ts, resp):
    """GET /markets/{ticker}/orderbook returns bids only; asks are derived (YES ask = 100 - NO bid)."""
    ob = (data or {}).get("orderbook_fp") or {}
    yes = sorted(((cents(p), num(q)) for p, q in ob.get("yes_dollars") or []), key=lambda x: -x[0])
    no = sorted(((cents(p), num(q)) for p, q in ob.get("no_dollars") or []), key=lambda x: -x[0])
    h = sha(ob)
    rows = []

    def add(outcome, side, levels, derived):
        for i, (p, q) in enumerate(levels):
            rows.append({
                "tick_ts": tick_ts, "platform": PLATFORM, "market_uid": market_uid,
                "request_sent_ts": resp.sent_ms, "response_received_ts": resp.recv_ms, "exchange_ts": None,
                "outcome": outcome, "side": side, "level": i, "price_c": p, "qty": q, "derived": derived,
                "book_hash": h, "book_state": None,
            })

    add("yes", "bid", yes, False)
    add("no", "bid", no, False)
    add("yes", "ask", [(flip(p), q) for p, q in no], True)
    add("no", "ask", [(flip(p), q) for p, q in yes], True)
    return rows, h
