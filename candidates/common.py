"""Shared helpers for candidate generation (DECISIONS.md D10).

Every category folder uses these to load open markets, normalise names, turn rules text
into templates, and write its outputs to data/candidate_pairs/<category>/<subcategory>/.
"""
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from collector.config import DATA
from collector.util import sha

from .sports.sport_map import sport_of

OUT = DATA / "candidate_pairs"
ET = ZoneInfo("America/New_York")

CATALOG_COLS = [
    "market_uid", "event_uid", "platform", "native_market_id", "native_event_id", "market_slug", "event_slug",
    "series_id", "event_title", "market_title", "market_question", "yes_title", "no_title",
    "yes_side_label", "no_side_label", "market_type", "category", "subcategory", "tags",
    "competition", "competition_scope", "sports_market_type", "line", "yes_team_code", "no_team_code", "event_teams",
    "game_id", "floor_strike", "strike_type", "game_start_ms", "close_time_ms", "status_canonical",
    "expected_expiration_ms", "event_end_ms",
    "rules_primary", "rules_secondary", "description", "rules_disclaimer", "rules_hash", "last_seen_ms",
]
NEW_FIELDS = ("competition", "sports_market_type")  # missing in catalogs pulled before 2026-11-17


# ---------------------------------------------------------------- loading

def read_catalog_df(platform):
    """T1 as a DataFrame with every CATALOG_COLS column (columns an older catalog lacks are null)."""
    path = DATA / "market_catalog" / "current" / f"{platform}.parquet"
    have = set(pq.read_schema(path).names)
    df = pd.read_parquet(path, columns=[c for c in CATALOG_COLS if c in have])
    for c in CATALOG_COLS:
        if c not in df.columns:
            df[c] = None
    return df[CATALOG_COLS]


def load_open(platform, sport):
    """Open markets of one sport from the latest catalog pull (T1).

    Kept: markets returned by the most recent catalog refresh (`last_seen_ms` equals the latest pull;
    the catalog also carries markets that have since left the open list), with canonical status
    `open` and a close time after that pull.
    Returns (DataFrame, as_of_ms) where as_of_ms is that pull's time.
    """
    df = read_catalog_df(platform)
    as_of = int(df.last_seen_ms.max())
    df = df[(df.last_seen_ms == as_of) & (df.status_canonical == "open")]
    df = df[df.close_time_ms.isna() | (df.close_time_ms > as_of)]
    keep = [sport_of(platform, *r) == sport
            for r in df[["category", "subcategory", "tags", "series_id"]].itertuples(index=False)]
    return df[keep].reset_index(drop=True), as_of


# ---------------------------------------------------------------- close window

def market_close_ms(df, platform):
    """Each market's close (UTC epoch ms), as used by the close window.

    Kalshi: close_time (games: 48 hours after the scheduled start).
    Polymarket US: the event's endDate (games: end of the game day). The market's own endDate is padded
      about two weeks after a game, so it would push every game out of the window while it is being played.
      Falls back to the market endDate when the event has none.
    """
    if platform == "KALSHI":
        return pd.to_numeric(df.close_time_ms)
    return pd.to_numeric(df.event_end_ms).fillna(pd.to_numeric(df.close_time_ms))


# Kalshi game markets close 48 h after the start; Polymarket US games at the end of the game day. Markets are
# first pre-filtered with this extra margin, then each PAIR is kept if its earlier close is inside the window,
# so a game is in or out on both platforms together.
WINDOW_SLACK_DAYS = 3


def pair_window(pairs, k, p, now_ms_, days):
    """Keep pairs whose earlier close (Kalshi vs Polymarket US) is within `days` of now. Adds pair_close_ms."""
    if not len(pairs):
        return pairs, 0
    kc = pd.Series(market_close_ms(k, "KALSHI").values, index=k.market_uid.values)
    pc = pd.Series(market_close_ms(p, "POLYUS").values, index=p.market_uid.values)
    close = pd.concat([pairs.market_uid_k.map(kc), pairs.market_uid_p.map(pc)], axis=1).min(axis=1)
    keep = close.notna() if days is None else close <= now_ms_ + int(days * 86_400_000)
    if days is None:
        return pairs.assign(pair_close_ms=close.astype("Int64")), 0
    return pairs[keep].assign(pair_close_ms=close[keep].astype("int64")), int((~keep).sum())


def bet_filter(pairs, bets, league):
    """Keep only pairs whose (period, bet) is in `bets` (a league config's BETS). None keeps everything.

    Pairs carry map_key = f"{league}_{period}_{bet}". Returns (kept pairs, report dict with what was dropped
    by map_key), so the run report still shows every bet type the matcher found.
    """
    if bets is None or not len(pairs):
        return pairs, dict(bets=None if bets is None else sorted(bets), pairs_before=int(len(pairs)), dropped_by_map_key={})
    allowed = {f"{league.lower()}_{per}_{b}" for per, b in bets}
    keep = pairs.map_key.isin(allowed)
    dropped = pairs[~keep].map_key.value_counts().to_dict()
    return pairs[keep].reset_index(drop=True), dict(bets=sorted(bets), pairs_before=int(len(pairs)),
                                                    dropped_by_map_key=dropped)


