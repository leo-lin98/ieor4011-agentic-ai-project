"""From a candidate pair and its direction to the two riskless combos.

A pair links one Kalshi binary market and one Polymarket US binary market that the matcher and the judge
agree settle on the same proposition. `direction` says how their YES sides relate:

  same      Kalshi YES pays  <=>  Polymarket US YES pays
  opposite  Kalshi YES pays  <=>  Polymarket US NO pays   (Polymarket's YES is the other team)

A combo is two legs, one per platform, each a side to BUY, such that in every outcome exactly one leg pays
$1. Buying both legs for less than $1 (after fees) locks in the difference. The combos are DERIVED from the
payoff matrix, not hard-coded, and `combos()` asserts there are exactly two and that each sums to $1 in both
outcomes. A tie that pays $0.50 on both platforms also sums to $1 and is covered by the same assertion.
"""
from dataclasses import dataclass

OUTCOMES = ("kalshi_yes_event", "kalshi_no_event")   # the Kalshi YES proposition happens / does not happen


@dataclass(frozen=True)
class Leg:
    platform: str      # KALSHI | POLYUS
    market_uid: str
    side: str          # yes | no  (the side we BUY)
    label: str = ""    # what that side means in words, for display and logs

    def pays(self, outcome, direction):
        """$ payout of this leg (bought) in `outcome`, given the pair's direction."""
        yes_event = outcome == "kalshi_yes_event"
        if self.platform == "KALSHI":
            return 1.0 if (self.side == "yes") == yes_event else 0.0
        poly_yes_pays = yes_event if direction == "same" else not yes_event
        return 1.0 if (self.side == "yes") == poly_yes_pays else 0.0


@dataclass(frozen=True)
class Combo:
    pair_id: str
    direction: str
    kalshi: Leg
    polyus: Leg

    @property
    def legs(self):
        return (self.kalshi, self.polyus)

    def payoff(self, outcome):
        return self.kalshi.pays(outcome, self.direction) + self.polyus.pays(outcome, self.direction)

    @property
    def key(self):
        return f"{self.pair_id}|K.{self.kalshi.side}+P.{self.polyus.side}"

    def describe(self):
        return (f"BUY Kalshi {self.kalshi.side.upper()} [{self.kalshi.label}]  +  "
                f"BUY Polymarket US {self.polyus.side.upper()} [{self.polyus.label}]   (direction {self.direction})")


def combos(pair_id, direction, kalshi_uid, polyus_uid, kalshi_yes_label="", polyus_yes_label="", polyus_no_label=""):
    """The riskless combos for one pair. Raises if the direction is unknown or the payoff matrix does not give
    exactly two combos that pay $1 in every outcome."""
    if direction not in ("same", "opposite"):
        raise ValueError(f"{pair_id}: unknown direction {direction!r}")
    k_labels = {"yes": kalshi_yes_label or "YES", "no": f"NOT {kalshi_yes_label}" if kalshi_yes_label else "NO"}
    p_labels = {"yes": polyus_yes_label or "YES", "no": polyus_no_label or (f"NOT {polyus_yes_label}" if polyus_yes_label else "NO")}
    out = []
    for ks in ("yes", "no"):
        for ps in ("yes", "no"):
            c = Combo(pair_id, direction, Leg("KALSHI", kalshi_uid, ks, k_labels[ks]), Leg("POLYUS", polyus_uid, ps, p_labels[ps]))
            if all(c.payoff(o) == 1.0 for o in OUTCOMES):
                out.append(c)
    if len(out) != 2:
        raise AssertionError(f"{pair_id}: expected 2 riskless combos, derived {len(out)}")
    return out


def combos_for_row(r):
    """Combos from a candidate_pairs / match_flags-style row (itertuples or dict-like with the T7 columns)."""
    g = (lambda k: getattr(r, k, None)) if not isinstance(r, dict) else r.get
    return combos(g("pair_id"), g("direction"), g("kalshi_market_uid"), g("polyus_market_uid"),
                  kalshi_yes_label=g("kalshi_yes") or "", polyus_yes_label=g("polyus_yes") or "")
