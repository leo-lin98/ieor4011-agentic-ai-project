"""Baseball candidate pairs. How the filtering works: FILTERING.md.

    python -m candidates sports baseball
"""
import pandas as pd

from candidates import common as C
from collector.util import now_ms

from . import config, futures, games

SPORT = "baseball"
OUT = C.OUT / "sports" / SPORT


_DEFAULT = object()


def run(write=True, frames=None, approvals=None, window_days=_DEFAULT, now=None):
    """frames=(kalshi_df, polyus_df, kalshi_as_of, polyus_as_of) runs on given rows instead of the catalog (selftest).

    window_days: keep only markets that close within this many days of `now` (default
    config.CLOSES_WITHIN_DAYS; None = no window). now defaults to the current time.
    """
    if frames is None:
        k, k_asof = C.load_open("KALSHI", SPORT)
        p, p_asof = C.load_open("POLYUS", SPORT)
    else:
        k, p = frames[0].reset_index(drop=True).copy(), frames[1].reset_index(drop=True).copy()
        k_asof, p_asof = frames[2], frames[3]
    open_counts = {"KALSHI": len(k), "POLYUS": len(p)}
    window_days = config.CLOSES_WITHIN_DAYS if window_days is _DEFAULT else window_days
    now = now_ms() if now is None else now
    window, end = None, None
    if window_days is not None:
        # pre-filter with a margin, then keep whole pairs by their earlier close (C.pair_window)
        k, kw = C.close_window(k, "KALSHI", now, window_days + C.WINDOW_SLACK_DAYS)
        p, pw = C.close_window(p, "POLYUS", now, window_days + C.WINDOW_SLACK_DAYS)
        k, p = k.reset_index(drop=True), p.reset_index(drop=True)
        end = now + int(window_days * 86_400_000)
        window = dict(days=window_days, start=_iso(now), end=_iso(end), prefilter_margin_days=C.WINDOW_SLACK_DAYS,
                      KALSHI=kw, POLYUS=pw)
    C.add_templates(k, p)
    approvals = C.load_approvals(config.APPROVALS) if approvals is None else approvals
    notes = []
    stale = [pl for pl, df, col in (("KALSHI", k, "competition"), ("POLYUS", p, "sports_market_type"))
             if len(df) and df[col].isna().all()]
    if stale:
        notes.append(f"Catalog for {stale} predates the bet-type fields, so games cannot be matched. "
                     "Run `python -m collector catalog`, then re-run this.")

    f_pairs, f_report, k_fut, p_fut = futures.match(config.FUTURES, k, p, notes)
    g_pairs, g_report, k_game, p_game = games.match(config, k, p, notes)
    pairs, dropped = C.pair_window(pd.DataFrame(f_pairs + g_pairs), k, p, now, window_days)
    if window:
        window["pairs_dropped_close_after_window"] = dropped
        p_close = dict(zip(p.event_slug, C.market_close_ms(p, "POLYUS")))
        g_report["unpaired_polyus_games"] = [u for u in g_report["unpaired_polyus_games"]
                                             if p_close.get(u["slug"], 0) <= end]
    out, templates = _outputs(pairs, approvals, min(k_asof, p_asof))

    k_un, p_un = k.drop(index=list(k_fut | k_game)), p.drop(index=list(p_fut | p_game))
    report = dict(
        subcategory=f"sports/{SPORT}",
        catalog_pulled={"KALSHI": _iso(k_asof), "POLYUS": _iso(p_asof)},
        window=window,
        funnel=dict(
            open_markets=open_counts,
            in_window={"KALSHI": len(k), "POLYUS": len(p)},
            naive_market_pairs=len(k) * len(p),
            futures=dict(in_map={"KALSHI": len(k_fut), "POLYUS": len(p_fut)},
                         event_pairs=sum(r["event_pairs"] for r in f_report), market_pairs=len(f_pairs)),
            games=dict(markets={"KALSHI": g_report["kalshi_game_markets"], "POLYUS": g_report["polyus_game_markets"]},
                       games={"KALSHI": g_report["kalshi_games"], "POLYUS": g_report["polyus_games"]},
                       game_pairs=g_report["game_pairs"], market_pairs=len(g_pairs)),
            market_pairs=len(out),
            polyus_markets_paired=int(out.polyus_market_uid.nunique()) if len(out) else 0,
            by_direction=out.direction.value_counts().to_dict() if len(out) else {},
            by_status=out.status.value_counts().to_dict() if len(out) else {},
            template_pairs=len(templates),
            template_pairs_to_review=int((templates.status == "needs_review").sum()) if len(templates) else 0,
        ),
        futures=f_report,
        games={x: v for x, v in g_report.items()
               if x not in ("kalshi_games", "polyus_games", "game_pairs", "market_pairs",
                            "kalshi_game_markets", "polyus_game_markets")},
        not_covered={
            "KALSHI_series": k_un.series_id.value_counts().to_dict(),
            "POLYUS_events": p_un.assign(g=p_un.event_slug.str.replace(r"\d{4}-\d{2}-\d{2}", "<date>", regex=True))
                                 .g.value_counts().to_dict(),
        },
        notes=notes,
    )
    if write:
        C.write_parquet(out, C.CANDIDATE_PAIRS, OUT / "candidate_pairs.parquet")
        C.write_parquet(templates, C.TEMPLATE_PAIRS, OUT / "template_pairs.parquet")
        report["csv"] = str(C.write_csv(out, C.csv_path(OUT, min(k_asof, p_asof))))
        C.write_json(report, OUT / "report.json")
    return out, templates, report


