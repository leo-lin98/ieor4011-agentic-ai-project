"""Load the catalogs and candidate pairs into two DataFrames the API serves from."""
import re

import pandas as pd
import pyarrow.parquet as pq

from candidates.sports.sport_map import sport_of
from collector.config import DATA
from collector.util import now_ms

# Rules text is NOT loaded into the markets frame (it is most of the memory); pair_detail reads it per market.
CATALOG_COLS = [
    "market_uid", "event_uid", "platform", "native_event_id", "market_slug", "event_slug", "series_id",
    "event_title", "market_title", "market_question", "yes_title", "yes_side_label", "no_side_label",
    "category", "subcategory", "tags", "competition", "competition_scope", "sports_market_type", "line",
    "open_time_ms", "close_time_ms", "event_end_ms", "game_start_ms", "status_canonical", "market_url",
    "last_seen_ms",
]
RULES_COLS = ["market_uid", "rules_primary", "rules_secondary", "description", "rules_disclaimer"]
WINDOW_DAYS = 7


def _read(platform):
    path = DATA / "market_catalog" / "current" / f"{platform}.parquet"
    have = set(pq.read_schema(path).names)
    df = pd.read_parquet(path, columns=[c for c in CATALOG_COLS if c in have])
    for c in CATALOG_COLS:
        if c not in df.columns:
            df[c] = None
    as_of = int(df.last_seen_ms.max())
    df = df[(df.last_seen_ms == as_of) & (df.status_canonical == "open")]
    df = df[df.close_time_ms.isna() | (df.close_time_ms > as_of)]
    return df.reset_index(drop=True), as_of


# Kalshi event competition -> the Polymarket US slug prefix, so one league code covers both platforms.
KALSHI_LEAGUE = {
    "Pro Football": "nfl", "NCAA Football": "cfb",
    "Pro Basketball (M)": "nba", "College Basketball (M)": "cbb", "College Basketball (W)": "wcbb",
    "Pro Basketball (W)": "wnba", "Pro Baseball": "mlb", "Korea KBO": "kbo", "Pro Hockey": "nhl",
}


def _league(df):
    """One league code on both platforms: Kalshi competition mapped through KALSHI_LEAGUE (unmapped
    competitions keep their Kalshi name); Polymarket US event slug prefix (nba, nfl, cfb, ...). Sports only."""
    k = df.platform == "KALSHI"
    out = pd.Series(None, index=df.index, dtype=object)
    comp = df.loc[k, "competition"]
    out[k] = comp.map(KALSHI_LEAGUE).fillna(comp)
    pre = df.loc[~k, "event_slug"].str.extract(r"^(?:aec-)?([a-z0-9]+)-", expand=False)
    out[~k] = pre.where(df.loc[~k, "category"].str.lower() == "sports")
    return out


def load_markets():
    """One row per open market on both platforms, unified columns. Returns (df, {platform: as_of_ms})."""
    frames, as_of = [], {}
    for plat in ("KALSHI", "POLYUS"):
        df, as_of[plat] = _read(plat)
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    k = df.platform == "KALSHI"
    df["market"] = df.market_title.where(k, df.market_question.fillna(df.market_title))
    df["yes"] = df.yes_title.where(k, df.yes_side_label)
    df["bet_type"] = df.competition_scope.where(k, df.sports_market_type)
    df["sport"] = [sport_of(p, c, s, t, sid) for p, c, s, t, sid
                   in zip(df.platform, df.category, df.subcategory, df.tags, df.series_id)]
    df["league"] = _league(df)
    close = pd.to_numeric(df.close_time_ms)
    df["close_ms"] = close.where(k, pd.to_numeric(df.event_end_ms).fillna(close))
    now = now_ms()
    df["in_window"] = (df.close_ms >= now) & (df.close_ms <= now + WINDOW_DAYS * 86_400_000)
    df["category"] = df.category.fillna("(none)")
    for c in ("category", "sport", "league", "bet_type", "platform", "status_canonical"):
        df[c] = df[c].astype("category")   # repeated strings -> small codes
    return df, as_of