def close_window(df, platform, now_ms_, days):
    """Keep markets that close within `days` of now (and have not closed yet).

    Returns (kept, counts)."""
    close = market_close_ms(df, platform)
    end_ms = now_ms_ + int(days * 86_400_000)
    keep = (close >= now_ms_) & (close <= end_ms)
    counts = {"kept": int(keep.sum()), "dropped_closes_later": int((close > end_ms).sum()),
              "dropped_already_closed": int((close < now_ms_).sum()), "dropped_no_close_time": int(close.isna().sum())}
    return df[keep], counts


# ---------------------------------------------------------------- names and times

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b")


def norm_name(s):
    """'Edwin Díaz Jr.' -> 'edwin diaz'. Accents folded, punctuation and suffixes removed."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[-–/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = _SUFFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip() or None


_MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def kalshi_et_to_ms(yy, mon, dd, hhmm):
    """Kalshi game tickers carry the scheduled start in US Eastern time: 26NOV060430 -> Nov 6 2026 04:30 ET."""
    dt = datetime(2000 + int(yy), _MON[mon], int(dd), int(hhmm[:2]), int(hhmm[2:]), tzinfo=ET)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------- rules templates

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
_DATE = re.compile(rf"\b(?:{_MONTHS})[a-z]*\.? \d{{1,2}}(?:\s*[-–]\s*\d{{1,2}})?,? \d{{4}}")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?(?:\s*(?:ET|EST|EDT|UTC|PT|CT)\b)?")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def rules_template(text, names):
    """Rules text with the market-specific parts replaced, so every market in a series shares one template.

    Names (players, teams) become {X}; a name's preceding capitalised words are absorbed too, so
    'the Los Angeles Dodgers' and 'the Seattle Mariners' give the same template. Dates, clock times
    and numbers become {DATE}, {TIME}, {N}.
    """
    t = text or ""
    for n in sorted({x.strip() for x in names if isinstance(x, str) and x.strip()}, key=len, reverse=True):
        t = re.sub(r"(?:\b[A-Z][\w.'&]*\s){0,3}" + re.escape(n), "{X}", t)
    t = _DATE.sub("{DATE}", t)
    t = _TIME.sub("{TIME}", t)
    t = _NUM.sub("{N}", t)
    return re.sub(r"\s+", " ", t).strip()


def kalshi_rules(r):
    return "\n\n".join(x for x in (r.rules_primary, r.rules_secondary) if isinstance(x, str) and x)


def polyus_rules(r):
    return "\n\n".join(x for x in (r.description, r.rules_disclaimer) if isinstance(x, str) and x)


_GAME_PREFIX = re.compile(r"^Game \d+:\s*")


def title_teams(title):
    """'Game 5: Milwaukee vs Tampa Bay: Spread' -> ['Milwaukee', 'Tampa Bay']."""
    t = _GAME_PREFIX.sub("", title or "")
    t = t.split(":", 1)[0]
    parts = re.split(r"\s+vs\.?\s+", t)
    return [x.strip() for x in parts] if len(parts) == 2 else []


def _team_names(event_teams):
    try:
        teams = json.loads(event_teams) if isinstance(event_teams, str) else []
    except ValueError:
        return []
    return [t.get(k) for t in teams if isinstance(t, dict) for k in ("name", "display")]


_NOT_NAMES = {"yes", "no", "over", "under"}


def _names(*vals):
    return [v for v in vals if isinstance(v, str) and v.strip() and v.strip().lower() not in _NOT_NAMES
            and not re.fullmatch(r"[+-]?\d+(\.\d+)?", v.strip())]


_P_SUBJECT = re.compile(r"^Will (?:the )?(.+?) (?:record|throw|score|win|cover|not score)\b")


def _kalshi_subject(t):
    """'Mark Andrews: 10+' -> 'Mark Andrews' (player props put the player before the colon)."""
    return t.split(":", 1)[0] if isinstance(t, str) and ":" in t else None


def _polyus_subject(q):
    """'Will Zay Flowers record 10+ receiving yards?' -> 'Zay Flowers'."""
    m = _P_SUBJECT.match(q) if isinstance(q, str) else None
    return m.group(1) if m else None


def add_templates(k, p):
    """Add a `template` column to both frames (see rules_template). Players and teams named in a market
    (title, YES/NO labels, player before a colon, 'Will <name> record ...') are replaced by {X}."""
    k["template"] = [rules_template(kalshi_rules(r), _names(r.yes_title, r.no_title, _kalshi_subject(r.yes_title),
                                                            *title_teams(r.event_title)))
                     for r in k.itertuples()]
    p["template"] = [rules_template(polyus_rules(r), _names(r.market_title, r.yes_side_label, r.no_side_label,
                                                            _polyus_subject(r.market_question),
                                                            *title_teams(r.event_title), *_team_names(r.event_teams)))
                     for r in p.itertuples()]


def polyus_title(r):
    """Polymarket US market title: the question, which states what YES pays on.

    For futures the question is just the event ("National League MVP"), so the market's own title
    (the player or team) is appended. It is NOT appended to questions ("...?"): on spread markets the
    title describes the NO side ("Will the Jaguars cover +20.5" has title "Ravens wins by over 20.5").
    """
    q, t = r.market_question, r.market_title
    q = q.strip() if isinstance(q, str) and q.strip() else None
    t = t.strip() if isinstance(t, str) and t.strip() else None
    if q and t and t not in q and "?" not in q:
        return f"{q} ({t})"
    return q or t


def pair_row(kr, pr, **extra):
    """One candidate pair from a Kalshi row and a Polymarket US row (itertuples of the loaded frames)."""
    return dict(
        market_uid_k=kr.market_uid, market_uid_p=pr.market_uid, event_uid_k=kr.event_uid, event_uid_p=pr.event_uid,
        title_k=kr.market_title, title_p=polyus_title(pr),
        template_k=kr.template, template_p=pr.template,
        rules_primary_k=kr.rules_primary, rules_secondary_k=kr.rules_secondary,
        description_p=pr.description, rules_disclaimer_p=pr.rules_disclaimer,
        rules_hash_k=kr.rules_hash, rules_hash_p=pr.rules_hash,
        **{"event_time_ms": None, "start_diff_min": None, **extra})


def template_pair_id(k_template, p_template):
    return sha(k_template, p_template)[:16]


def load_approvals(path):
    """Template-pair approvals: {template_pair_id: {"status": "approved"|"rejected", "opposite_side_ok": bool, ...}}."""
    return json.loads(path.read_text()) if path.exists() else {}


# ---------------------------------------------------------------- outputs

S, I, F, B = pa.string(), pa.int64(), pa.float64(), pa.bool_()

# T7 candidate_pairs: one row per (Kalshi market, Polymarket US market) pair proposed by code.
CANDIDATE_PAIRS = pa.schema([
    ("pair_id", S), ("kalshi_market_uid", S), ("polyus_market_uid", S),
    ("kalshi_title", S), ("polyus_title", S),   # each market's own title, as shown on the platform
    ("kalshi_event_uid", S), ("polyus_event_uid", S),
    ("category", S), ("subcategory", S), ("league", S), ("map_key", S), ("shape", S),
    ("match_key", S),            # the exact key both markets share, e.g. season=2026|player=shohei ohtani
    ("direction", S),            # same: YES on both pays together. opposite: Polymarket US YES = Kalshi NO
    ("kalshi_yes", S), ("polyus_yes", S),
    ("event_time_ms", I),        # game start (games) or null (futures)
    ("pair_close_ms", I),        # the earlier of the two markets' closes (what the close window uses)
    ("start_diff_min", F),       # |Kalshi start - Polymarket US start| in minutes (games)
    ("template_pair_id", S),
    ("status", S),               # auto_confirmed | needs_template_review | template_rejected
    ("kalshi_rules_hash", S), ("polyus_rules_hash", S),
    ("as_of_ms", I), ("generated_ms", I),
])

# One row per pair of rules templates. This is the only thing the LLM / a person has to review.
TEMPLATE_PAIRS = pa.schema([
    ("template_pair_id", S), ("map_key", S), ("league", S), ("shape", S),
    ("kalshi_template", S), ("polyus_template", S),
    ("kalshi_example", S), ("polyus_example", S),
    ("n_pairs", I), ("status", S),
])


def write_parquet(df, schema, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tbl = pa.Table.from_pandas(df, schema=schema, preserve_index=False) if len(df) else schema.empty_table()
    pq.write_table(tbl, tmp, compression="zstd")
    os.replace(tmp, path)


def csv_path(out_dir, as_of_ms):
    """data/candidate_pairs/.../candidate_pairs_<YYYY-MM-DD>.csv, dated by the catalog pull (UTC)."""
    return out_dir / f"candidate_pairs_{datetime.fromtimestamp(as_of_ms / 1000, timezone.utc):%Y-%m-%d}.csv"


def write_csv(df, path):
    """Candidate pairs as CSV for spreadsheets: readable UTC times next to the epoch-ms columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    o = df.copy()
    for col in ("event_time_ms", "pair_close_ms", "generated_ms"):
        if col in o:
            o.insert(o.columns.get_loc(col) + 1, col.replace("_ms", "_utc"),
                     pd.to_datetime(o[col], unit="ms", utc=True).dt.strftime("%Y-%m-%d %H:%M"))
    tmp = path.with_name("." + path.name + ".tmp")
    o.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return path


def write_json(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)
