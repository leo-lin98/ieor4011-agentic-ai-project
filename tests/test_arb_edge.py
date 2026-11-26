"""Joint depth walk: sizing by the thinner leg, stopping when fees eat the edge, caps, and P&L arithmetic."""
import pytest

from arb import fees
from arb.books import Book
from arb.edge import walk
from arb.legs import combos

K, P = "KALSHI:K", "POLYUS:P"


def _books(k_no_bids, k_yes_bids, p_yes_bids, p_yes_asks):
    """Kalshi stores bids; Polymarket YES asks are stored as NO bids at 100 - price."""
    bk = Book(K, "KALSHI"); bk.replace(k_yes_bids, k_no_bids, 1_000, 1_000)
    bp = Book(P, "POLYUS"); bp.replace(p_yes_bids, {round(100 - a, 6): q for a, q in p_yes_asks.items()}, 1_250, 1_200)
    return bk, bp


def test_asks_are_derived_from_opposing_bids():
    bk, bp = _books(k_no_bids={55: 10, 54: 20}, k_yes_bids={40: 5}, p_yes_bids={41: 7}, p_yes_asks={52: 30, 53: 40})
    assert bk.asks("yes") == [(45.0, 10), (46.0, 20)]      # YES ask = 100 - NO bid
    assert bk.asks("no") == [(60.0, 5)]
    assert bp.asks("yes") == [(52.0, 30), (53.0, 40)]
    assert bp.asks("no") == [(59.0, 7)]


def test_walk_sizes_by_thinner_leg_and_uses_exact_fees():
    # Kalshi YES ask 45 x 10 then 46 x 20; Polymarket NO ask 50 x 100 -> sum 95 then 96, both under 100 after fees
    bk, bp = _books(k_no_bids={55: 10, 54: 20}, k_yes_bids={}, p_yes_bids={50: 100}, p_yes_asks={})
    c = next(x for x in combos("p", "same", K, P) if x.kalshi.side == "yes")
    o = walk(c, bk, bp, cap_per_side=1e9)
    assert o.qty == 30 and o.levels_k == 2 and o.levels_p == 1
    assert o.vwap_k_c == pytest.approx((10 * 45 + 20 * 46) / 30) and o.vwap_p_c == 50
    assert o.fee_k == fees.kalshi_fee(o.vwap_k_c, 30) and o.fee_p == fees.polyus_fee(50, 30)
    assert o.locked_pnl == pytest.approx(30 * (100 - o.vwap_k_c - 50) / 100 - o.fee_k - o.fee_p, abs=1e-4)  # stored to 4 dp
    assert o.skew_ms == 250


def test_walk_stops_where_fees_eat_the_edge():
    # Kalshi YES asks 48 x 10 then 51 x 100; Polymarket NO ask 48 (YES bid 52). Level 1 sums to 96 (fine),
    # level 2 to 99 (fees ~3.5c at even money -> marginal > 100): only level 1 is taken
    bk, bp = _books(k_no_bids={52: 10, 49: 100}, k_yes_bids={}, p_yes_bids={52: 1000}, p_yes_asks={})
    c = next(x for x in combos("p", "same", K, P) if x.kalshi.side == "yes")
    o = walk(c, bk, bp, cap_per_side=1e9)
    assert o.qty == 10 and o.levels_k == 1


def test_walk_returns_none_when_no_edge():
    bk, bp = _books(k_no_bids={50: 10}, k_yes_bids={}, p_yes_bids={48: 10}, p_yes_asks={})   # 50 + 52 = 102
    c = next(x for x in combos("p", "same", K, P) if x.kalshi.side == "yes")
    assert walk(c, bk, bp) is None


def test_cap_per_side_limits_quantity():
    bk, bp = _books(k_no_bids={55: 100000}, k_yes_bids={}, p_yes_bids={50: 100000}, p_yes_asks={})  # 45 + 50
    c = next(x for x in combos("p", "same", K, P) if x.kalshi.side == "yes")
    o = walk(c, bk, bp, cap_per_side=1000.0)
    assert o.cost_p <= 1000.0 + 1e-6 and o.cost_k <= 1000.0 + 1e-6
    assert o.qty == pytest.approx(2000.0)   # Polymarket leg at 50c hits $1,000 first


def test_consume_removes_depth():
    bk, _ = _books(k_no_bids={55: 10, 54: 20}, k_yes_bids={}, p_yes_bids={}, p_yes_asks={})
    bk.consume("yes", 15)
    assert bk.asks("yes") == [(46.0, 15)]
