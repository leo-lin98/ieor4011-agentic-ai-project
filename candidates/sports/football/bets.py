"""Every football bet type on each platform (NFL and college use the same names on both platforms).

Each entry maps to (period, bet). Both platforms' names for the same bet map to the same tuple.
Built from the 2026-11-05 catalog (NFL Jaguars @ Ravens and the college slate).

Bet kinds (games.py turns each market into a proposition (period, bet, subject, line)):
  TEAM_PICK    one team (or tie / neither) is the answer: game/half/quarter winner, first team to score a TD,
               race to N points. Kalshi: one market per outcome.
  TEAM_LINE    a team versus a number: spread (team wins by more than X), team points, team field goals, team yards
  GAME_LINE    both teams versus a number: total points
  YESNO        it happens or not: tie, overtime, safety, defensive/special-teams TD, both teams score in a quarter
  PLAYER_LINE  a player reaches N or more: passing / rushing / receiving yards, receptions, touchdowns, ...
  PLAYER_PICK  one player is the answer: first TD scorer, team's first TD scorer, most passing yards
"""
import re

_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
_WORD = {1: "first", 2: "second", 3: "third", 4: "fourth"}

TEAM_PICK = {"winner", "first_td_team"}            # plus race_to_N
TEAM_LINE = {"spread", "team_total", "team_field_goals", "team_total_yards"}
GAME_LINE = {"total"}
YESNO = {"tie", "overtime", "safety", "dst_touchdown", "both_teams_score"}
PLAYER_PICK = {"player_first_td", "player_team_first_td", "player_most_passing_yards"}
PLAYER_LINE = {"player_passing_yards", "player_passing_tds", "player_pass_attempts", "player_pass_completions",
               "player_interceptions", "player_rushing_yards", "player_rush_attempts", "player_receiving_yards",
               "player_receptions", "player_touchdowns", "player_longest_reception", "player_longest_rush",
               "player_scrimmage_yards"}


def kind(bet):
    if bet in TEAM_PICK or bet.startswith("race_to_"):
        return "team_pick"
    for name, group in (("team_line", TEAM_LINE), ("game_line", GAME_LINE), ("yesno", YESNO),
                        ("player_pick", PLAYER_PICK), ("player_line", PLAYER_LINE)):
        if bet in group:
            return name
    return None


# ---------------------------------------------------------------- Kalshi competition_scope
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
    **{f"BTTS in {_ORD[q]} Quarter": (f"quarter{q}", "both_teams_score") for q in _ORD},
    "Tie": ("full_game", "tie"),
    "Overtime": ("full_game", "overtime"),
    "Safety": ("full_game", "safety"),
    "D/ST Touchdown": ("full_game", "dst_touchdown"),
    "First Team to Score a TD": ("full_game", "first_td_team"),
    "Team Field Goals": ("full_game", "team_field_goals"),
    "Team Total Yards": ("full_game", "team_total_yards"),
    # players ("(Protected)" is a separate Kalshi product with its own rules; it pairs separately)
    **{f"{n}{p}": ("full_game", b) for n, b in (
        ("Passing Yards", "player_passing_yards"), ("Passing Touchdowns", "player_passing_tds"),
        ("Rushing Yards", "player_rushing_yards"), ("Receiving Yards", "player_receiving_yards"),
        ("Receptions", "player_receptions"), ("Anytime Touchdowns", "player_touchdowns"))
       for p in ("", " (Protected)")},
    "Passing Attempts": ("full_game", "player_pass_attempts"),
    "Passing Completions": ("full_game", "player_pass_completions"),
    "Passing Interceptions": ("full_game", "player_interceptions"),
    "Passing Interceptions (Protected)": ("full_game", "player_interceptions"),
    "Rushing Attempts": ("full_game", "player_rush_attempts"),
    "Rushing + Receiving Yards": ("full_game", "player_scrimmage_yards"),
    "Rushing + Receiving Yards (Protected)": ("full_game", "player_scrimmage_yards"),
    "Longest Reception": ("full_game", "player_longest_reception"),
    "Longest Rush": ("full_game", "player_longest_rush"),
    "First TD": ("full_game", "player_first_td"),
    "Most Passing Yards": ("full_game", "player_most_passing_yards"),
}
_K_RACE = re.compile(r"^Race to (\d+) Points$")
_K_TEAM_FIRST_TD = re.compile(r"^First (?!TD$|Team to Score)(.+) TD$")   # "First Baltimore TD"

KALSHI_NOT_MATCHED_YET = {
    "Point Total Range": "exact-range buckets; Polymarket US has no ranges",
    "Passing Yards Range": "exact-range buckets; Polymarket US has no ranges",
    "Fantasy Points": "scoring system not confirmed to match Polymarket US PPR; fractional points",
    "Fantasy Ladder": "Kalshi-only product",
    "Receiving Yards Ladder": "Kalshi-only product", "Rushing Yards Ladder": "Kalshi-only product",
    "Receptions Ladder": "Kalshi-only product", "Receiving Yards Escalator": "Kalshi-only product",
    "Rushing Yards Escalator": "Kalshi-only product", "Receptions Escalator": "Kalshi-only product",
    "Team Sacks": "no Polymarket US counterpart",
    "Weather Delay": "no Polymarket US counterpart",
    "BTTS Points": "no Polymarket US counterpart",
    "BTTS in Every Quarter": "no Polymarket US counterpart",
    "Most Receiving Yards": "no Polymarket US counterpart seen",
    "Most Rushing Yards": "no Polymarket US counterpart seen",
    "Winning Margin": "margin bands not yet checked against Polymarket US exact margin",
    "Team Receiving Yards": "college only; may differ from Polymarket US team passing yards (sacks); not yet checked",
    "Receiving Touchdowns": "college only; team receiving TDs vs Polymarket US team passing TDs; not yet checked",
    "First Half / Fulltime": "not yet checked against Polymarket US halftime/full-time",
}


