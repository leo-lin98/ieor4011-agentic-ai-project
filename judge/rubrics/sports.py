"""Sports rubric: game and futures contracts on Kalshi and Polymarket US.

The matching code (candidates/sports/<sport>/) has already verified league, game (teams + start time),
bet type, team / player, line and direction. The judge decides whether the two rulebooks pay the same way
when the game is played to an official result.
"""
NAME = "sports"
VERSION = "1"

DIMENSIONS = {
    "outcome_definition": "exactly what YES pays on, on each side",
    "direction_mapping": 'for "same" pairs, YES=YES in every outcome; for "opposite" pairs, Kalshi YES = Polymarket US NO in every outcome of a played game, including a tie (both must pay the same amount on that mapping)',
    "tie_or_draw": "what happens if the game ends tied or drawn (possible in the NFL, NHL regulation, soccer; not in the NBA after overtime)",
    "overtime_and_game_completion": "does overtime / extra time / a shootout count toward the result; what defines the official result",
    "period_and_scoring": "same period (full game, half, quarter, first 5 innings) and the same scoring definition (points, runs, yards) for the line, if any",
    "timing_and_close": "do both refer to the same scheduled game; any date or time condition that could differ",
    "resolution_source": "official league result on both? any other source?",
    "postponement_or_rescheduling": "ACCEPTED RISK: how long each waits for a postponed game before voiding; whether a moved game still counts",
    "cancellation_suspension_shortened": "ACCEPTED RISK: when each voids, refunds or settles at a fair price; suspended or shortened games; forfeits",
    "other": "anything else that could make payouts differ",
}

VERDICT_SCOPE = """The verdict is about whether the two contracts pay the same way when the game is PLAYED TO AN OFFICIAL RESULT, including overtime, and whether the stated direction holds in every such outcome (win, loss, tie). "Official final result" on a sports contract includes overtime unless the text says otherwise."""

ACCEPTED_RISKS = """ACCEPTED RISKS (desk policy): the desk knowingly accepts that a game which is cancelled, not started within each platform's postponement window, suspended, or shortened may settle differently on the two exchanges (each settles at its own fair price, or voids). Record what each rulebook says about these on the checklist, mark them "same" = "unsure" or "no" as the text warrants, but DO NOT let them drive the verdict or opposite_side_ok. They are too rare to matter and nothing can be done about them. The same applies to each platform's generic disclaimers and to the wording of the resolution source when both plainly mean the official league result."""

_LEAGUE_NOTES = {
    "nfl": "NFL games can end in a tie after one overtime period in the regular season. A moneyline tie typically settles at $0.50 on both platforms; check that both say so. Spreads and totals use half-point lines, so pushes do not occur.",
    "nba": "NBA games cannot end tied: overtime periods are played until there is a winner. A tie clause is therefore irrelevant for full-game markets but matters for half and quarter markets, which can end level.",
    "cfb": "College football has no ties: overtime continues until a winner. Half and quarter markets can end level.",
    "nhl": "Hockey: distinguish regulation-time results from overtime/shootout results. A 'moneyline' may be 3-way (regulation, draw possible) or 2-way (includes OT/SO). Treat a mismatch here as not_identical.",
    "mlb": "Baseball: full-game markets include extra innings; first-5-innings markets can end tied. Check whether a shortened official game (rain) counts, but that is an accepted risk.",
    "soccer": "Soccer: a 3-way market (home / draw / away, 90 minutes plus stoppage) is not the same as a 2-way 'to qualify' or 'to lift the trophy' market that includes extra time and penalties. Period definitions matter.",
}


def notes(subcategory):
    key = (subcategory or "").split("/")[-1].lower()
    return _LEAGUE_NOTES.get(key, "")


def claim(t, examples):
    return ("Deterministic code has verified for every pair sharing these templates: same league, same game "
            "(both teams and scheduled start within 90 minutes), same bet type, same team or player, and the same "
            "line where there is one. The stated direction comes from the YES/NO side labels. Do not re-check those; "
            "check only how the rulebooks settle.")
