"""Basketball games (NBA; college later): every bet type in bets.py.

Game recognition
  Kalshi         Each bet type is its own event (KXNBAGAME-, KXNBASPREAD-, KXNBAPTS-26NOV25SASPOR, ...).
                 They are grouped into one game by game_id: the Kalshi sports milestone that lists them
                 (collector, market_catalog.game_id), whose start is game_start_ms (the tip-off Kalshi shows).
                 Basketball tickers carry only the date (26NOV25SASPOR). Without milestones (an older catalog),
                 events are grouped by ticker date + teams and paired on the US Eastern date.
  Polymarket US  One event per game, slug [aec-]nba-TEAM-TEAM-YYYY-MM-DD; tip-off = gameStartTime.
                 Only events with two teams in event_teams count as games: the league-wide daily leader
                 (nba-lead-pts-2026-11-25) has a game-shaped slug but no teams.

Game pairing: same league; tip-offs within GAME_START_TOLERANCE_MIN (or same US Eastern date when a Kalshi
start is unknown); both teams resolved (code, display code, name, team_aliases.json, elimination).
NBA codes differ on both platforms for some teams (Kalshi NYK / Polymarket US ny, PHX / pho, SAS / sa);
those resolve by name ("New York" is the start of "New York Knicks") or by team_aliases.json.

Market pairing: each market becomes (period, bet, subject, line), see bets.py for the kinds.
  subject  team code, "tie", or a player's normalised name
  line     spread / total threshold ("more than X"); for players the N in "N or more"
  Kalshi player thresholds are "greater than X.5" = N or more with N = X.5 + 0.5.
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from candidates import common as C

from . import bets as B

K_GAME = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})?"
                    r"(?P<teams>[A-Z]+)(?:-(?P<n>[A-Z0-9]+))?$")
P_GAME = re.compile(r"^(?:aec-)?(?P<league>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})"
                    r"(?:-(?P<n>\d+))?$")
_TIE = re.compile(r"\bends? (?:in a )?tie(?:d)?\b|^tie$", re.I)       # "end in a tie", "Tie"
_NONE = re.compile(r"\bneither\b", re.I)                                # "neither team reach 120 points" (Kalshi has no such market)
_P_PLAYER_LINE = re.compile(r"^Will (?P<name>.+?) record at least (?P<n>\d+)\b")
_P_PLAYER_YESNO = re.compile(r"^Will (?P<name>.+?) record a (?:double|triple)-double\b")


def _num(x):
    try:
        v = float(str(x).replace("+", ""))
        return None if v != v else round(v, 3)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- teams

def _poly_teams(r):
    """The event's two teams from event_teams. [] when the event is not a game (no team list)."""
    try:
        teams = json.loads(r.event_teams) if isinstance(r.event_teams, str) else []
    except ValueError:
        teams = []
    teams = [t for t in teams if t.get("code")]
    return teams if len(teams) == 2 else []


STRONG = {"alias", "code", "name"}       # exact evidence; "prefix" (Phoenix -> Phoenix Suns) is weak
SCORE = {"alias": 3, "code": 2, "name": 2, "prefix": 1, "elimination": 0}


def resolve_teams(k_codes, k_teamstr, p_teams, alias):
    """Map Kalshi team codes to Polymarket US team codes for one game. Returns ({k: p}, how) or (None, why).

    Evidence per team: alias, code / display code, exact name (strong), or the Kalshi name being the start of
    the Polymarket US name (weak: "Los Angeles" is the start of two teams' names). The second team may be
    resolved by elimination only when the first was resolved by strong evidence.
    """
    pcodes = [t["code"] for t in p_teams]
    if len(pcodes) != 2:
        return None, "Polymarket US teams unknown"
    k_codes = dict(k_codes)
    if len(k_codes) < 2 and k_teamstr:   # no winner market open on Kalshi: split the ticker's team string
        for x, y in ((0, 1), (1, 0)):
            for kind in ("code", "display"):
                a, b = (p_teams[x].get(kind) or ""), (p_teams[y].get(kind) or "")
                if a and b and k_teamstr == (a + b).upper():
                    k_codes = {a.upper(): None, b.upper(): None}
    mapping, how = {}, {}
    for kc, kn in k_codes.items():
        hits = set()
        knorm = C.norm_name(kn) or ""
        for t in p_teams:
            name = C.norm_name(t.get("name")) or ""
            if alias.get(kc) == t["code"]:
                hits.add((t["code"], "alias"))
            elif kc.lower() == t["code"] or kc.upper() == (t.get("display") or "").upper():
                hits.add((t["code"], "code"))
            elif knorm and name and knorm == name:
                hits.add((t["code"], "name"))
            elif knorm and name and name.startswith(knorm + " "):
                hits.add((t["code"], "prefix"))
        codes = {h[0] for h in hits}
        if len(codes) == 1:
            mapping[kc] = codes.pop()
            how[kc] = max((h[1] for h in hits), key=SCORE.get)
    if len(set(mapping.values())) != len(mapping):
        return None, "teams resolve ambiguously"
    left_k = [c for c in k_codes if c not in mapping]
    left_p = [c for c in pcodes if c not in mapping.values()]
    if len(left_k) == 1 and len(left_p) == 1 and any(h in STRONG for h in how.values()):
        mapping[left_k[0]], how[left_k[0]] = left_p[0], "elimination"
    if len(mapping) != 2:
        return None, f"teams not resolved: Kalshi {sorted(k_codes) or k_teamstr} vs Polymarket US {pcodes}"
    return mapping, how


