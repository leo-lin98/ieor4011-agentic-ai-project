"""Shared checks plus category-specific settlement dimensions."""
from dataclasses import dataclass

COMMON = {
    "outcome_and_mapping": "Underlying event, reference entity, YES/NO mapping, and payout amounts.",
    "resolution_source": "Designated authority, source priority, and fallback sources.",
    "deadline_and_time_zone": "Observation window, cutoff inclusivity, time zone and daylight saving.",
    "exceptional_outcomes": "Cancellation, delay, unavailable data, voids, refunds, partial payouts and disputes.",
}

SPECIALIZED = {
    "economics": {
        "series_definition": "Publisher, series, geography, units, adjustments, and reference period.",
        "release_and_revision": "First release versus revised release, vintage, corrections and revised schedules.",
        "threshold": "Comparator, numeric boundary, rounding precision and unit conversions.",
    },
    "politics": {
        "jurisdiction_and_stage": "Office, constituency, election round and exact result or officeholding condition.",
        "declaration_and_certification": "Projection versus official or certified result, recounts and challenges.",
        "candidate_and_transition": "Withdrawals, substitutions, runoffs, vacancies and delayed transitions.",
    },
    "crypto": {
        "instrument_and_feed": "Base and quote assets, venue or index, and exact price feed.",
        "observation_method": "Intraperiod touch versus a snapshot, averaging, candle type and feed timestamp.",
        "threshold_and_disruption": "Comparator, precision, outages, illiquid trading and fork treatment.",
    },
}

ALIASES = {
    "elections": "politics", "geopolitics": "politics",
    "macro": "economics", "financials": "economics", "finance": "economics",
    "cryptocurrency": "crypto", "digital assets": "crypto",
}
@dataclass(frozen=True)
class Rubric:
    NAME: str
    DIMENSIONS: dict
    VERSION = "1"
    VERDICT_SCOPE = "Check the stated mapping across ordinary and exceptional settlement scenarios."
    ACCEPTED_RISKS = "Record all payout-relevant differences and uncertainties for human review."

    @staticmethod
    def notes(subcategory):
        return ""


def for_pair(kalshi_category, polyus_category, *, include_sports=False):
    categories = set()
    for raw in (kalshi_category, polyus_category):
        key = (raw or "").strip().lower()
        categories.add(ALIASES.get(key, key))
    if "sports" in categories:
        if not include_sports:
            raise ValueError("Use the sports template judge for sports markets")
        return Rubric("sports_pair", {
            **COMMON,
            "game_and_period": "Teams, scheduled game, full game versus regulation or a partial period.",
            "completion_and_scoring": "Overtime, tie, push, official result, scoring and shortened games.",
        })
    known = categories & SPECIALIZED.keys()
    if len(known) == 1:
        name = next(iter(known))
        return Rubric(name, {**COMMON, **SPECIALIZED[name]})
    return Rubric("general", COMMON)
