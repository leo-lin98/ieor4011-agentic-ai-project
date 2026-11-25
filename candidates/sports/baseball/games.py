"""Baseball games: every game in config.LEAGUES, every bet type in config.KALSHI_BETS / POLYUS_BETS.

Game recognition
  Kalshi         event ticker SERIES-YYMONDDHHMMTEAMS[-N]     KXMLBSPREAD-26OCT282000MILTB, KXMLBINNINGWIN-…-9
                 Kalshi lists each bet type as its own event; they are grouped into one game by
                 competition + date/time + teams. HHMM is the scheduled start in US Eastern time.
  Polymarket US  event slug [aec-]LEAGUE-TEAM-TEAM-YYYY-MM-DD[-N]   mlb-mil-tb-2026-10-28
                 One event holds every bet type for the game. Teams come from the event's team list
                 (titles are not used: "Game 5: MIL Brewers vs. TB Rays").

Game pairing: same league, scheduled starts within GAME_START_TOLERANCE_MIN, and both teams resolved
(code, display code, name, team_aliases.json, or elimination when one team is resolved).

Market pairing: each market becomes a proposition (period, bet, team, line).
  Kalshi         (period, bet) from competition_scope; team from the ticker (-TB4 -> TB); line = floor_strike,
                 strike_type "greater" (YES = more than the line)
  Polymarket US  (period, bet) from sportsMarketType; team from marketSides; line from the YES label or `line`
  spread         stored as "team wins by more than X". Polymarket US YES "A -1.5" = Kalshi "A wins by over 1.5" (same).
                 Polymarket US YES "A +1.5" = Kalshi NO "B wins by over 1.5" (opposite).
  total, team_total  same line; YES "Over" = same, "Under" = opposite
  winner         Polymarket US YES team A pairs with Kalshi "A" (same) and Kalshi "B" (opposite)
"""
import json
import re
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

from candidates import common as C

K_GAME = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})?"
                    r"(?P<teams>[A-Z]+)(?:-(?P<n>\d+))?$")
P_GAME = re.compile(r"^(?:aec-)?(?P<league>[a-z0-9]+)-(?P<a>[a-z0-9]+)-(?P<b>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})"
                    r"(?:-(?P<n>\d+))?$")
LINE_BETS = ("spread", "total", "team_total")


def _num(x):
    try:
        return round(float(str(x).replace("+", "")), 3)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- teams

def _poly_teams(r):
    try:
        teams = json.loads(r.event_teams) if isinstance(r.event_teams, str) else []
    except ValueError:
        teams = []
    teams = [t for t in teams if t.get("code")]
    if len(teams) != 2:
        g = P_GAME.match(r.event_slug or "")
        teams = [{"code": g["a"], "display": None, "name": None}, {"code": g["b"], "display": None, "name": None}] if g else []
    return teams


STRONG = {"alias", "code", "name"}       # exact evidence; "prefix" is weak
SCORE = {"alias": 3, "code": 2, "name": 2, "prefix": 1, "elimination": 0}


def resolve_teams(k_codes, k_teamstr, p_teams, alias):
    """Map Kalshi team codes to Polymarket US team codes for one game. Returns ({k: p}, how) or (None, why)."""
    pcodes = [t["code"] for t in p_teams]
    if len(pcodes) != 2:
        return None, "Polymarket US teams unknown"
    k_codes = dict(k_codes)
    if len(k_codes) < 2:  # only totals open on Kalshi: split the ticker's team string with Polymarket US codes
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
            elif knorm and name and name.startswith(knorm + " "):   # weak: "Chicago" is the start of two teams' names
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
        mapping[left_k[0]], how[left_k[0]] = left_p[0], "elimination"   # only from an exact anchor
    if len(mapping) != 2:
        return None, f"teams not resolved: Kalshi {sorted(k_codes) or k_teamstr} vs Polymarket US {pcodes}"
    return mapping, how


# ---------------------------------------------------------------- propositions

_TIE = re.compile(r"\bend in a tie\b|^tie$", re.I)


def kalshi_prop(r, bet, mapping):
    per, kind = bet
    if kind == "winner" and (r.yes_team_code == "TIE" or C.norm_name(r.yes_title) == "tie"):
        return (per, kind, "tie", None)   # 3-way bets (first 5 innings, single innings) have a tie market
    team = mapping.get(r.yes_team_code) if isinstance(r.yes_team_code, str) else None
    if kind in ("winner", "spread", "team_total") and team is None:
        return None
    line = None
    if kind in LINE_BETS:
        if r.strike_type != "greater" or pd.isna(r.floor_strike):
            return None
        line = _num(r.floor_strike)
    return (per, kind, team, line)