def rules_for(uid):
    """Rules text for one market, read from the catalog on demand."""
    import pyarrow.compute as pc

    plat = uid.split(":", 1)[0]
    t = pq.read_table(DATA / "market_catalog" / "current" / f"{plat}.parquet", columns=RULES_COLS,
                      filters=[("market_uid", "==", uid)])
    if t.num_rows == 0:
        return ""
    r = t.slice(0, 1).to_pylist()[0]
    parts = (r["rules_primary"], r["rules_secondary"]) if plat == "KALSHI" else (r["description"], r["rules_disclaimer"])
    return "\n\n".join(x for x in parts if isinstance(x, str) and x.strip())


def load_pairs(name="candidate_pairs"):
    """Every <name>.parquet under data/candidate_pairs, concatenated, with its subcategory.

    candidate_pairs: the enabled bet types (what flows to T2). template_examples: one pair per template x
    direction for every matched bet type (what the judge saw)."""
    root = DATA / "candidate_pairs"
    frames = []
    for path in sorted(root.glob(f"**/{name}.parquet")):
        sub = str(path.parent.relative_to(root))
        d = pd.read_parquet(path)
        d["source"] = sub
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_templates():
    """Template pairs from every candidate set, joined with the judge's verdict from that set's approvals file."""
    import json

    from collector.config import ROOT
    root = DATA / "candidate_pairs"
    rows = []
    for path in sorted(root.glob("**/template_pairs.parquet")):
        source = str(path.parent.relative_to(root))
        appr_path = ROOT / "candidates" / source / "template_approvals.json"
        approvals = json.loads(appr_path.read_text()) if appr_path.exists() else {}
        t = pd.read_parquet(path)
        for r in t.itertuples():
            v = approvals.get(r.template_pair_id) or {}
            rows.append({
                "template_pair_id": r.template_pair_id, "source": source, "league": r.league, "map_key": r.map_key,
                "shape": r.shape, "n_pairs": int(r.n_pairs),
                "verdict": v.get("verdict") or ("human:" + v["status"] if v.get("status") else "not_judged"),
                "status": v.get("status") or "needs_review", "opposite_side_ok": v.get("opposite_side_ok"),
                "confidence": v.get("confidence"), "reviewed_by": v.get("reviewed_by"),
                "prompt_version": v.get("prompt_version"), "date": v.get("date"),
                "reasoning": v.get("reasoning"), "human_review_question": v.get("human_review_question"),
                "checklist": v.get("checklist") or [], "note": v.get("note"),
                "kalshi_template": r.kalshi_template, "polyus_template": r.polyus_template,
                "kalshi_example": r.kalshi_example, "polyus_example": r.polyus_example,
            })
    return pd.DataFrame(rows)


