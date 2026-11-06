"""T0 market_profiles: the agent-facing view of the catalog.

One row per OPEN market, same columns for both platforms, holding only what the matching
workflow reads: who/what (title, summary), how it resolves (rules), category, timing.
Built from market_catalog (T1) after every catalog refresh.

  data/market_profiles/market_profiles.parquet
"""
import json
import os

import pyarrow as pa
import pyarrow.parquet as pq

from .config import DATA
from .schemas import MARKET_PROFILES
from .store import read_catalog
from .util import now_ms

PROFILES_PATH = DATA / "market_profiles" / "market_profiles.parquet"

CATALOG_COLS = ["market_uid", "platform", "event_uid", "category", "subcategory", "tags",
                "event_title", "event_subtitle", "market_title", "market_question", "yes_title", "yes_side_label",
                "rules_primary", "rules_secondary", "settlement_sources", "early_close_condition",
                "description", "rules_disclaimer", "event_description",
                "open_time_ms", "close_time_ms", "status_canonical", "market_url", "rules_hash"]


def _clean(x):
    return x.strip() if isinstance(x, str) and x.strip() else None


def _sources(js):
    try:
        items = json.loads(js) if js else []
    except ValueError:
        return None
    names = [i.get("name") for i in items if isinstance(i, dict) and i.get("name")]
    return ", ".join(names) or None


def _profile(c, as_of):
    plat = c["platform"]
    event = _clean(c["event_title"])
    if plat == "KALSHI":
        market = _clean(c["market_title"])
        yes = _clean(c["yes_title"])
        sub = _clean(c["event_subtitle"])
        rules = [_clean(c["rules_primary"]), _clean(c["rules_secondary"])]
        if _clean(c["early_close_condition"]):
            rules.append("Early close: " + c["early_close_condition"].strip())
        src = _sources(c["settlement_sources"])
        if src:
            rules.append("Settlement sources: " + src)
    else:
        q, t = _clean(c["market_question"]), _clean(c["market_title"])
        market = q if not t or (q and t in q) else (f"{q} ({t})" if q else t)
        side = _clean(c["yes_side_label"])
        yes = market if not side or side.lower() == "yes" else side
        sub = None
        ev_desc = _clean(c["event_description"])
        rules = [_clean(c["description"]), _clean(c["rules_disclaimer"]),
                 ev_desc if ev_desc and ev_desc != event else None]

    parts = [f"Event: {event}" + (f" ({sub})" if sub and sub not in (event or "") else "")]
    if market and market != event:
        parts.append(f"Market: {market}")
    if yes and yes != market:
        parts.append(f"YES means: {yes}")
    close = c["close_time_ms"]
    return {
        "market_uid": c["market_uid"], "platform": plat, "event_uid": c["event_uid"],
        "category": c["category"], "subcategory": c["subcategory"], "tags": c["tags"],
        "title": event, "market_title": market, "yes_means": yes,
        "summary": " | ".join(parts),
        "rules": "\n\n".join(r for r in rules if r) or None,
        "open_time_ms": c["open_time_ms"], "close_time_ms": close,
        "time_until_close_hours": round((close - as_of) / 3_600_000, 2) if close else None,
        "status": c["status_canonical"], "market_url": c["market_url"], "rules_hash": c["rules_hash"],
        "as_of_ms": as_of,
    }


def build_profiles(as_of=None):
    """Rebuild market_profiles.parquet from the current catalogs (open markets only)."""
    as_of = as_of or now_ms()
    rows = []
    for plat in ("KALSHI", "POLYUS"):
        cat = read_catalog(plat, CATALOG_COLS)
        if cat is None:
            continue
        for c in cat.to_pylist():
            if c["status_canonical"] == "open" and (c["close_time_ms"] is None or c["close_time_ms"] > as_of):
                rows.append(_profile(c, as_of))
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILES_PATH.with_name("." + PROFILES_PATH.name + ".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=MARKET_PROFILES), tmp, compression="zstd")
    os.replace(tmp, PROFILES_PATH)
    return len(rows)
