"""Baseball configuration: leagues, every baseball bet type on each platform, futures.

No individual game or market is listed here. Anything in baseball that these tables do not cover
is printed in the run report by name and count, so nothing is dropped without being seen.

Verified against the 2026-11-05 catalog and the 2026 World Series Game 5
(Kalshi KXMLB*-26OCT282000MILTB, Polymarket US mlb-mil-tb-2026-10-28).
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
APPROVALS = HERE / "template_approvals.json"
ALIASES = HERE / "team_aliases.json"

# ---------------------------------------------------------------- leagues
# Kalshi: event product_metadata.competition. Polymarket US: first part of the event slug.
LEAGUES = {
    "MLB": dict(kalshi_competition="Pro Baseball", polyus_prefix="mlb"),
    "KBO": dict(kalshi_competition="Korea KBO", polyus_prefix="kbo"),
}

# Only look at markets that close within this many days of the run time (liquidity is concentrated
# near expiration). See common.market_close_ms for which close date is used. None = no window.
CLOSES_WITHIN_DAYS = 7

# Games: scheduled starts must be within this many minutes.
# Observed difference: 0 (KBO 2026-11-06..08, MLB World Series 2026-10-28).
GAME_START_TOLERANCE_MIN = 90

# ---------------------------------------------------------------- game bet types
# (period, bet). Both platforms' names for the same bet map to the same tuple.
#   winner      which team wins                        Kalshi: one market per team
#   spread      team wins by more than X runs (run line)
#   total       both teams' runs more than X
#   team_total  one team's runs more than X
#   first_inning_run, extra_innings: yes/no game props
_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th", 8: "8th", 9: "9th"}

KALSHI_BETS = {   # competition_scope
    "Game": ("full_game", "winner"),
    "Spread": ("full_game", "spread"),
    "Point Total": ("full_game", "total"),
    "Team Total": ("full_game", "team_total"),
    "First 5 Innings Winner": ("first5", "winner"),
    "First 5 Innings Spread": ("first5", "spread"),
    "First 5 Innings Total": ("first5", "total"),
    "YRFI/NRFI": ("full_game", "first_inning_run"),
    "Extra Innings": ("full_game", "extra_innings"),
    **{f"{_ORD[i]} Inning Winner": (f"inning{i}", "winner") for i in _ORD},
    **{f"{_ORD[i]} Inning Total": (f"inning{i}", "total") for i in _ORD},
}

POLYUS_BETS = {   # sportsMarketType
    "baseball_team_full_game_winner": ("full_game", "winner"),
    "baseball_team_full_game_spread": ("full_game", "spread"),
    "baseball_team_full_game_total": ("full_game", "total"),
    "baseball_team_total_runs": ("full_game", "team_total"),
    "baseball_team_first_five_winner": ("first5", "winner"),
    "baseball_team_first_five_spread": ("first5", "spread"),
    "baseball_team_first_five_total": ("first5", "total"),
    "baseball_team_first_inning_run": ("full_game", "first_inning_run"),
    "baseball_game_extra_innings": ("full_game", "extra_innings"),
    **{f"baseball_team_inning{i}_winner": (f"inning{i}", "winner") for i in _ORD},
    **{f"baseball_team_inning{i}_total": (f"inning{i}", "total") for i in _ORD},
}

# Recognised but not matched yet (reported under their own heading, with counts).
POLYUS_NOT_MATCHED_YET = {
    "baseball_player_": "player props. Kalshi has player series (KXMLBHIT, KXMLBHR, KXMLBKS, ...); matching not built yet",
}

# ---------------------------------------------------------------- futures
# One row per futures series that exists on both platforms. A row covers every season:
# Kalshi reuses the series ticker (KXMLBNLMVP-26, -27, ...); Polymarket US reuses the slug pattern.
#   polyus_event: regex on the Polymarket US event slug, group 1 = season year
#   entity: player | team_code | team_name  (how markets inside the event are linked)
_D = r"(\d{4})-\d{2}-\d{2}"
FUTURES = [
    dict(key="mlb_al_mvp", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBALMVP", polyus_event=rf"^mlb-al-{_D}-mvp$"),
    dict(key="mlb_nl_mvp", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBNLMVP", polyus_event=rf"^mlb-nl-{_D}-mvp$"),
    dict(key="mlb_al_cy_young", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBALCY", polyus_event=rf"^mlb-al-{_D}-cy$"),
    dict(key="mlb_nl_cy_young", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBNLCY", polyus_event=rf"^mlb-nl-{_D}-cy$"),
    dict(key="mlb_al_rookie", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBALROTY", polyus_event=rf"^mlb-al-{_D}-roy$"),
    dict(key="mlb_nl_rookie", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBNLROTY", polyus_event=rf"^mlb-nl-{_D}-roy$"),
    dict(key="mlb_al_hank_aaron", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBALHAARON", polyus_event=rf"^mlb-hankaaronal-{_D}-w$"),
    dict(key="mlb_nl_hank_aaron", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBNLHAARON", polyus_event=rf"^mlb-hankaaronnl-{_D}-w$"),
    dict(key="mlb_al_manager", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBALMOTY", polyus_event=rf"^mlb-almoty-{_D}-w$"),
    dict(key="mlb_nl_manager", league="MLB", shape="award", entity="player",
         kalshi_series="KXMLBNLMOTY", polyus_event=rf"^mlb-nlmoty-{_D}-w$"),
    dict(key="mlb_world_series", league="MLB", shape="championship", entity="team_code",
         kalshi_series="KXMLB", polyus_event=rf"^mlb-ws-{_D}-w$"),
    dict(key="kbo_champion", league="KBO", shape="championship", entity="team_name",
         kalshi_series="KXKBO", polyus_event=rf"^kbo-series-{_D}-w$"),
]


def team_aliases():
    """{league: {kalshi_code: polyus_code}} for teams that code, name and elimination cannot resolve."""
    return json.loads(ALIASES.read_text()) if ALIASES.exists() else {}
