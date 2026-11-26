"""Executable, fee-adjusted edge for one combo from two live books (DECISIONS D11.3-4).

Joint depth walk: at each step take the smaller remaining quantity at the current best level of each leg,
as long as the marginal cost of one more contract (ask_K + ask_P + marginal fees) stays below 100 cents and
neither leg's cost would exceed its cap. Then compute the EXACT fees on the total quantity at each leg's
average price (each exchange's rounding) and the locked-in P&L:

    locked_pnl = qty * (100 - vwap_K - vwap_P) / 100  -  fee_K  -  fee_P        (dollars)

Marginal fees inside the walk are the un-rounded formulas; the rounded fees decide the final number.
"""
from dataclasses import asdict, dataclass

from . import fees


@dataclass
class Opportunity:
    combo_key: str
    pair_id: str
    direction: str
    kalshi_uid: str
    kalshi_side: str
    polyus_uid: str
    polyus_side: str
    qty: float
    vwap_k_c: float
    vwap_p_c: float
    cost_k: float          # dollars, before fees
    cost_p: float
    fee_k: float
    fee_p: float
    locked_pnl: float      # dollars, after both fees, before the Polymarket rebate
    rebate_p: float        # dollars expected back weekly (70% of fee_p)
    edge_c: float          # locked_pnl per contract, cents
    top_k_c: float         # best asks at detection (for the log)
    top_p_c: float
    levels_k: int
    levels_p: int
    recv_k_ms: int
    recv_p_ms: int
    exch_k_ms: int | None
    exch_p_ms: int | None
    skew_ms: int           # |recv_k - recv_p|

    def to_dict(self):
        return asdict(self)


def _marginal_fee_c(platform, price_c, params):
    p = price_c / 100.0
    if platform == "KALSHI":
        return 100 * fees.KALSHI_TAKER_COEF * (params.get("fee_multiplier") or 1.0) * p * (1 - p)
    return 100 * (params.get("theta") or fees.POLYUS_THETA_API) * p * (1 - p)


def walk(combo, book_k, book_p, k_params=None, p_params=None, cap_per_side=1000.0, max_qty=None):
    """Opportunity for `combo` given the two books, or None if the best levels already cost >= 100c."""
    k_params, p_params = k_params or {}, p_params or {}
    ak, ap = book_k.asks(combo.kalshi.side), book_p.asks(combo.polyus.side)
    if not ak or not ap:
        return None
    qty = cost_k = cost_p = 0.0
    i = j = 0
    used_k, used_p = set(), set()
    rem_k, rem_p = ak[0][1], ap[0][1]
    while i < len(ak) and j < len(ap):
        pk, pp = ak[i][0], ap[j][0]
        marginal = pk + pp + _marginal_fee_c("KALSHI", pk, k_params) + _marginal_fee_c("POLYUS", pp, p_params)
        if marginal >= 100.0:
            break
        take = min(rem_k, rem_p)
        # caps: dollars per side, and an optional contract cap
        room_k = (cap_per_side * 100 - cost_k * 100) / pk if pk > 0 else take
        room_p = (cap_per_side * 100 - cost_p * 100) / pp if pp > 0 else take
        take = min(take, room_k, room_p)
        if max_qty is not None:
            take = min(take, max_qty - qty)
        if take <= 1e-9:
            break
        qty += take
        cost_k += take * pk / 100
        cost_p += take * pp / 100
        used_k.add(i)
        used_p.add(j)
        rem_k -= take
        rem_p -= take
        if rem_k <= 1e-9:
            i += 1
            rem_k = ak[i][1] if i < len(ak) else 0
        if rem_p <= 1e-9:
            j += 1
            rem_p = ap[j][1] if j < len(ap) else 0
        if take == room_k or take == room_p or (max_qty is not None and qty >= max_qty):
            break
    qty = round(qty, 4)
    if qty < 0.01:   # Polymarket allows fractional contracts; below its 0.01 minimum there is nothing to trade
        return None
    vk, vp = round(100 * cost_k / qty, 4), round(100 * cost_p / qty, 4)   # the numbers we report; P&L follows from them
    cost_k, cost_p = round(qty * vk / 100, 4), round(qty * vp / 100, 4)
    fee_k = fees.fee("KALSHI", vk, qty, params=k_params)
    fee_p = fees.fee("POLYUS", vp, qty, params=p_params)
    locked = round(qty * (100 - vk - vp) / 100 - fee_k - fee_p, 4)
    return Opportunity(
        combo_key=combo.key, pair_id=combo.pair_id, direction=combo.direction,
        kalshi_uid=combo.kalshi.market_uid, kalshi_side=combo.kalshi.side,
        polyus_uid=combo.polyus.market_uid, polyus_side=combo.polyus.side,
        qty=qty, vwap_k_c=vk, vwap_p_c=vp,
        cost_k=cost_k, cost_p=cost_p, fee_k=fee_k, fee_p=fee_p,
        locked_pnl=locked, rebate_p=fees.polyus_rebate(fee_p), edge_c=round(100 * locked / qty, 4),
        top_k_c=ak[0][0], top_p_c=ap[0][0], levels_k=len(used_k), levels_p=len(used_p),
        recv_k_ms=book_k.recv_ms, recv_p_ms=book_p.recv_ms, exch_k_ms=book_k.exch_ms, exch_p_ms=book_p.exch_ms,
        skew_ms=abs((book_k.recv_ms or 0) - (book_p.recv_ms or 0)),
    )


def quote(combo, book_k, book_p, k_params=None, p_params=None):
    """Top-of-book view for the Live tab even when there is no edge: best asks, sizes, pre-fee sum, net edge at
    the smaller top size."""
    (pk, qk), (pp, qp) = book_k.best_ask(combo.kalshi.side), book_p.best_ask(combo.polyus.side)
    if pk is None or pp is None:
        return {"combo_key": combo.key, "ask_k_c": pk, "ask_p_c": pp, "qty_k": qk, "qty_p": qp, "sum_c": None, "edge_c": None}
    q = min(qk, qp)   # both >= Book.MIN_QTY by construction
    fk = fees.fee("KALSHI", pk, q, params=k_params)
    fp = fees.fee("POLYUS", pp, q, params=p_params)
    edge_c = (100 - pk - pp) - 100 * (fk + fp) / q
    return {"combo_key": combo.key, "ask_k_c": pk, "ask_p_c": pp, "qty_k": qk, "qty_p": qp, "sum_c": round(pk + pp, 4),
            "edge_c": round(edge_c, 4), "skew_ms": abs((book_k.recv_ms or 0) - (book_p.recv_ms or 0)),
            "recv_k_ms": book_k.recv_ms, "recv_p_ms": book_p.recv_ms}