class Store:
    def __init__(self):
        self.reload()

    def reload(self):
        self.markets, self.as_of = load_markets()
        self.pairs = load_pairs()
        self.examples = load_pairs("template_examples")
        self.templates = load_templates()
        self.by_uid = self.markets.set_index("market_uid", drop=False)
        self.loaded_ms = now_ms()

    # ------------------------------------------------------------ tab 3

    def filter_templates(self, source=None, verdict=None, league=None, q=None):
        t = self.templates
        if not len(t):
            return t
        if source:
            t = t[t.source == source]
        if verdict:
            t = t[t.verdict == verdict]
        if league:
            t = t[t.league == league]
        if q:
            rx = re.escape(q.strip())
            hay = t.map_key.fillna("") + " | " + t.template_pair_id + " | " + t.reasoning.fillna("")
            t = t[hay.str.contains(rx, case=False, regex=True)]
        return t

    def template_detail(self, tpid, source):
        t = self.templates[(self.templates.template_pair_id == tpid) & (self.templates.source == source)]
        if not len(t):
            return None
        d = {k: _py(v) if k != "checklist" else v for k, v in t.iloc[0].items()}
        cols = ["direction", "kalshi_market_uid", "kalshi_title", "kalshi_yes", "polyus_market_uid", "polyus_title",
                "polyus_yes", "event_time_ms"]
        src = self.examples if len(self.examples) else self.pairs
        ex = src[(src.template_pair_id == tpid) & (src.source == source)] if len(src) else src
        d["examples"] = records(ex.groupby("direction").head(1), cols) if len(ex) else []
        enabled = self.pairs[(self.pairs.template_pair_id == tpid) & (self.pairs.source == source)] if len(self.pairs) else self.pairs
        d["enabled_pairs"] = int(len(enabled))
        d["status_counts"] = enabled.status.value_counts().to_dict() if len(enabled) else {}
        return d

    # ------------------------------------------------------------ tabs 4-5: live monitor and paper trading (files written by `python -m arb`)

    @staticmethod
    def live():
        import json
        p = DATA / "arb" / "live.json"
        if not p.exists():
            return {"running": False, "rows": []}
        d = json.loads(p.read_text())
        d["running"] = (now_ms() - d.get("ts_ms", 0)) < 10_000
        d["age_ms"] = now_ms() - d.get("ts_ms", 0)
        return d

    @staticmethod
    def paper(limit=500):
        import json
        base = DATA / "paper"
        out = {"state": None, "trades": [], "edges_recent": []}
        if (base / "state.json").exists():
            out["state"] = json.loads((base / "state.json").read_text())
        if (base / "trades.jsonl").exists():
            lines = (base / "trades.jsonl").read_text().splitlines()
            out["trades"] = [json.loads(l) for l in lines if l.strip()][::-1][:limit]
            out["trades_total"] = len(lines)
        if (base / "edges.jsonl").exists():
            lines = (base / "edges.jsonl").read_text().splitlines()[-2000:]
            out["edges_recent"] = [json.loads(l) for l in lines if l.strip()][::-1][:200]
            out["edges_total"] = len(lines)
        return out

    # ------------------------------------------------------------ tab 1

    def filter_markets(self, platform=None, category=None, sport=None, league=None, bet=None, q=None, window=False):
        m = self.markets
        if platform:
            m = m[m.platform == platform]
        if category:
            m = m[m.category == category]
        if sport:
            m = m[m.sport == sport]
        if league:   # one code, or a comma-separated list ("nfl,nba")
            m = m[m.league.isin(league.split(","))]
        if bet:
            m = m[m.bet_type == bet]
        if window:
            m = m[m.in_window]
        if q:
            rx = re.escape(q.strip())
            hay = m.event_title.fillna("") + " | " + m.market.fillna("") + " | " + m.yes.fillna("") + " | " + m.market_uid
            m = m[hay.str.contains(rx, case=False, regex=True)]
        return m

    def market_options(self, m):
        """Distinct filter values within the current selection (so the dropdowns narrow as you filter)."""
        def opts(col):
            vc = m[col].value_counts()
            return [{"v": str(k), "n": int(n)} for k, n in vc.items() if k is not None and k == k]
        return {"category": opts("category"), "sport": opts("sport"), "league": opts("league"), "bet_type": opts("bet_type")}

    # ------------------------------------------------------------ tab 2

    def filter_pairs(self, source=None, league=None, direction=None, status=None, map_key=None, q=None):
        p = self.pairs
        if not len(p):
            return p
        if source:
            p = p[p.source == source]
        if league:
            p = p[p.league == league]
        if direction:
            p = p[p.direction == direction]
        if status:
            p = p[p.status == status]
        if map_key:
            p = p[p.map_key == map_key]
        if q:
            rx = re.escape(q.strip())
            hay = (p.kalshi_title.fillna("") + " | " + p.polyus_title.fillna("") + " | " + p.match_key.fillna("")
                   + " | " + p.pair_id)
            p = p[hay.str.contains(rx, case=False, regex=True)]
        return p

    def pair_detail(self, pair_id):
        row = self.pairs[self.pairs.pair_id == pair_id]
        if not len(row):
            return None
        r = row.iloc[0]
        out = {k: _py(v) for k, v in r.items()}
        for side, uid in (("kalshi", r.kalshi_market_uid), ("polyus", r.polyus_market_uid)):
            if uid in self.by_uid.index:
                m = self.by_uid.loc[uid]
                out[side] = {
                    "event_title": _py(m.event_title), "market": _py(m.market), "yes": _py(m.yes),
                    "no": _py(m.no_side_label), "bet_type": _py(m.bet_type), "line": _py(m.line),
                    "close_ms": _py(m.close_ms), "game_start_ms": _py(m.game_start_ms), "url": _py(m.market_url),
                    "rules": rules_for(uid),
                }
            else:
                out[side] = None
        return out


def _py(v):
    if v is None or (isinstance(v, float) and v != v) or v is pd.NaT:
        return None
    if hasattr(v, "item"):
        try:
            v = v.item()
        except (ValueError, TypeError):
            pass
    if isinstance(v, float) and v != v:
        return None
    return v


def records(df, cols):
    out = []
    for r in df[cols].itertuples(index=False):
        out.append({c: _py(v) for c, v in zip(cols, r)})
    return out
