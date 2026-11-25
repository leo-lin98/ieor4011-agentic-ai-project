"""Print candidate pairs in a readable form, to eyeball them for bugs.

    python -m candidates.show sports basketball/nba                 # N examples per bet type
    python -m candidates.show sports basketball/nba -n 5            # 5 per bet type
    python -m candidates.show sports basketball/nba --game sa-por   # every pair of one game (slug substring)
    python -m candidates.show sports basketball/nba --templates     # template pairs, biggest first
    python -m candidates.show sports basketball/nba --random 20     # 20 random pairs across everything

Reads data/candidate_pairs/<category>/<subcategory>/ written by `python -m candidates ...`.
"""
import argparse

import pandas as pd

from collector.config import DATA


def _wrap(s, w):
    s = "" if s is None or s != s else str(s)
    return s if len(s) <= w else s[:w - 1] + "…"


def print_pairs(df, width=95):
    for i, r in enumerate(df.itertuples(), 1):
        when = pd.to_datetime(r.event_time_ms, unit="ms", utc=True).strftime("%b %d %H:%M") if pd.notna(r.event_time_ms) else "-"
        print(f"{i:3d}. [{r.direction:8s}] {r.map_key}   game {when} UTC   status={r.status}")
        print(f"       KALSHI  {r.kalshi_market_uid}")
        print(f"               title: {_wrap(r.kalshi_title, width)}")
        print(f"               YES  : {_wrap(r.kalshi_yes, width)}")
        print(f"       POLYUS  {r.polyus_market_uid}")
        print(f"               title: {_wrap(r.polyus_title, width)}")
        print(f"               YES  : {_wrap(r.polyus_yes, width)}")
        print(f"       key: {r.match_key}   template_pair: {r.template_pair_id}")
        print()


def main():
    ap = argparse.ArgumentParser(prog="candidates.show")
    ap.add_argument("category")
    ap.add_argument("subcategory")
    ap.add_argument("-n", type=int, default=2, help="examples per bet type and direction (default 2)")
    ap.add_argument("--game", help="only pairs whose Polymarket US event slug contains this (e.g. sa-por)")
    ap.add_argument("--type", help="only this map_key (e.g. nba_full_game_spread)")
    ap.add_argument("--random", type=int, metavar="N", help="N random pairs across everything")
    ap.add_argument("--templates", action="store_true", help="list template pairs instead, biggest first")
    a = ap.parse_args()
    out = DATA / "candidate_pairs" / a.category / a.subcategory
    pairs = pd.read_parquet(out / "candidate_pairs.parquet")
    print(f"{len(pairs):,} pairs in {out}\n")

    if a.templates:
        t = pd.read_parquet(out / "template_pairs.parquet").sort_values("n_pairs", ascending=False)
        for r in t.itertuples():
            print(f"=== {r.template_pair_id}  {r.map_key}  n_pairs={r.n_pairs}  status={r.status}")
            print("  KALSHI example rules:\n    " + _wrap(r.kalshi_example, 600).replace("\n", "\n    "))
            print("  POLYUS example rules:\n    " + _wrap(r.polyus_example, 600).replace("\n", "\n    "))
            print()
        return

    if a.game:
        cat = pd.read_parquet(DATA / "market_catalog" / "current" / "POLYUS.parquet", columns=["event_uid", "event_slug"])
        uids = set(cat[cat.event_slug.str.contains(a.game, na=False)].event_uid)
        pairs = pairs[pairs.polyus_event_uid.isin(uids)]
        print(f"{len(pairs)} pairs for games matching '{a.game}'\n")
    if a.type:
        pairs = pairs[pairs.map_key == a.type]
    if a.random:
        print_pairs(pairs.sample(min(a.random, len(pairs)), random_state=None))
        return
    if a.game or a.type:
        print_pairs(pairs.sort_values(["map_key", "direction", "match_key"]))
        return
    # default: n examples per (map_key, direction)
    print(pairs.groupby(["map_key", "direction"]).size().unstack(fill_value=0).to_string(), "\n")
    print_pairs(pairs.groupby(["map_key", "direction"], group_keys=False).head(a.n).sort_values(["map_key", "direction"]))


if __name__ == "__main__":
    main()
