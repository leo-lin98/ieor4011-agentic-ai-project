"""Check the baseball matcher against real markets with known answers.

    python -m candidates.sports.baseball.selftest

fixtures/ holds real catalog rows (built with the collector's own catalog_row, so they have every T1 field):
  - 2026 World Series Game 5, Milwaukee vs Tampa Bay, 2026-10-28, both platforms (all bet types, settled)
  - the 2026-11-05 catalog: KBO games Nov 6-8, KBO and MLB futures
Run this after changing anything in candidates/ or the collector's catalog fields, and before each season.
"""
import sys
from pathlib import Path

import pandas as pd

from . import match

FIX = Path(__file__).parent / "fixtures"
G5 = "26OCT282000MILTB"


def main():
    k, p = pd.read_parquet(FIX / "kalshi.parquet"), pd.read_parquet(FIX / "polyus.parquet")
    # Matching check: no resolution window (the fixtures are past games)
    out, templates, r = match.run(write=False, frames=(k, p, 0, 0), approvals={}, window_days=None)
    pi = p.set_index("market_uid")
    got = {(x.kalshi_market_uid, x.direction) for x in out.itertuples()}
    fails = []

    def expect(cond, msg):
        if not cond:
            fails.append(msg)

    def pair(kalshi_ticker, direction, polyus_question_part):
        hits = [x for x in out.itertuples() if x.kalshi_market_uid == f"KALSHI:{kalshi_ticker}" and x.direction == direction
                and polyus_question_part in (pi.loc[x.polyus_market_uid].market_question or "")]
        expect(len(hits) == 1, f"expected 1 {direction} pair for {kalshi_ticker} ~ '{polyus_question_part}', got {len(hits)}")

    # World Series Game 5: one of every bet type
    pair(f"KXMLBGAME-{G5}-MIL", "same", "Who will win")
    pair(f"KXMLBGAME-{G5}-TB", "opposite", "Who will win")
    pair(f"KXMLBSPREAD-{G5}-MIL2", "same", "Milwaukee Brewers cover -1.5")
    pair(f"KXMLBSPREAD-{G5}-TB2", "opposite", "Milwaukee Brewers cover 1.5")
    pair(f"KXMLBTOTAL-{G5}-2", "same", "be more than 1.5")
    pair(f"KXMLBTEAMTOTAL-{G5}-TB2", "same", "Tampa Bay Rays score more than 1.5")
    pair(f"KXMLBF5-{G5}-TIE", "same", "first 5 innings of Tampa Bay Rays vs Milwaukee Brewers end in a tie")
    pair(f"KXMLBF5-{G5}-MIL", "same", "Milwaukee Brewers win the first 5 innings")
    pair(f"KXMLBINNINGWIN-{G5}-9-TB", "same", "Tampa Bay Rays win the 9th inning")
    expect(any(k_.startswith(f"KALSHI:KXMLBRFI-{G5}") for k_, _ in got), "first-inning run not paired")
    expect(any(k_.startswith(f"KALSHI:KXMLBEXTRAS-{G5}") for k_, _ in got), "extra innings not paired")

    # Spreads never pair a Polymarket US line with a different Kalshi line
    for x in out[out.map_key.str.endswith("_spread")].itertuples():
        expect(x.match_key.split("line=")[1] in x.kalshi_yes, f"spread line mismatch: {x.match_key} vs {x.kalshi_yes}")

    # KBO: 3 games, each Polymarket US moneyline pairs same with one team and opposite with the other
    kbo = out[out.map_key == "kbo_full_game_winner"]
    expect(len(kbo) == 6 and set(kbo.direction) == {"same", "opposite"}, f"KBO games: expected 6 pairs, got {len(kbo)}")
    expect((kbo.start_diff_min == 0).all(), "KBO start times should match exactly")

    # Futures
    fut = {x["key"]: x["market_pairs"] for x in r["futures"]}
    expect(fut.get("mlb_world_series") == 30, f"World Series: expected 30 pairs, got {fut.get('mlb_world_series')}")
    expect(fut.get("mlb_nl_mvp") == 3, "NL MVP: expected 3 pairs")

    # Nothing silently dropped
    g = r["games"]
    expect(not any(g["unknown_bet_types"].values()), f"unknown bet types: {g['unknown_bet_types']}")
    expect(not any(g["unknown_leagues"].values()), f"unknown leagues: {g['unknown_leagues']}")
    expect(not g["unusable_markets"], f"unusable markets: {g['unusable_markets']}")
    expect(not g["unpaired_polyus_games"], f"unpaired Polymarket US games: {g['unpaired_polyus_games']}")
    dup = out.groupby(["polyus_market_uid", "direction"]).size()
    expect((dup == 1).all(), f"a Polymarket US market paired twice in one direction: {dup[dup > 1].index.tolist()[:5]}")

    # Close window: 2 hours before World Series Game 5, 7 days. Only Game 5 should remain
    # (KBO games close Nov 8-10, awards close late Nov / Dec, World Series 2027 futures much later).
    before_g5 = int(pd.Timestamp("2026-10-28T18:00Z").timestamp() * 1000)
    w_out, _, w_r = match.run(write=False, frames=(k, p, 0, 0), approvals={}, window_days=7, now=before_g5)
    g5 = out[(out["shape"] == "game") & (out.league == "MLB")]
    expect(set(w_out.pair_id) == set(g5.pair_id),
           f"7-day window from Oct 28: expected exactly the {len(g5)} Game 5 pairs, got {len(w_out)} "
           f"({w_out.map_key.value_counts().to_dict()})")
    expect(w_r["window"]["POLYUS"]["dropped_closes_later"] > 0 and w_r["window"]["KALSHI"]["dropped_closes_later"] > 0,
           "window should drop later-closing markets on both platforms")

    print(f"{len(out)} pairs ({(out['shape'] == 'game').sum()} game, {(out['shape'] != 'game').sum()} futures), "
          f"{len(templates)} template pairs; 7-day window from {pd.Timestamp(before_g5, unit='ms', tz='UTC'):%Y-%m-%d %H:%MZ}"
          f" keeps {len(w_out)}")
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
