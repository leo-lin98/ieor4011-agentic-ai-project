"""Payoff matrix for both directions, and both fee schedules against the exchanges' own examples."""
import pytest

from arb import fees
from arb.legs import OUTCOMES, Combo, Leg, combos

K, P = "KALSHI:KXNBAGAME-26NOV25MIAPHI-MIA", "POLYUS:2420097"


def _sides(cs):
    return sorted((c.kalshi.side, c.polyus.side) for c in cs)


def test_same_direction_combos():
    cs = combos("p", "same", K, P, "Miami", "Heat")
    assert _sides(cs) == [("no", "yes"), ("yes", "no")]
    for c in cs:
        assert all(c.payoff(o) == 1.0 for o in OUTCOMES)


def test_opposite_direction_combos():
    cs = combos("p", "opposite", "KALSHI:...-PHI", P, "Philadelphia", "Heat")
    assert _sides(cs) == [("no", "no"), ("yes", "yes")]
    for c in cs:
        assert all(c.payoff(o) == 1.0 for o in OUTCOMES)


def test_wrong_combos_do_not_pay_one_in_every_outcome():
    bad = Combo("p", "same", Leg("KALSHI", K, "yes"), Leg("POLYUS", P, "yes"))   # same-direction YES+YES: doubles up
    assert {bad.payoff(o) for o in OUTCOMES} == {2.0, 0.0}


def test_unknown_direction_rejected():
    with pytest.raises(ValueError):
        combos("p", "sideways", K, P)


# ---------------------------------------------------------------- Kalshi: PDF "General Trading Fees Table"

@pytest.mark.parametrize("price_c,qty,expected", [
    (1, 1, 0.01), (50, 1, 0.02), (85, 1, 0.01), (99, 1, 0.01),
    (1, 100, 0.07), (5, 100, 0.34), (10, 100, 0.63), (20, 100, 1.12), (45, 100, 1.74), (50, 100, 1.75),
    (55, 100, 1.74), (80, 100, 1.12), (90, 100, 0.63), (95, 100, 0.34), (99, 100, 0.07),
])
def test_kalshi_taker_matches_published_table(price_c, qty, expected):
    assert fees.kalshi_fee(price_c, qty, taker=True) == pytest.approx(expected)


def test_kalshi_maker_only_on_maker_fee_series():
    assert fees.kalshi_fee(50, 100, taker=False, fee_type="quadratic") == 0.0
    assert fees.kalshi_fee(50, 100, taker=False, fee_type="quadratic_with_maker_fees") == pytest.approx(0.44)   # 0.4375 ceil
    assert fees.kalshi_fee(50, 100, taker=False, fee_type="quadratic_with_combo_maker_fees") == pytest.approx(0.88)


def test_kalshi_multiplier_and_direct_member_precision():
    assert fees.kalshi_fee(50, 100, fee_multiplier=0.0) == 0.0
    assert fees.kalshi_fee(50, 100, fee_multiplier=2.0) == pytest.approx(3.50)
    assert fees.kalshi_fee(45, 100, precision=0.0001) == pytest.approx(1.7325)


def test_kalshi_flat_fee_series_refused():
    with pytest.raises(ValueError):
        fees.kalshi_fee(50, 1, fee_type="flat")


# ---------------------------------------------------------------- Polymarket US

def test_polyus_taker_api_theta_bankers_rounding():
    assert fees.polyus_fee(50, 100) == pytest.approx(1.74)       # 1.7375 -> 1.74
    assert fees.polyus_fee(50, 1) == pytest.approx(0.02)         # 0.017375 -> 0.02
    assert fees.polyus_fee(90, 100) == pytest.approx(0.63)       # 0.6255 -> 0.63 (not a .5 tie)
    assert fees.polyus_fee(50, 100, theta=0.095) == pytest.approx(2.38)   # 2.375 -> 2.38 (half-even: 2.375 is exact -> 2.38)
    assert fees.polyus_fee(50, 100, taker=False) == 0.0


def test_polyus_rebate():
    assert fees.polyus_rebate(1.74) == pytest.approx(1.218)


def test_dispatch_uses_t3_params_and_ignores_feecoefficient():
    assert fees.fee("KALSHI", 50, 100, params={"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0}, taker=False) == pytest.approx(0.44)
    assert fees.fee("POLYUS", 50, 100, params={"fee_coefficient": 0.095}) == pytest.approx(1.74)
    assert fees.fee("POLYUS", 50, 100, params={"theta": 0.095}) == pytest.approx(2.38)


def test_breakeven_sum_at_even_money():
    # both legs at 48c, 100 contracts, taker both sides: 96 + 1.75 + 1.74 (per 100) -> 96 + 3.49c
    be = fees.breakeven_sum_c(48, 48, qty=100)
    assert be == pytest.approx(96 + (fees.kalshi_fee(48, 100) + fees.polyus_fee(48, 100)))
    assert be > 99.0
    assert fees.breakeven_sum_c(48, 48, qty=100, count_rebate=True) < be
