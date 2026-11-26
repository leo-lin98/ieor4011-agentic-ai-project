"""Map each platform's sports labels onto one shared sport list (DECISIONS.md D10).

Kalshi: the sport is the series tag (T1 `tags`, e.g. '["Football"]').
Polymarket US: the sport is `subcategory`, which is inconsistent, so several labels map to one sport
(NHL appears under HOCKEY and ICEHOCKEY; UFC and boxing share COMBAT; tennis has five labels).

Only sports listed on both platforms with real overlap are included. Each has its own folder here.
"""
import json

# folder name -> display name
INCLUDED = {
    "football": "Football",
    "basketball": "Basketball",
    "soccer": "Soccer",
    "hockey": "Hockey",
    "golf": "Golf",
    "baseball": "Baseball",
    "motorsport": "Motorsport",
    "esports": "Esports",
    "tennis": "Tennis",
    "mma": "MMA",
    "boxing": "Boxing",
    "table_tennis": "Table Tennis",
}

# Dropped 2026-11-17: no counterpart on the other platform, or only one to three events on one side.
EXCLUDED = {"Chess", "Other", "Rugby", "Cricket", "Darts", "Volleyball", "Lacrosse", "Pickleball"}

KALSHI_TAG = {
    "Football": "football", "Basketball": "basketball", "Soccer": "soccer", "Hockey": "hockey",
    "Golf": "golf", "Baseball": "baseball", "Motorsport": "motorsport", "Esports": "esports",
    "Tennis": "tennis", "MMA": "mma", "Boxing": "boxing", "Table Tennis": "table_tennis",
}

POLYUS_SUBCATEGORY = {
    "FOOTBALL": "football",
    "BASKETBALL": "basketball",
    "SOCCER": "soccer",
    "HOCKEY": "hockey", "ICEHOCKEY": "hockey",
    "GOLF": "golf",
    "BASEBALL": "baseball",
    "MOTORSPORT": "motorsport",
    "ESPORTS": "esports", "EFOOTBALL": "esports", "EBASKETBAL": "esports",  # eSoccer / eBasketball
    "TENNIS": "tennis", "ATP": "tennis", "WTA": "tennis", "ATPDB": "tennis", "WTADB": "tennis",
    "MMA": "mma",
    "BOXING": "boxing",
    "TABLETENIS": "table_tennis",
    # COMBAT holds both UFC and boxing; split by series slug in sport_of()
}


def sport_of(platform, category, subcategory=None, tags=None, series_id=None):
    """Return the sport folder name for one T1 row, or None if it is not an included sport."""
    if (category or "").lower() != "sports":
        return None
    if platform == "KALSHI":
        if not isinstance(tags, str) or not tags:   # None, or NaN on some pandas/pyarrow versions
            return None
        try:
            tag = (json.loads(tags) or [None])[0]
        except ValueError:
            return None
        return KALSHI_TAG.get(tag)
    if subcategory == "COMBAT":
        return "mma" if (series_id or "").startswith("ufc") else "boxing"
    return POLYUS_SUBCATEGORY.get(subcategory)
