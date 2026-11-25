"""Every basketball bet type on each platform (leagues share these names on both platforms).

Each entry maps to (period, bet). Both platforms' names for the same bet map to the same tuple.
Built from the 2026-11-25 catalog (NBA, 30 Kalshi games / 66 Polymarket US games listed).

Bet kinds (games.py turns each market into a proposition (period, bet, subject, line)):
  TEAM_PICK     one team (or tie) is the answer: game / half / quarter winner, race to N points.
                Kalshi: one market per outcome.
  TEAM_LINE     a team versus a number: spread (team wins by more than X), team points
  GAME_LINE     both teams versus a number: total points
  YESNO         it happens or not: overtime
  PLAYER_LINE   a player reaches N or more: points, rebounds, assists, threes, steals, blocks, PRA
  PLAYER_YESNO  a player does or doesn't: double-double, triple-double
"""
import re

_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
_WORD = {1: "first", 2: "second", 3: "third", 4: "fourth"}

TEAM_PICK = {"winner"}                      # plus race_to_N
TEAM_LINE = {"spread", "team_total"}
GAME_LINE = {"total"}
YESNO = {"overtime"}
PLAYER_LINE = {"player_points", "player_rebounds", "player_assists", "player_threes", "player_steals",
               "player_blocks", "player_pra"}
PLAYER_YESNO = {"player_double_double", "player_triple_double"}


def kind(bet):
    if bet in TEAM_PICK or bet.startswith("race_to_"):
        return "team_pick"
    for name, group in (("team_line", TEAM_LINE), ("game_line", GAME_LINE), ("yesno", YESNO),
                        ("player_line", PLAYER_LINE), ("player_yesno", PLAYER_YESNO)):
        if bet in group:
            return name
    return None


# ---------------------------------------------------------------- Kalshi competition_scope
_PLAYER_LINES = (("Player Points", "player_points"), ("Player Rebounds", "player_rebounds"),
                 ("Player Assists", "player_assists"), ("Player Threes", "player_threes"),
                 ("Player Steals", "player_steals"), ("Player Blocks", "player_blocks"),
                 ("Pts + Reb + Ast", "player_pra"))
_PLAYER_YESNO = (("Double Double", "player_double_double"), ("Triple Double", "player_triple_double"))

KALSHI_BETS = {
    "Game": ("full_game", "winner"),
    "Spread": ("full_game", "spread"),
    "Point Total": ("full_game", "total"),
    "Team Total": ("full_game", "team_total"),
    **{f"{_ORD[h]} Half Winner": (f"half{h}", "winner") for h in (1, 2)},
    **{f"{_ORD[h]} Half Spread": (f"half{h}", "spread") for h in (1, 2)},
    **{f"{_ORD[h]} Half Total": (f"half{h}", "total") for h in (1, 2)},
    **{f"{_ORD[h]} Half Team Total": (f"half{h}", "team_total") for h in (1, 2)},
    **{f"{_ORD[q]} Quarter Winner": (f"quarter{q}", "winner") for q in _ORD},
    **{f"{_ORD[q]} Quarter Spread": (f"quarter{q}", "spread") for q in _ORD},
    **{f"{_ORD[q]} Quarter Total": (f"quarter{q}", "total") for q in _ORD},
    "Overtime": ("full_game", "overtime"),
    # players ("(Protected)" is a separate Kalshi product with its own rules; it pairs separately)
    **{f"{n}{p}": ("full_game", b) for n, b in _PLAYER_LINES + _PLAYER_YESNO for p in ("", " (Protected)")},
}
_K_RACE = re.compile(r"^Race to (\d+) Points$")

KALSHI_NOT_MATCHED_YET = {
    "Points Leader": "per-game points leader; Polymarket US only has a league-wide daily leader (nba-lead-pts-<date>)",
    "Winning Margin": "margin bands (1-5, 6-10, 11-20, 21+) look identical to Polymarket US exact margin; not yet checked",
    "Point Total Range": "KXNBATOTALPRECISE 1-point ladder; confirm one-sided vs range before pairing with totals",
    "Steals + Blocks": "no Polymarket US counterpart", "Steals + Blocks (Protected)": "no Polymarket US counterpart",
    "Player Points Escalator": "Kalshi-only product", "Player Threes Escalator": "Kalshi-only product",
}


def kalshi_bet(scope):
    if not isinstance(scope, str):
        return None
    if scope in KALSHI_BETS:
        return KALSHI_BETS[scope]
    if m := _K_RACE.match(scope):
        return ("full_game", f"race_to_{m[1]}")
    return None


# ---------------------------------------------------------------- Polymarket US sportsMarketType
POLYUS_BETS = {
    "basketball_team_full_game_winner": ("full_game", "winner"),
    "basketball_team_full_game_spread": ("full_game", "spread"),
    "basketball_team_full_game_total": ("full_game", "total"),        # game total despite "team" in the name
    "basketball_team_total_points": ("full_game", "team_total"),      # one team's points
    **{f"basketball_team_{_WORD[h]}_half_winner": (f"half{h}", "winner") for h in (1, 2)},
    **{f"basketball_team_{_WORD[h]}_half_spread": (f"half{h}", "spread") for h in (1, 2)},
    **{f"basketball_team_{_WORD[h]}_half_total": (f"half{h}", "total") for h in (1, 2)},
    **{f"basketball_team_total_points_{_WORD[h]}_half": (f"half{h}", "team_total") for h in (1, 2)},
    **{f"basketball_team_{_WORD[q]}_quarter_winner": (f"quarter{q}", "winner") for q in _WORD},
    **{f"basketball_team_{_WORD[q]}_quarter_spread": (f"quarter{q}", "spread") for q in _WORD},
    **{f"basketball_team_{_WORD[q]}_quarter_total": (f"quarter{q}", "total") for q in _WORD},
    "basketball_game_overtime": ("full_game", "overtime"),
    "basketball_player_points": ("full_game", "player_points"),
    "basketball_player_rebounds": ("full_game", "player_rebounds"),
    "basketball_player_assists": ("full_game", "player_assists"),
    "basketball_player_threes": ("full_game", "player_threes"),
    "basketball_player_steals": ("full_game", "player_steals"),
    "basketball_player_blocks": ("full_game", "player_blocks"),
    "basketball_player_points_rebounds_assists": ("full_game", "player_pra"),
    "basketball_player_double_double": ("full_game", "player_double_double"),
    "basketball_player_triple_double": ("full_game", "player_triple_double"),
}

POLYUS_NOT_MATCHED_YET = {
    "basketball_game_winning_margin": "margin bands not yet checked against Kalshi Winning Margin",
    "futures": "league-wide daily leader (nba-lead-pts-<date>) listed with a game-shaped slug; not a game",
}


def polyus_bet(smt, line):
    if not isinstance(smt, str):
        return None
    if smt == "basketball_game_race_to_points" and line is not None and line == line:
        return ("full_game", f"race_to_{int(line)}")
    return POLYUS_BETS.get(smt)
