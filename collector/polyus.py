"""Polymarket US fetchers and normalisers.

- Pricing: GET /v1/markets (offset pagination, max 500/page) carries best bid/ask per market.
- Catalog: GET /v1/events (500/page). The list view truncates nested markets for big sports
  events (spreads/totals/props), so those events are re-fetched one by one with ?slug=.
- Per-market volume / OI only exist on /v1/markets/{slug}/book and /bbo (market_stats table).
Prices on the book are for the YES (long) side; NO prices are derived as 100 - YES.
"""
import asyncio

from .kalshi import rules_hash
from .util import b, cents, flip, iso_ms, js, num, s, sha

PLATFORM = "POLYUS"

STATUS_MAP = {
    "MARKET_STATUS_OPEN": "open",
    "MARKET_STATUS_CLOSED": "closed",
    "MARKET_STATUS_RESOLVING": "resolving",
    "MARKET_STATUS_RESOLVED": "settled",
    "MARKET_STATUS_HALTED": "halted",
}


def uid(market_id):
    return f"POLYUS:{market_id}"


def canon(status):
    return STATUS_MAP.get(status or "", "unknown")


async def iter_market_pages(api, priority=1, page=500, wave=6):
    """Fetch active market pages `wave` at a time until an empty page comes back."""
    offset = 0
    while True:
        offs = [offset + i * page for i in range(wave)]
        resps = await asyncio.gather(*[
            api.get("/v1/markets", params={"limit": page, "offset": o, "active": "true", "closed": "false"},
                    priority=priority)
            for o in offs])
        done = False
        for r in resps:
            ms = (r.data or {}).get("markets") or []
            if not ms:
                done = True
                continue
            yield ms, r
        if done:
            return
        offset += wave * page


async def iter_catalog_events(api, raw=None, priority=2, concurrency=8):
    """Yield (events, resp) with complete nested market lists."""
    offset = 0
    sem = asyncio.Semaphore(concurrency)

    async def full_event(e):
        async with sem:
            rr = await api.get("/v1/events", params={"slug": e.get("slug"), "limit": 1,
                                                       "active": "true", "closed": "false"}, priority=priority)
        got = [x for x in (rr.data or {}).get("events") or [] if x.get("id") == e.get("id")]
        if got and len(got[0].get("markets") or []) >= len(e.get("markets") or []):
            return got[0], rr
        return e, rr  # keep the partial event rather than dropping it

    while True:
        r = await api.get("/v1/events", params={"limit": 500, "offset": offset, "active": "true",
                                                 "closed": "false"}, priority=priority)
        events = (r.data or {}).get("events") or []
        if not events:
            return
        offset += len(events)
        if raw:
            raw.write("GET /v1/events", r, events)
        complete, truncated = [], []
        for e in events:
            want = (e.get("marketCounts") or {}).get("numMarkets") or 0
            (truncated if len(e.get("markets") or []) < want else complete).append(e)
        yield complete, r
        for fut in asyncio.as_completed([full_event(e) for e in truncated]):
            ev, rr = await fut
            if raw:
                raw.write("GET /v1/events?slug", rr, [ev])
            yield [ev], rr


def _sides(m):
    long_side = short_side = None
    for side in m.get("marketSides") or []:
        if side.get("long") is True and long_side is None:
            long_side = side
        elif side.get("long") is False and short_side is None:
            short_side = side
    return long_side or {}, short_side or {}


def _team(side):
    code = ((side or {}).get("team") or {}).get("abbreviation")
    return code.lower() if isinstance(code, str) and code else None


def _labels(tags):
    out = []
    for t in tags or []:
        out.append(t.get("label") or t.get("slug") if isinstance(t, dict) else str(t))
    return out