def kalshi_bet(scope):
    if not isinstance(scope, str):
        return None
    if scope in KALSHI_BETS:
        return KALSHI_BETS[scope]
    if m := _K_RACE.match(scope):
        return ("full_game", f"race_to_{m[1]}")
    if _K_TEAM_FIRST_TD.match(scope):
        return ("full_game", "player_team_first_td")
    return None


# ---------------------------------------------------------------- Polymarket US sportsMarketType
POLYUS_BETS = {
    "football_team_full_game_winner": ("full_game", "winner"),
    "football_team_full_game_spread": ("full_game", "spread"),
    "football_team_full_game_total": ("full_game", "total"),            # game total despite "team" in the name
    "football_team_points_full_game_total": ("full_game", "team_total"),  # one team's points
    **{f"football_team_{_WORD[h]}_half_winner": (f"half{h}", "winner") for h in (1, 2)},
    **{f"football_team_{_WORD[h]}_half_spread": (f"half{h}", "spread") for h in (1, 2)},
    **{f"football_game_{_WORD[h]}_half_total": (f"half{h}", "total") for h in (1, 2)},
    **{f"football_team_{_WORD[h]}_half_total": (f"half{h}", "team_total") for h in (1, 2)},
    **{f"football_team_{_WORD[q]}_quarter_winner": (f"quarter{q}", "winner") for q in _WORD},
    **{f"football_team_{_WORD[q]}_quarter_spread": (f"quarter{q}", "spread") for q in _WORD},
    **{f"football_game_{_WORD[q]}_quarter_total": (f"quarter{q}", "total") for q in _WORD},
    **{f"football_game_{_WORD[q]}_quarter_both_teams_score_points": (f"quarter{q}", "both_teams_score") for q in _WORD},
    "football_game_tie": ("full_game", "tie"),
    "football_game_overtime": ("full_game", "overtime"),
    "football_game_safety": ("full_game", "safety"),
    "football_game_total_defensive_special_teams_touchdowns": ("full_game", "dst_touchdown"),
    "football_game_first_touchdown": ("full_game", "first_td_team"),
    "football_team_total_field_goals_made": ("full_game", "team_field_goals"),
    "football_team_total_offensive_yards": ("full_game", "team_total_yards"),
    "football_player_passing_yards": ("full_game", "player_passing_yards"),
    "football_player_passing_touchdowns": ("full_game", "player_passing_tds"),
    "football_player_passing_attempts": ("full_game", "player_pass_attempts"),
    "football_player_passing_completions": ("full_game", "player_pass_completions"),
    "football_player_interceptions_thrown": ("full_game", "player_interceptions"),
    "football_player_rushing_yards": ("full_game", "player_rushing_yards"),
    "football_player_rushing_attempts": ("full_game", "player_rush_attempts"),
    "football_player_scrimmage_yards": ("full_game", "player_scrimmage_yards"),   # rushing + receiving yards
    "football_player_receiving_yards": ("full_game", "player_receiving_yards"),
    "football_player_receptions": ("full_game", "player_receptions"),
    "football_player_touchdowns": ("full_game", "player_touchdowns"),
    "football_player_longest_reception": ("full_game", "player_longest_reception"),
    "football_player_longest_rush": ("full_game", "player_longest_rush"),
    "football_player_first_touchdown": ("full_game", "player_first_td"),
    "football_player_team_first_touchdown": ("full_game", "player_team_first_td"),
    "football_player_most_passing_yards": ("full_game", "player_most_passing_yards"),
}

POLYUS_NOT_MATCHED_YET = {
    "football_player_fantasy_points_ppr": "scoring system not confirmed to match Kalshi; fractional points",
    "football_game_double_result": "not yet checked against Kalshi First Half / Fulltime",
    "football_game_exact_margin": "margin bands not yet checked against Kalshi Winning Margin",
    "football_game_first_score": "no Kalshi counterpart (first points, not first TD)",
    "football_game_total_touchdowns": "no Kalshi counterpart",
    "football_game_total_pass_touchdowns": "no Kalshi counterpart",
    "football_game_total_rush_touchdowns": "no Kalshi counterpart",
    "football_game_total_two_point_conversions": "no Kalshi counterpart",
    "football_team_total_touchdowns": "no Kalshi counterpart",
    "football_team_total_defensive_special_teams_touchdowns": "no Kalshi counterpart (Kalshi D/ST TD is per game)",
    "football_team_total_pass_yards": "college; Kalshi has Team Receiving Yards, definitions not yet checked",
    "football_team_total_pass_touchdowns": "college; Kalshi has Receiving Touchdowns, definitions not yet checked",
    "football_team_total_rush_yards": "no Kalshi counterpart",
    "football_team_total_rush_touchdowns": "no Kalshi counterpart",
    "football_game_total_fourth_down_conversions": "no Kalshi counterpart",
}


def polyus_bet(smt, line):
    if not isinstance(smt, str):
        return None
    if smt == "football_game_race_to_points" and line is not None and line == line:
        return ("full_game", f"race_to_{int(line)}")
    return POLYUS_BETS.get(smt)