# ---------------------------------------------------------------- propositions

def _k_code(r):
    if isinstance(r.yes_team_code, str):
        return r.yes_team_code
    suffix = (r.native_market_id or "").rsplit("-", 1)[-1]
    return re.sub(r"\d+$", "", suffix) or None


def kalshi_prop(r, bet, mapping):
    per, b = bet
    kd = B.kind(b)
    if kd == "team_pick":
        code, title = _k_code(r), (r.yes_title or "").strip()
        subject = "tie" if code == "TIE" or _TIE.search(title) else mapping.get(code)
        return (per, b, subject, None) if subject else None
    if kd in ("team_line", "game_line"):
        if r.strike_type != "greater" or pd.isna(r.floor_strike):
            return None
        team = mapping.get(_k_code(r)) if kd == "team_line" else None
        if kd == "team_line" and team is None:
            return None
        return (per, b, team, _num(r.floor_strike))
    if kd == "yesno":
        return (per, b, None, None)
    if kd == "player_line":
        x = _num(r.floor_strike)
        if r.strike_type != "greater" or x is None or round(x % 1, 3) != 0.5:
            return None
        name = C.norm_name((r.yes_title or "").split(":")[0])
        return (per, b, name, int(x + 0.5)) if name else None
    if kd == "player_yesno":
        name = C.norm_name(r.yes_title)
        return (per, b, name, None) if name else None
    return None


def polyus_props(r, bet):
    """[(prop, direction)]: direction is how Polymarket US YES relates to the Kalshi market's YES."""
    per, b = bet
    kd = B.kind(b)
    yes, no = r.yes_team_code, r.no_team_code
    label = r.yes_side_label.strip().lower() if isinstance(r.yes_side_label, str) else ""
    q = r.market_question or ""
    if kd == "team_pick":
        if b.startswith("race_to_"):   # "Will the MIL Bucks reach 100 points before the BKN Nets?", yes side = mil
            if label != "yes":
                return []
            if isinstance(yes, str):
                return [((per, b, yes, None), "same")]
            return [((per, b, "none", None), "same")] if _NONE.search(q) else []   # "Will neither team reach 120"
        if not isinstance(yes, str):
            return [((per, b, "tie", None), "same")] if label == "yes" and _TIE.search(q) else []
        out = [((per, b, yes, None), "same")]
        if isinstance(no, str) and no != yes:   # one market per game (moneyline): NO side = the other team
            out.append(((per, b, no, None), "opposite"))
        return out
    if b == "spread":
        h = _num(r.yes_side_label)
        h = h if h is not None else _num(r.line)
        if h is None or h == 0 or not isinstance(yes, str):
            return []
        if h < 0:
            return [((per, b, yes, -h), "same")]
        return [((per, b, no, h), "opposite")] if isinstance(no, str) and no != yes else []
    if kd in ("team_line", "game_line"):
        line = _num(r.line)
        team = yes if kd == "team_line" else None
        if line is None or (kd == "team_line" and not isinstance(team, str)):
            return []
        if label in ("over", "yes"):
            return [((per, b, team, line), "same")]
        if label in ("under", "no"):
            return [((per, b, team, line), "opposite")]
        return []
    if kd == "yesno":
        line = _num(r.line)
        if line not in (None, 0.5):
            return []
        if label in ("yes", "over"):
            return [((per, b, None, None), "same")]
        if label in ("no", "under"):
            return [((per, b, None, None), "opposite")]
        return []
    if kd == "player_line":
        m = _P_PLAYER_LINE.match(q)
        if not m or label != "yes" or _num(r.line) != float(m["n"]):
            return []
        return [((per, b, C.norm_name(m["name"]), int(m["n"])), "same")]
    if kd == "player_yesno":
        m = _P_PLAYER_YESNO.match(q)
        return [((per, b, C.norm_name(m["name"]), None), "same")] if m and label == "yes" else []
    return []