def polyus_props(r, bet):
    """[(prop, direction)]: direction is how Polymarket US YES relates to the Kalshi market's YES."""
    per, kind = bet
    yes, no = r.yes_team_code, r.no_team_code
    label = r.yes_side_label.strip().lower() if isinstance(r.yes_side_label, str) else ""
    if kind == "winner":
        if not isinstance(yes, str) and label == "yes" and _TIE.search(r.market_question or ""):
            return [((per, kind, "tie", None), "same")]   # "Will the first 5 innings ... end in a tie?"
        out = [((per, kind, yes, None), "same")] if isinstance(yes, str) else []
        if isinstance(no, str) and no != yes:
            out.append(((per, kind, no, None), "opposite"))
        return out
    if kind == "spread":
        h = _num(r.yes_side_label)
        h = h if h is not None else _num(r.line)
        if h is None or h == 0 or not isinstance(yes, str):
            return []
        if h < 0:
            return [((per, kind, yes, -h), "same")]
        return [((per, kind, no, h), "opposite")] if isinstance(no, str) and no != yes else []
    if kind in ("total", "team_total"):
        line = _num(r.line)
        team = yes if kind == "team_total" else None
        if line is None or (kind == "team_total" and not isinstance(team, str)):
            return []
        if label in ("over", "yes"):
            return [((per, kind, team, line), "same")]
        if label in ("under", "no"):
            return [((per, kind, team, line), "opposite")]
        return []
    if label in ("yes", "over"):  # yes/no game props
        return [((per, kind, None, None), "same")]
    if label in ("no", "under"):
        return [((per, kind, None, None), "opposite")]
    return []


# ---------------------------------------------------------------- match

