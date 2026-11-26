"""Paper engine: detect -> confirm -> fire -> consume -> settle, balances and P&L, both directions, tie."""
import pytest

from arb import paper
from arb.books import Books


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paper, "PAPER", tmp_path / "paper")
    monkeypatch.setattr(paper, "LIVE", tmp_path / "live.json")
    books = Books()
    eng = paper.Engine(books, confirm_ms=0, cooldown_ms=0)
    return books, eng


def _flag(pid, rel, k, p):
    return {"pair_id": pid, "relationship": rel, "kalshi_market_uid": k, "polyus_market_uid": p, "note": "t"}


def _set(books, k, p, k_no_bids, k_yes_bids, p_yes_bids, p_yes_asks, t=1000):
    bk = books.get(k, "KALSHI"); bk.replace(k_yes_bids, k_no_bids, t, t); bk.state = "active"
    bp = books.get(p, "POLYUS"); bp.replace(p_yes_bids, {round(100 - a, 6): q for a, q in p_yes_asks.items()}, t + 100, t + 100, "MARKET_STATE_OPEN")


def test_fire_and_settle_same_direction(env):
    books, eng = env
    K, P = "KALSHI:A", "POLYUS:1"
    eng.set_pairs([_flag("p1", "identical", K, P)])
    # Kalshi YES ask 45 x 50, Polymarket NO ask 50 x 50 (YES bid 50): sum 95
    _set(books, K, P, k_no_bids={55: 50}, k_yes_bids={}, p_yes_bids={50: 50}, p_yes_asks={})
    eng.on_book(K)   # confirm_ms=0: detected and fired on the same update
    eng.tick()
    assert eng.counters["detected"] == 1 and eng.counters["fired"] == 1 and len(eng.trades) == 1 and not eng.pending
    t = eng.trades[0]
    assert t["qty"] == 50 and t["legs"][0]["side"] == "yes" and t["legs"][1]["side"] == "no"
    assert t["sum_c"] == 95 and t["locked_pnl"] > 0
    assert eng.cash["KALSHI"] == pytest.approx(100_000 - t["cost_k"] - t["fee_k"])
    assert eng.cash["POLYUS"] == pytest.approx(100_000 - t["cost_p"] - t["fee_p"])
    assert books.by_uid[K].asks("yes") == [] and books.by_uid[P].asks("no") == []     # depth consumed
    # settle: Kalshi YES wins -> Kalshi leg pays, Polymarket NO leg does not
    eng.settle(K, "yes")
    assert t["status"] == "settled" and t["payout"] == 50 and t["settled_pnl"] == pytest.approx(t["locked_pnl"], abs=1e-6)
    assert eng.cash["KALSHI"] + eng.cash["POLYUS"] == pytest.approx(200_000 + t["locked_pnl"], abs=1e-6)


def test_opposite_direction_pays_exactly_one_leg(env):
    books, eng = env
    K, P = "KALSHI:B", "POLYUS:2"
    eng.set_pairs([_flag("p2", "complementary", K, P)])
    # combos for opposite: K.yes+P.yes and K.no+P.no. Make K YES ask 40 (NO bid 60), P YES ask 50: sum 90
    _set(books, K, P, k_no_bids={60: 10}, k_yes_bids={}, p_yes_bids={}, p_yes_asks={50: 10})
    eng.on_book(P); eng.tick()
    t = eng.trades[0]
    assert (t["legs"][0]["side"], t["legs"][1]["side"]) == ("yes", "yes")
    eng.settle(K, "no")          # Kalshi YES loses -> Polymarket YES (the other team) pays
    assert t["payout"] == 10 and t["settled_pnl"] == pytest.approx(t["locked_pnl"], abs=1e-6)


def test_tie_pays_half_each_leg(env):
    books, eng = env
    K, P = "KALSHI:C", "POLYUS:3"
    eng.set_pairs([_flag("p3", "identical", K, P)])
    _set(books, K, P, k_no_bids={55: 10}, k_yes_bids={}, p_yes_bids={50: 10}, p_yes_asks={})
    eng.on_book(K); eng.tick(); t = eng.trades[0]
    eng.settle(K, "tie")
    assert t["payout"] == 10 and t["settled_pnl"] == pytest.approx(t["locked_pnl"], abs=1e-6)


def test_no_fire_without_edge_and_skew_gate(env):
    books, eng = env
    K, P = "KALSHI:D", "POLYUS:4"
    eng.set_pairs([_flag("p4", "identical", K, P)])
    _set(books, K, P, k_no_bids={50: 10}, k_yes_bids={}, p_yes_bids={48: 10}, p_yes_asks={})   # 50 + 52
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 0
    # edge exists but books 2 s apart -> skew rejected
    books.by_uid[K].replace({}, {55: 10}, 1000, 1000); books.by_uid[P].replace({50: 10}, {}, 3000, 3000)
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 0 and eng.counters["skew_rejected"] >= 1


def test_not_tradeable_states_block_fire(env):
    books, eng = env
    K, P = "KALSHI:F", "POLYUS:6"
    eng.set_pairs([_flag("p6", "identical", K, P)])
    _set(books, K, P, k_no_bids={55: 10}, k_yes_bids={}, p_yes_bids={50: 10}, p_yes_asks={})   # 45 + 50: an edge
    books.by_uid[P].state = "MARKET_STATE_SUSPENDED"
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 0 and eng.counters["not_tradeable"] >= 1
    books.by_uid[P].state = "MARKET_STATE_OPEN"; books.by_uid[K].state = "closed"
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 0
    books.by_uid[K].state = "active"; books.by_uid[K].stale = True        # seq gap, waiting for snapshot
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 0
    books.by_uid[K].replace({}, {55: 10}, 1000, 1000)                      # snapshot arrives: stale cleared
    eng.on_book(K); eng.tick()
    assert eng.counters["fired"] == 1
    assert eng.trades[0]["state_k"] == "active" and eng.trades[0]["state_p"] == "MARKET_STATE_OPEN"


def test_caps_per_side(env):
    books, eng = env
    K, P = "KALSHI:E", "POLYUS:5"
    eng.set_pairs([_flag("p5", "identical", K, P)])
    _set(books, K, P, k_no_bids={55: 100000}, k_yes_bids={}, p_yes_bids={50: 100000}, p_yes_asks={})
    eng.on_book(K); eng.tick(); t = eng.trades[0]
    assert t["cost_k"] <= 1000 + 1e-6 and t["cost_p"] <= 1000 + 1e-6