# ---------------------------------------------------------------- match

def _kalshi_games(k, leagues_k):
    """Kalshi game rows grouped into games. Milestone game_id when the catalog has it, else ticker date + teams."""
    use_milestones = k.game_id.notna().any()
    rows = []
    for r in k.itertuples():
        g = K_GAME.match(r.native_event_id) if isinstance(r.native_event_id, str) else None
        if use_milestones:
            if not isinstance(r.game_id, str):
                continue
            key = r.game_id
        elif g:
            key = f"{r.competition}|{g['yy']}{g['mon']}{g['dd']}{g['hhmm'] or ''}{g['teams']}"
        else:
            continue
        start = r.game_start_ms if pd.notna(r.game_start_ms) else (
            C.kalshi_et_to_ms(g["yy"], g["mon"], g["dd"], g["hhmm"]) if g and g["hhmm"] else None)
        rows.append((r.Index, key, leagues_k.get(r.competition), start,
                     f"20{g['yy']}-{C._MON[g['mon']]:02d}-{g['dd']}" if g else None, g["teams"] if g else None))
    meta = pd.DataFrame(rows, columns=["idx", "game_key", "league", "start_ms", "date_et", "teamstr"]).set_index("idx")
    return k.loc[meta.index].join(meta), use_milestones


def match(cfg, k, p, notes):
    leagues_k = {cfg.LEAGUE["kalshi_competition"]: cfg.LEAGUE["name"]}
    aliases = cfg.team_aliases()
    tol = cfg.GAME_START_TOLERANCE_MIN

    k_game, use_milestones = _kalshi_games(k, leagues_k)
    k_used = set(k_game.index)
    unknown_k = Counter(k_game[k_game.league.isna()].competition.fillna("(no competition field)"))
    k_game = k_game[k_game.league.notna()]

    # Polymarket US: game-shaped slug AND two teams on the event (the daily leader has the slug but no teams)
    is_game = [bool(P_GAME.match(s)) and len(_poly_teams(r)) == 2 if isinstance(s, str) else False
               for s, r in zip(p.event_slug, p.itertuples())]
    p_used = {i for i, g in zip(p.index, is_game) if g}
    p_game = p.loc[sorted(p_used)].copy()
    p_game["league"] = cfg.LEAGUE["name"]

    games_k = {}
    for key, g in k_game.groupby("game_key"):
        codes = {r.yes_team_code: r.yes_title for r in g.itertuples()
                 if isinstance(r.yes_team_code, str) and r.yes_team_code != "TIE"
                 and B.kalshi_bet(r.competition_scope) == ("full_game", "winner")}
        starts = g.start_ms.dropna()
        dates = g.date_et.dropna()
        teamstrs = g.teamstr.dropna()
        games_k[key] = dict(start_ms=int(starts.iloc[0]) if len(starts) else None,
                            date_et=dates.mode().iloc[0] if len(dates) else None,
                            teamstr=teamstrs.mode().iloc[0] if len(teamstrs) else None,
                            codes=codes, rows=g, paired=False)

    pairs, unpaired = [], []
    unknown_bet = {"KALSHI": Counter(), "POLYUS": Counter()}
    not_yet = {"KALSHI": Counter(), "POLYUS": Counter()}
    no_partner, unusable = Counter(), Counter()
    for ev, g in p_game.groupby("event_uid"):
        first = g.iloc[0]
        start = g.game_start_ms.dropna()
        start = int(start.iloc[0]) if len(start) else None
        date_et = (datetime.fromtimestamp(start / 1000, timezone.utc).astimezone(C.ET).strftime("%Y-%m-%d")
                   if start else P_GAME.match(first.event_slug)["date"])
        teams = _poly_teams(first)
        cands, why = [], "no Kalshi game at this time"
        for key, kgm in games_k.items():
            diff = None
            if kgm["start_ms"] is not None and start is not None:
                diff = abs(kgm["start_ms"] - start) / 60_000
                if diff > tol:
                    continue
            elif kgm["date_et"] != date_et:
                continue
            mapping, how = resolve_teams(kgm["codes"], kgm["teamstr"], teams, aliases)
            if mapping is None:
                if why.startswith("no Kalshi"):
                    why = how
                continue
            score = sum(SCORE[h] for h in how.values())
            cands.append(((-score, tol if diff is None else diff), diff, key, mapping, how))
        if not cands:
            unpaired.append(dict(event=first.event_title, slug=first.event_slug, markets=len(g), reason=why))
            continue
        cands.sort(key=lambda x: x[0])   # strongest team evidence first, then closest tip-off
        if len(cands) > 1 and cands[0][0] == cands[1][0]:
            unpaired.append(dict(event=first.event_title, slug=first.event_slug, markets=len(g),
                                 reason="several Kalshi games fit equally well"))
            continue
        _, diff, key, mapping, how = cands[0]
        games_k[key]["paired"] = True
        if "elimination" in how.values():
            notes.append(f"{first.event_slug}: team matched by elimination: " + ", ".join(
                f"Kalshi {kc} = Polymarket US {mapping[kc]}" for kc, h in how.items() if h == "elimination"))

        by_prop = {}
        for r in games_k[key]["rows"].itertuples():
            bet = B.kalshi_bet(r.competition_scope)
            if bet is None:
                (not_yet if r.competition_scope in B.KALSHI_NOT_MATCHED_YET else unknown_bet)["KALSHI"][
                    r.competition_scope or "(none)"] += 1
                continue
            prop = kalshi_prop(r, bet, mapping)
            if prop is None:
                unusable[f"Kalshi {r.competition_scope}"] += 1
                continue
            by_prop.setdefault(prop, []).append(r)
        for pr in g.itertuples():
            smt = pr.sports_market_type or "(none)"
            bet = B.polyus_bet(pr.sports_market_type, pr.line)
            if bet is None:
                (not_yet if smt in B.POLYUS_NOT_MATCHED_YET else unknown_bet)["POLYUS"][smt] += 1
                continue
            props = polyus_props(pr, bet)
            if not props:
                unusable[f"Polymarket US {smt}"] += 1
                continue
            hit = False
            for prop, direction in props:
                krs = by_prop.get(prop, [])
                per_series = Counter(kr.series_id for kr in krs)
                for kr in krs:
                    if per_series[kr.series_id] > 1:
                        notes.append(f"{first.event_slug}: several Kalshi {kr.series_id} markets for {prop}; not paired")
                        continue
                    hit = True
                    per, b, subject, line = prop
                    pairs.append(C.pair_row(
                        kr, pr, map_key=f"{cfg.LEAGUE['name'].lower()}_{per}_{b}", league=cfg.LEAGUE["name"],
                        shape="game",
                        match_key=f"teams={'+'.join(sorted(mapping.values()))}|{per}|{b}"
                                  + (f"|{'player' if B.kind(b).startswith('player') else 'team'}={subject}" if subject else "")
                                  + (f"|line={line:g}" if line is not None else ""),
                        direction=direction, kalshi_yes=kr.yes_title, polyus_yes=pr.yes_side_label,
                        event_time_ms=start, start_diff_min=diff))
            if not hit:
                no_partner[f"{bet[0]} {bet[1]}"] += 1

    report = dict(
        kalshi_games=len(games_k), kalshi_game_markets=len(k_game),
        kalshi_games_unpaired=sum(1 for x in games_k.values() if not x["paired"]),
        kalshi_grouped_by="milestone game_id" if use_milestones else "ticker date + teams (catalog without milestones)",
        polyus_games=int(p_game.event_uid.nunique()), polyus_game_markets=len(p_game),
        game_pairs=sum(1 for x in games_k.values() if x["paired"]), market_pairs=len(pairs),
        unpaired_polyus_games=unpaired,
        unknown_bet_types={pl: dict(c.most_common()) for pl, c in unknown_bet.items()},
        recognised_not_matched_yet={pl: dict(c.most_common()) for pl, c in not_yet.items()},
        polyus_no_kalshi_market_at_same_line=dict(no_partner.most_common()),
        unusable_markets=dict(unusable.most_common()),
        unknown_leagues={"KALSHI": dict(unknown_k), "POLYUS": {}},
    )
    return pairs, report, k_used, p_used