def pricing_row(m, tick_ts, resp):
    yb, ya = cents(m.get("bestBidQuote")), cents(m.get("bestAskQuote"))
    tick = num(m.get("orderPriceMinTickSize"))
    st = m.get("status")
    return {
        "tick_ts": tick_ts, "platform": PLATFORM, "market_uid": uid(m.get("id")),
        "request_sent_ts": resp.sent_ms, "response_received_ts": resp.recv_ms,
        "yes_bid_c": yb, "yes_ask_c": ya, "no_bid_c": flip(ya), "no_ask_c": flip(yb),
        "last_price_c": None, "yes_bid_size": None, "yes_ask_size": None, "price_derived": True,
        "volume": None, "volume_24h": None, "open_interest": None,  # see market_stats
        "status_raw": s(st), "status_canonical": canon(st),
        "fee_type": "taker_theta_p_1mp", "fee_multiplier": None,
        "fee_type_override": None, "fee_multiplier_override": None,
        "fee_coefficient": num(m.get("feeCoefficient")),
        "tick_size_c": round(tick * 100, 6) if tick else None,
        "min_order_qty": num(m.get("minimumTradeQty")), "price_level_structure": None,
    }


def catalog_row(e, m, pulled_ms):
    long_side, short_side = _sides(m)
    st = m.get("status")
    tags = _labels(e.get("tags")) + [t for t in _labels(m.get("tags")) if t not in _labels(e.get("tags"))]
    row = {
        "market_uid": uid(m.get("id")), "event_uid": f"POLYUS:{e.get('id')}", "platform": PLATFORM,
        "native_market_id": s(m.get("id")), "native_event_id": s(e.get("id")),
        "market_slug": s(m.get("slug")), "event_slug": s(e.get("slug")),
        "series_id": s(e.get("seriesSlug")), "series_title": None,
        "event_title": s(e.get("title")), "event_subtitle": s(e.get("subtitle")),
        "market_title": s(m.get("title")), "market_question": s(m.get("question")),
        "yes_title": s(m.get("question") or m.get("title")), "no_title": None,
        "yes_side_label": s(long_side.get("description")), "no_side_label": s(short_side.get("description")),
        "market_type": s(m.get("marketType") or m.get("sportsMarketTypeV2")),
        "category": s(m.get("category") or e.get("category")),
        "subcategory": s(m.get("eventSubcategory") or e.get("subcategory")),
        "tags": js(tags) if tags else None,
        "competition": None, "competition_scope": None,
        "sports_market_type": s(m.get("sportsMarketType")), "line": num(m.get("line")),
        "yes_team_code": _team(long_side), "no_team_code": _team(short_side),
        "event_teams": js([{"code": s((t.get("abbreviation") or "").lower()), "display": s(t.get("displayAbbreviation")),
                            "name": s(t.get("name"))} for t in e.get("teams") or [] if isinstance(t, dict)]) or None,
        "game_id": None,  # Polymarket US has one event per game: event_uid identifies it
        "mutually_exclusive": None, "collateral_return_type": None,
        "strike_type": None, "floor_strike": None, "cap_strike": None,
        "custom_strike": js(m.get("assetPriceTerms")), "strike_date_ms": None, "strike_period": None,
        "open_time_ms": iso_ms(m.get("startDate")), "close_time_ms": iso_ms(m.get("endDate")),
        "expected_expiration_ms": None, "latest_expiration_ms": None, "occurrence_ms": None,
        "game_start_ms": iso_ms(m.get("gameStartTime")), "settlement_ts_ms": None,
        "created_time_ms": iso_ms(m.get("createdAt")), "updated_time_ms": iso_ms(m.get("updatedAt")),
        "event_start_ms": iso_ms(e.get("startDate")), "event_end_ms": iso_ms(e.get("endDate")),
        "event_closed_time_ms": iso_ms(e.get("closedTime")),
        "can_close_early": None, "early_close_condition": None,
        "rules_primary": None, "rules_secondary": None, "settlement_sources": None,
        "contract_terms_url": None, "contract_url": None,
        "description": s(m.get("description")), "rules_disclaimer": s(m.get("rulesDisclaimer")),
        "event_description": s(e.get("description")), "resolution_source": s(e.get("resolutionSource")),
        "status_raw": s(st), "status_canonical": canon(st), "result": None,
        "settlement_value_c": None, "expiration_value": None,
        "ep3_status": s(m.get("ep3Status")), "is_active": b(m.get("active")), "is_closed": b(m.get("closed")),
        "is_archived": b(m.get("archived")), "is_hidden": b(m.get("hidden")),
        "event_active": b(e.get("active")), "event_closed": b(e.get("closed")),
        "event_archived": b(e.get("archived")), "event_live": b(e.get("live")), "event_ended": b(e.get("ended")),
        "event_state": js(e.get("eventState")),
        "market_url": f"https://polymarket.us/event/{e.get('slug')}" if e.get("slug") else None,
    }
    row["rules_hash"] = rules_hash(row)
    return row


