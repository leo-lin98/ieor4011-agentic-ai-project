"""Run candidate generation for one subcategory and print the cut report.

    python -m candidates sports baseball            # writes data/candidate_pairs/sports/baseball/
    python -m candidates sports baseball --no-write # report only
"""
import argparse
import importlib


def _top(d, n=10):
    items = list(d.items())
    s = ", ".join(f"{k} ({v})" for k, v in items[:n])
    return (s + (f", … {len(items) - n} more" if len(items) > n else "")) or "none"


def format_report(r):
    f, fu, ga = r["funnel"], r["funnel"]["futures"], r["funnel"]["games"]
    g = r["games"]
    L = [f"Candidate pairs: {r['subcategory']}   (catalog pulled: Kalshi {r['catalog_pulled']['KALSHI']}, "
         f"Polymarket US {r['catalog_pulled']['POLYUS']})", "",
         f"  Open markets         Kalshi {f['open_markets']['KALSHI']:>6,}   Polymarket US {f['open_markets']['POLYUS']:>6,}",
         (f"  Close by {r['window']['end']}  "
          f"  Kalshi {f['in_window']['KALSHI']:>6,}   Polymarket US {f['in_window']['POLYUS']:>6,}"
          f"   (window {r['window']['days']} days from {r['window']['start']})"
          if r.get("window") else "  No resolution window"),
         f"                       naive pairs {f['naive_market_pairs']:,}",
         f"  Futures in map       Kalshi {fu['in_map']['KALSHI']:>6,}   Polymarket US {fu['in_map']['POLYUS']:>6,}"
         f"   event pairs {fu['event_pairs']}   market pairs {fu['market_pairs']}",
         f"  Game markets         Kalshi {ga['markets']['KALSHI']:>6,}   Polymarket US {ga['markets']['POLYUS']:>6,}"
         f"   games K/P {ga['games']['KALSHI']}/{ga['games']['POLYUS']}   game pairs {ga['game_pairs']}"
         f"   market pairs {ga['market_pairs']}",
         *([f"  Bet filter           keep {[f'{per}/{b}' for per, b in r['bet_filter']['bets']]}: "
            f"{r['bet_filter']['pairs_before']:,} matched pairs -> "
            f"{r['bet_filter']['pairs_before'] - sum(r['bet_filter']['dropped_by_map_key'].values()):,} kept; dropped "
            f"{_top(r['bet_filter']['dropped_by_map_key'], 6)}"]
           if r.get("bet_filter") and r["bet_filter"]["bets"] is not None else []),
         f"  Total market pairs   {f['market_pairs']:,}  ({f['polyus_markets_paired']} Polymarket US markets paired)"
         f"   {f['by_direction']}",
         f"  Status               {f['by_status']}",
         f"  Template pairs       {f['template_pairs']} ({f['template_pairs_to_review']} need review)", "",
         f"  {'futures row':20s} {'K':>4} {'P':>4} {'pairs':>5}  templates K/P  Polymarket US unpaired"]
    for x in r["futures"]:
        L.append(f"  {x['key']:20s} {x['kalshi_markets']:4d} {x['polyus_markets']:4d} {x['market_pairs']:5d}"
                 f"  {x['kalshi_templates']:>4}/{x['polyus_templates']:<4}     {', '.join(x['polyus_unpaired'][:5])}")
    if r.get("window"):
        W = r["window"]
        L += ["", f"  Close window: pairs whose earlier close is by {W['end']} (markets pre-filtered with a "
                  f"{W.get('prefilter_margin_days', 0)}-day margin)"] + [
            f"    {pl}: kept {w['kept']}, dropped {w['dropped_closes_later']} closing later, "
            f"{w['dropped_already_closed']} already closed, {w['dropped_no_close_time']} with no close time"
            for pl, w in ((pl, W[pl]) for pl in ("KALSHI", "POLYUS"))] + [
            f"    pairs dropped because both closes are after the window: {W.get('pairs_dropped_close_after_window', 0)}"]
    L += ["", "  Games",
          f"    Polymarket US games with no Kalshi game: {len(g['unpaired_polyus_games'])}"]
    L += [f"      {u['slug']} ({u['markets']} markets): {u['reason']}" for u in g["unpaired_polyus_games"][:10]]
    L += [f"    Kalshi games with no Polymarket US game: {g['kalshi_games_unpaired']}",
          f"    Unknown bet types (add to config):  Kalshi: {_top(g['unknown_bet_types']['KALSHI'])}",
          f"                                        Polymarket US: {_top(g['unknown_bet_types']['POLYUS'])}",
          *([f"    Recognised, not matched yet:        Kalshi: {_top(g['recognised_not_matched_yet']['KALSHI'])}",
             f"                                        Polymarket US: {_top(g['recognised_not_matched_yet']['POLYUS'])}"]
            if "recognised_not_matched_yet" in g else
            [f"    Recognised, not matched yet:        {_top(g['polyus_recognised_not_matched_yet'])}"]),
          *([f"    Kalshi games grouped by:            {g['kalshi_grouped_by']}"] if "kalshi_grouped_by" in g else []),
          f"    Polymarket US, no Kalshi market at the same line: {_top(g['polyus_no_kalshi_market_at_same_line'])}",
          f"    Unusable (no team/line/side):       {_top(g['unusable_markets'])}",
          f"    Unknown leagues (add to config):    Kalshi: {_top(g['unknown_leagues']['KALSHI'])}"
          f"   Polymarket US: {_top(g['unknown_leagues']['POLYUS'])}",
          "", "  Not covered by futures or games (no counterpart, or a new futures series to add):"]
    for plat, d in r["not_covered"].items():
        L.append(f"    {plat}: {len(d)} groups, {sum(d.values())} markets. {_top(d, 12)}")
    if r["notes"]:
        L += ["", "  Notes:"] + [f"    - {n}" for n in r["notes"][:30]]
        if len(r["notes"]) > 30:
            L.append(f"    … {len(r['notes']) - 30} more in report.json")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(prog="candidates")
    ap.add_argument("category", choices=["sports"])
    ap.add_argument("subcategory")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--open", action="store_true", help="open the CSV in the default spreadsheet app (macOS)")
    a = ap.parse_args()
    mod = importlib.import_module(f"candidates.{a.category}.{a.subcategory.replace('/', '.')}.match")
    _, _, report = mod.run(write=not a.no_write)
    print(format_report(report))
    if report.get("csv"):
        print(f"\n  CSV: {report['csv']}")
        if a.open:
            import subprocess
            subprocess.run(["open", report["csv"]], check=False)


if __name__ == "__main__":
    main()