def match(cfg, k, p, notes):
    leagues_k = {v["kalshi_competition"]: lg for lg, v in cfg.LEAGUES.items()}
    leagues_p = {v["polyus_prefix"]: lg for lg, v in cfg.LEAGUES.items()}
    aliases = cfg.team_aliases()
    tol = cfg.GAME_START_TOLERANCE_MIN

    # Kalshi: game-shaped rows, grouped into games across bet-type series
    kmatch = [K_GAME.match(e) if isinstance(e, str) else None for e in k.native_event_id]
    k_used = {i for i, g in zip(k.index, kmatch) if g}
    k_game = k.loc[sorted(k_used)].copy()
    kg = [g for g in kmatch if g]
    k_game["league"] = k_game.competition.map(leagues_k)
    k_game["game_key"] = [f"{c}|{g['yy']}{g['mon']}{g['dd']}{g['hhmm'] or ''}{g['teams']}" for c, g in zip(k_game.competition, kg)]
    k_game["teamstr"] = [g["teams"] for g in kg]
    k_game["start_ms"] = [C.kalshi_et_to_ms(g["yy"], g["mon"], g["dd"], g["hhmm"]) if g["hhmm"] else None for g in kg]
    k_game["date_et"] = [f"20{g['yy']}-{C._MON[g['mon']]:02d}-{g['dd']}" for g in kg]
    unknown_k = Counter(k_game[k_game.league.isna()].competition.fillna("(no competition field)"))
    k_game = k_game[k_game.league.notna()]

    # Polymarket US: game-shaped events
    pmatch = [P_GAME.match(s) if isinstance(s, str) else None for s in p.event_slug]
    p_used = {i for i, g in zip(p.index, pmatch) if g}
    p_game = p.loc[sorted(p_used)].copy()
    p_game["prefix"] = [g["league"] for g in pmatch if g]
    p_game["league"] = p_game.prefix.map(leagues_p)
    unknown_p = Counter(p_game[p_game.league.isna()].prefix)
    p_game = p_game[p_game.league.notna()]

    games_k = {}
    for key, g in k_game.groupby("game_key"):
        # The game's two teams come from its full-game winner markets only. Other markets carry codes that
        # are not teams (player props: BALMANDREWS, TIE). If no winner market is open, the team string
        # in the ticker is split using the Polymarket US codes (resolve_teams).
        codes = {r.yes_team_code: r.yes_title for r in g.itertuples()
                 if isinstance(r.yes_team_code, str) and r.yes_team_code != "TIE"
                 and cfg.KALSHI_BETS.get(r.competition_scope) == ("full_game", "winner")}
        games_k[key] = dict(league=g.league.iloc[0], start_ms=g.start_ms.iloc[0], date_et=g.date_et.iloc[0],
                            teamstr=g.teamstr.iloc[0], codes=codes, rows=g, paired=False)

    pairs, unpaired = [], []
    unknown_bet = {"KALSHI": Counter(), "POLYUS": Counter()}
    not_yet, no_partner, unusable = Counter(), Counter(), Counter()
    for ev, g in p_game.groupby("event_uid"):
        first = g.iloc[0]
        start = g.game_start_ms.dropna()
        start = int(start.iloc[0]) if len(start) else None
        date_et = (datetime.fromtimestamp(start / 1000, timezone.utc).astimezone(C.ET).strftime("%Y-%m-%d")
                   if start else P_GAME.match(first.event_slug)["date"])
        teams = _poly_teams(first)
        cands, why = [], "no Kalshi game in the same league at this time"
        for key, kgm in games_k.items():
            if kgm["league"] != first.league:
                continue
            diff = None  # minutes between scheduled starts; None when the Kalshi ticker has no time
            if kgm["start_ms"] is not None and start is not None:
                diff = abs(kgm["start_ms"] - start) / 60_000
                if diff > tol:
                    continue
            elif kgm["date_et"] != date_et:
                continue
            mapping, how = resolve_teams(kgm["codes"], kgm["teamstr"], teams, aliases.get(first.league, {}))
            if mapping is None:
                why = how
                continue
            score = sum(SCORE[h] for h in how.values())
            cands.append(((-score, tol if diff is None else diff), diff, key, mapping, how))
        if not cands:
            unpaired.append(dict(event=first.event_title, slug=first.event_slug, markets=len(g), reason=why))
            continue
        cands.sort(key=lambda x: x[0])   # strongest team evidence first, then closest start
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
            bet = cfg.KALSHI_BETS.get(r.competition_scope)
            if bet is None:
                unknown_bet["KALSHI"][r.competition_scope or "(none)"] += 1
                continue
            prop = kalshi_prop(r, bet, mapping)
            if prop is None:
                unusable[f"Kalshi {r.competition_scope}: no team or line"] += 1
                continue
            by_prop.setdefault(prop, []).append(r)
        for pr in g.itertuples():
            smt = pr.sports_market_type or "(none)"
            bet = cfg.POLYUS_BETS.get(smt)
            if bet is None:
                reason = next((v for pre, v in cfg.POLYUS_NOT_MATCHED_YET.items() if smt.startswith(pre)), None)
                (not_yet if reason else unknown_bet["POLYUS"])[smt] += 1
                continue
            props = polyus_props(pr, bet)
            if not props:
                unusable[f"Polymarket US {smt}: no team, line or side"] += 1
                continue
            hit = False
            for prop, direction in props:
                krs = by_prop.get(prop, [])
                if len(krs) > 1:
                    notes.append(f"{first.event_slug}: several Kalshi markets for {prop}; not paired")
                    continue
                for kr in krs:
                    hit = True
                    per, kind, team, line = prop
                    pairs.append(C.pair_row(
                        kr, pr, map_key=f"{first.league.lower()}_{per}_{kind}", league=first.league, shape="game",
                        match_key=f"teams={'+'.join(sorted(mapping.values()))}|{per}|{kind}"
                                  + (f"|team={team}" if team else "") + (f"|line={line:g}" if line is not None else ""),
                        direction=direction, kalshi_yes=kr.yes_title, polyus_yes=pr.yes_side_label,
                        event_time_ms=start, start_diff_min=diff))
            if not hit:
                no_partner[f"{bet[0]} {bet[1]}"] += 1

    report = dict(
        kalshi_games=len(games_k), kalshi_game_markets=len(k_game),
        kalshi_games_unpaired=sum(1 for x in games_k.values() if not x["paired"]),
        polyus_games=int(p_game.event_uid.nunique()), polyus_game_markets=len(p_game),
        game_pairs=sum(1 for x in games_k.values() if x["paired"]), market_pairs=len(pairs),
        unpaired_polyus_games=unpaired,
        unknown_bet_types={pl: dict(c.most_common()) for pl, c in unknown_bet.items()},
        polyus_recognised_not_matched_yet=dict(not_yet.most_common()),
        polyus_no_kalshi_market_at_same_line=dict(no_partner.most_common()),
        unusable_markets=dict(unusable.most_common()),
        unknown_leagues={"KALSHI": dict(unknown_k), "POLYUS": dict(unknown_p)},
    )
    return pairs, report, k_used, p_used