def _stats_row(market_uid, source, md, st, tick_ts, resp, exchange_ts):
    return {
        "tick_ts": tick_ts, "platform": PLATFORM, "market_uid": market_uid, "source": source,
        "request_sent_ts": resp.sent_ms, "response_received_ts": resp.recv_ms, "exchange_ts": exchange_ts,
        "state": s(md.get("state")),
        "best_bid_c": cents(md.get("bestBid")), "best_ask_c": cents(md.get("bestAsk")),
        "current_px_c": cents(md.get("currentPx")),
        "last_trade_c": cents(st.get("lastTradePx")), "last_trade_qty": num(st.get("lastTradeQty")),
        "settlement_px_c": cents(st.get("settlementPx")),
        "open_px_c": cents(st.get("openPx")), "high_px_c": cents(st.get("highPx")),
        "low_px_c": cents(st.get("lowPx")), "close_px_c": cents(st.get("closePx")),
        "shares_traded": num(st.get("sharesTraded")), "notional_traded": num(st.get("notionalTraded")),
        "open_interest": num(st.get("openInterest")),
        "bid_depth": md.get("bidDepth"), "ask_depth": md.get("askDepth"),
        "bid_shares": num(md.get("bidShares")), "ask_shares": num(md.get("askShares")),
    }


def book_rows(market_uid, data, tick_ts, resp):
    """GET /v1/markets/{slug}/book -> (level rows, stats row, book_hash)."""
    md = (data or {}).get("marketData") or {}
    bids = sorted(((cents(x.get("px")), num(x.get("qty"))) for x in md.get("bids") or []), key=lambda x: -x[0])
    offers = sorted(((cents(x.get("px")), num(x.get("qty"))) for x in md.get("offers") or []), key=lambda x: x[0])
    exch = iso_ms(md.get("transactTime"))
    state = s(md.get("state"))
    h = sha(bids, offers, state)
    rows = []

    def add(outcome, side, levels, derived):
        for i, (p, q) in enumerate(levels):
            rows.append({
                "tick_ts": tick_ts, "platform": PLATFORM, "market_uid": market_uid,
                "request_sent_ts": resp.sent_ms, "response_received_ts": resp.recv_ms, "exchange_ts": exch,
                "outcome": outcome, "side": side, "level": i, "price_c": p, "qty": q, "derived": derived,
                "book_hash": h, "book_state": state,
            })

    add("yes", "bid", bids, False)
    add("yes", "ask", offers, False)
    add("no", "bid", [(flip(p), q) for p, q in offers], True)
    add("no", "ask", [(flip(p), q) for p, q in bids], True)
    stats = _stats_row(market_uid, "book", md, md.get("stats") or {}, tick_ts, resp, exch) if md else None
    return rows, stats, h


def bbo_stats_row(market_uid, data, tick_ts, resp):
    md = (data or {}).get("marketData") or {}
    if not md:
        return None
    exch = iso_ms((md.get("lastPriceSample") or {}).get("ts"))
    return _stats_row(market_uid, "bbo", md, md, tick_ts, resp, exch)