def _outputs(pairs, approvals, as_of):
    if not len(pairs):
        return pd.DataFrame(columns=C.CANDIDATE_PAIRS.names), pd.DataFrame(columns=C.TEMPLATE_PAIRS.names)
    pairs["template_pair_id"] = [C.template_pair_id(a, b) for a, b in zip(pairs.template_k, pairs.template_p)]

    def status(tp, direction):
        a = approvals.get(tp)
        if not a:
            return "needs_template_review"
        if a.get("status") != "approved" or (direction == "opposite" and not a.get("opposite_side_ok")):
            return "template_rejected"
        return "auto_confirmed"

    pairs["status"] = [status(tp, d) for tp, d in zip(pairs.template_pair_id, pairs.direction)]
    out = pd.DataFrame({
        "pair_id": pairs.market_uid_k + "__" + pairs.market_uid_p,
        "kalshi_market_uid": pairs.market_uid_k, "polyus_market_uid": pairs.market_uid_p,
        "kalshi_title": pairs.title_k, "polyus_title": pairs.title_p,
        "kalshi_event_uid": pairs.event_uid_k, "polyus_event_uid": pairs.event_uid_p,
        "category": "sports", "subcategory": SPORT, "league": pairs.league, "map_key": pairs.map_key,
        "shape": pairs["shape"], "match_key": pairs.match_key, "direction": pairs.direction,
        "kalshi_yes": pairs.kalshi_yes, "polyus_yes": pairs.polyus_yes,
        "event_time_ms": pd.array(pairs.event_time_ms, dtype="Int64"),
        "pair_close_ms": pd.array(pairs.pair_close_ms, dtype="Int64"),
        "start_diff_min": pd.to_numeric(pairs.start_diff_min, errors="coerce"),
        "template_pair_id": pairs.template_pair_id, "status": pairs.status,
        "kalshi_rules_hash": pairs.rules_hash_k, "polyus_rules_hash": pairs.rules_hash_p,
        "as_of_ms": as_of, "generated_ms": now_ms(),
    })
    first = pairs.drop_duplicates("template_pair_id")
    n = pairs.template_pair_id.value_counts()
    join = lambda *xs: "\n\n".join(x for x in xs if isinstance(x, str) and x)  # noqa: E731
    templates = pd.DataFrame({
        "template_pair_id": first.template_pair_id, "map_key": first.map_key, "league": first.league,
        "shape": first["shape"], "kalshi_template": first.template_k, "polyus_template": first.template_p,
        "kalshi_example": [join(a, b) for a, b in zip(first.rules_primary_k, first.rules_secondary_k)],
        "polyus_example": [join(a, b) for a, b in zip(first.description_p, first.rules_disclaimer_p)],
        "n_pairs": first.template_pair_id.map(n).astype(int),
        "status": [(approvals.get(t) or {}).get("status", "needs_review") for t in first.template_pair_id],
    })
    return out, templates


def _iso(ms):
    return pd.to_datetime(ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%MZ")
