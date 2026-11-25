"""NFL configuration. Bet types are shared with the other football league (../bets.py).

No individual game or market is listed here; anything not covered is printed in the run report.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
APPROVALS = HERE / "template_approvals.json"
ALIASES = HERE / "team_aliases.json"
OUT_NAME = "nfl"   # data/candidate_pairs/sports/football/nfl/

# Kalshi: event product_metadata.competition. Polymarket US: series / event slug prefix.
LEAGUE = dict(name="NFL", kalshi_competition="Pro Football", polyus_prefix="nfl")

# Only markets that close within this many days of the run time. Kalshi: close_time (48 h after kickoff).
# Polymarket US: the event's end (end of game day), not the padded market endDate. None = no window.
CLOSES_WITHIN_DAYS = 7

# Kickoffs (Kalshi milestone start vs Polymarket US gameStartTime) must be within this many minutes.
GAME_START_TOLERANCE_MIN = 90

# Bet types written to candidate_pairs, as (period, bet) from ../bets.py. None = every recognised bet type.
# Initial build-out (2026-11-25): full-game moneylines only. Everything else is still matched and counted in
# the report ("bet filter" line), so widening this set is the only change needed to add bet types.
BETS = {("full_game", "winner")}

# Futures series on both platforms (see ../futures.py). Every football future checked on 2026-11-05
# closes beyond the 7-day window (season awards, conference titles, playoffs), so none are mapped yet.
# Any that fall inside the window appear in the report under "Not covered".
FUTURES = []


def team_aliases():
    """{kalshi_code: polyus_code} for teams that code, name and elimination cannot resolve."""
    return json.loads(ALIASES.read_text()) if ALIASES.exists() else {}
