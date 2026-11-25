"""NBA configuration. Bet types are shared with the other basketball leagues (../bets.py).

No individual game or market is listed here; anything not covered is printed in the run report.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
APPROVALS = HERE / "template_approvals.json"
ALIASES = HERE / "team_aliases.json"
OUT_NAME = "nba"   # data/candidate_pairs/sports/basketball/nba/

# Kalshi: event product_metadata.competition. Polymarket US: series / event slug prefix.
LEAGUE = dict(name="NBA", kalshi_competition="Pro Basketball (M)", polyus_prefix="nba")

# Only markets that close within this many days of the run time. Kalshi: close_time. Polymarket US: the
# event's end (end of game day), not the padded market endDate. None = no window.
CLOSES_WITHIN_DAYS = 7

# Tip-offs (Kalshi milestone start vs Polymarket US gameStartTime) must be within this many minutes.
GAME_START_TOLERANCE_MIN = 90

# Bet types written to candidate_pairs, as (period, bet) from ../bets.py. None = every recognised bet type.
# Initial build-out (2026-11-25): full-game moneylines only. Everything else is still matched and counted in
# the report ("bet filter" line), so widening this set is the only change needed to add bet types.
BETS = {("full_game", "winner")}

# Futures series on both platforms (see ../futures.py). Season awards, conference and finals markets all
# close beyond the 7-day window, so none are mapped yet. Any inside the window appear under "Not covered".
FUTURES = []


def team_aliases():
    """{kalshi_code: polyus_code} for teams that code, name and elimination cannot resolve."""
    return json.loads(ALIASES.read_text()) if ALIASES.exists() else {}
