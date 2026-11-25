"""Run one football league (nfl/ or cfb/): load, close window, futures, games, status, outputs, report.

Each league folder has a config.py (LEAGUE, FUTURES, CLOSES_WITHIN_DAYS, GAME_START_TOLERANCE_MIN,
team_aliases(), APPROVALS, OUT_NAME) and a match.py that calls run_league().
"""
import pandas as pd

from candidates import common as C
from collector.util import now_ms

from . import futures, games

_DEFAULT = object()


def league_rows(df, platform, cfg):
    """Only this league's markets: Kalshi competition, Polymarket US series slug prefix."""
    if platform == "KALSHI":
        return df[df.competition == cfg.LEAGUE["kalshi_competition"]]
    pre = cfg.LEAGUE["polyus_prefix"]
    return df[df.series_id.str.startswith(pre + "-", na=False) | df.event_slug.str.match(rf"^(?:aec-)?{pre}-", na=False)]


def run_league(cfg, write=True, frames=None, approvals=None, window_days=_DEFAULT, now=None):
    """frames=(kalshi_df, polyus_df, kalshi_as_of, polyus_as_of): run on given rows (football rows of both
    platforms) instead of the catalog. now: the time the close window starts (default: current time)."""
    if frames is None:
        k, k_asof = C.load_open("KALSHI", "football")
        p, p_asof = C.load_open("POLYUS", "football")
    else:
        k, p, k_asof, p_asof = frames[0], frames[1], frames[2], frames[3]
    notes = []
    if len(k) and k.competition.isna().all():
        notes.append("Kalshi catalog predates the competition field: leagues cannot be separated. "
                     "Run `python -m collector catalog`.")
    k = league_rows(k, "KALSHI", cfg).reset_index(drop=True).copy()
    p = league_rows(p, "POLYUS", cfg).reset_index(drop=True).copy()
    open_counts = {"KALSHI": len(k), "POLYUS": len(p)}

    window_days = cfg.CLOSES_WITHIN_DAYS if window_days is _DEFAULT else window_days
    now = now_ms() if now is None else now
    window = None
    end = None
    if window_days is not None:
        # pre-filter with a margin, then keep whole pairs by their earlier close (C.pair_window)
        k, kw = C.close_window(k, "KALSHI", now, window_days + C.WINDOW_SLACK_DAYS)
        p, pw = C.close_window(p, "POLYUS", now, window_days + C.WINDOW_SLACK_DAYS)
        k, p = k.reset_index(drop=True), p.reset_index(drop=True)
        end = now + int(window_days * 86_400_000)
        window = dict(days=window_days, start=_iso(now), end=_iso(end), prefilter_margin_days=C.WINDOW_SLACK_DAYS,
                      KALSHI=kw, POLYUS=pw)
    C.add_templates(k, p)
    approvals = C.load_approvals(cfg.APPROVALS) if approvals is None else approvals

    f_pairs, f_report, k_fut, p_fut = futures.match(cfg.FUTURES, k, p, notes)
    g_pairs, g_report, k_game, p_game = games.match(cfg, k, p, notes)
    pairs, dropped = C.pair_window(pd.DataFrame(f_pairs + g_pairs), k, p, now, window_days)
    if window:
        window["pairs_dropped_close_after_window"] = dropped
        p_close = dict(zip(p.event_slug, C.market_close_ms(p, "POLYUS")))
        g_report["unpaired_polyus_games"] = [u for u in g_report["unpaired_polyus_games"]
                                             if p_close.get(u["slug"], 0) <= end]
    pairs, bet_filter = C.bet_filter(pairs, getattr(cfg, "BETS", None), cfg.LEAGUE["name"])
    out, templates = _outputs(pairs, approvals, min(k_asof, p_asof), cfg)

    k_un, p_un = k.drop(index=list(k_fut | k_game)), p.drop(index=list(p_fut | p_game))
    report = dict(
        subcategory=f"sports/football/{cfg.OUT_NAME}",
        catalog_pulled={"KALSHI": _iso(k_asof), "POLYUS": _iso(p_asof)},
        window=window,
        bet_filter=bet_filter,
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
        out_dir = C.OUT / "sports" / "football" / cfg.OUT_NAME
        C.write_parquet(out, C.CANDIDATE_PAIRS, out_dir / "candidate_pairs.parquet")
        C.write_parquet(templates, C.TEMPLATE_PAIRS, out_dir / "template_pairs.parquet")
        report["csv"] = str(C.write_csv(out, C.csv_path(out_dir, min(k_asof, p_asof))))
        C.write_json(report, out_dir / "report.json")
    return out, templates, report


def _outputs(pairs, approvals, as_of, cfg):
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
        "category": "sports", "subcategory": f"football/{cfg.OUT_NAME}", "league": pairs.league,
        "map_key": pairs.map_key, "shape": pairs["shape"], "match_key": pairs.match_key, "direction": pairs.direction,
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
