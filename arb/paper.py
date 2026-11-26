"""Paper-trading engine (DECISIONS D11.8): detects, confirms and "executes" riskless combos on live books.

Rules (agreed 2026-11-25):
  fire      net edge after both taker fees > 0, and the two legs' books were received within MAX_SKEW_MS
  confirm   an opportunity must still be there CONFIRM_MS after first detection (quote persisted) before it fires;
            the fire uses the books as they are at that moment
  size      joint depth walk (edge.walk), capped at CAP_PER_SIDE dollars of cost per leg per trade and
            PAIR_CAP_PER_SIDE cumulative per pair per leg; never below $0 cash in either account
  after     the consumed depth is removed from the in-memory books; the same combo cannot fire again until both
            books have changed and COOLDOWN_MS has passed
  accounts  START_CASH per platform; cash -= cost + fee per leg at fire; += $1 x qty on the paying leg at settlement
  settle    from Kalshi's market result (status finalized/settled, result yes/no). The two legs are the same
            proposition by construction, so Kalshi's result settles both. A tie pays $0.50 per leg.

Files (data/paper/): trades.jsonl (one line per paper trade, every timestamp in ms), edges.jsonl (every
detected positive edge, fired or not, one line per combo per second), state.json (balances, positions,
totals; rewritten every second), plus data/arb/live.json for the dashboard.
"""
import json
import logging
import os
from collections import defaultdict

from collector.config import DATA
from collector.util import now_ms

from . import edge
from .legs import combos

log = logging.getLogger("arb.paper")

START_CASH = 100_000.0
CAP_PER_SIDE = 1_000.0
PAIR_CAP_PER_SIDE = 10_000.0
MAX_SKEW_MS = 150         # was 500; the Polymarket leg was 50-230 ms older than Kalshi's at every fire on night one
CONFIRM_MS = 20           # one order round trip from the Ohio box (Stefan, 2026-11-26); was 300 (every in-game edge vanished first)
COOLDOWN_MS = 3_000

PAPER = DATA / "paper"
LIVE = DATA / "arb" / "live.json"


def _append(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, path)


class Pair:
    __slots__ = ("pair_id", "direction", "kalshi_uid", "polyus_uid", "combos", "note", "fee_k", "fee_p", "spent_k", "spent_p")

    def __init__(self, flag, fee_k=None, fee_p=None, labels=None):
        self.pair_id = flag["pair_id"]
        self.direction = "same" if flag["relationship"] == "identical" else "opposite"
        self.kalshi_uid, self.polyus_uid = flag["kalshi_market_uid"], flag["polyus_market_uid"]
        self.note = flag.get("note") or ""
        labels = labels or {}
        self.combos = combos(self.pair_id, self.direction, self.kalshi_uid, self.polyus_uid,
                             kalshi_yes_label=labels.get("kalshi_yes") or "", polyus_yes_label=labels.get("polyus_yes") or "",
                             polyus_no_label=labels.get("polyus_no") or "")
        self.fee_k = fee_k or {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1.0}
        self.fee_p = fee_p or {}
        self.spent_k = self.spent_p = 0.0


class Engine:
    def __init__(self, books, start_cash=START_CASH, cap_per_side=CAP_PER_SIDE, pair_cap=PAIR_CAP_PER_SIDE,
                 max_skew_ms=MAX_SKEW_MS, confirm_ms=CONFIRM_MS, cooldown_ms=COOLDOWN_MS):
        self.books = books
        self.cap, self.pair_cap, self.max_skew, self.confirm_ms, self.cooldown_ms = cap_per_side, pair_cap, max_skew_ms, confirm_ms, cooldown_ms
        self.pairs = {}                       # pair_id -> Pair
        self.by_uid = defaultdict(list)       # market_uid -> [Pair]
        self.pending = {}                     # combo_key -> dict(opp, detected_ms, vk, vp)
        self.last_fire = {}                   # combo_key -> dict(ms, vk, vp)
        self.last_edge_log = {}               # combo_key -> second bucket
        self.cash = {"KALSHI": start_cash, "POLYUS": start_cash}
        self.start_cash = start_cash
        self.trades = []                      # open + settled, in memory (also on disk)
        self.realised = {"KALSHI": 0.0, "POLYUS": 0.0}
        self.rebate_expected = 0.0
        self.counters = defaultdict(int)
        self.stream_stats = {}
        self._load()

    # ------------------------------------------------------------ persistence

    def _load(self):
        st = PAPER / "state.json"
        if st.exists():
            s = json.loads(st.read_text())
            self.cash = s.get("cash", self.cash)
            self.realised = s.get("realised", self.realised)
            self.rebate_expected = s.get("rebate_expected", 0.0)
        tr = PAPER / "trades.jsonl"
        if tr.exists():
            self.trades = [json.loads(l) for l in tr.read_text().splitlines() if l.strip()]
        log.info("paper state: cash %s, %d trades on file (%d open)", self.cash, len(self.trades),
                 sum(1 for t in self.trades if t["status"] == "open"))

    def summary(self):
        open_t = [t for t in self.trades if t["status"] == "open"]
        locked_open = sum(t["locked_pnl"] for t in open_t)
        tied_up = {p: sum(t[f"cost_{'k' if p == 'KALSHI' else 'p'}"] + t[f"fee_{'k' if p == 'KALSHI' else 'p'}"] for t in open_t) for p in ("KALSHI", "POLYUS")}
        return {
            "ts_ms": now_ms(), "start_cash": self.start_cash, "cash": self.cash,
            "tied_up": tied_up, "realised": self.realised,
            "equity": {p: self.cash[p] + tied_up[p] for p in self.cash},
            "trades": len(self.trades), "open": len(open_t), "settled": len(self.trades) - len(open_t),
            "locked_pnl_total": round(sum(t["locked_pnl"] for t in self.trades), 4),
            "locked_pnl_open": round(locked_open, 4),
            "settled_pnl": round(sum(t.get("settled_pnl") or 0 for t in self.trades), 4),
            "fees_paid": round(sum(t["fee_k"] + t["fee_p"] for t in self.trades), 4),
            "rebate_expected": round(self.rebate_expected, 4),
            "counters": dict(self.counters),
        }

    def write_state(self, live_rows=None):
        _write_json(PAPER / "state.json", self.summary())
        _write_json(LIVE, {"ts_ms": now_ms(), "stream": self.stream_stats, "pairs": len(self.pairs),
                           "books": {p: sum(1 for b in self.books.by_uid.values() if b.platform == p and (b.yes_bids or b.no_bids))
                                     for p in ("KALSHI", "POLYUS")},
                           "pending": len(self.pending), "rows": live_rows or []})

    # ------------------------------------------------------------ pairs

    def set_pairs(self, flags, fee_params=None, labels=None):
        """(Re)load active flags. labels: pair_id -> {kalshi_yes, polyus_yes, polyus_no} for display.
        Returns (new kalshi uids, new polyus uids)."""
        fee_params, labels = fee_params or {}, labels or {}
        seen = set()
        new_k, new_p = set(), set()
        for f in flags:
            pid = f["pair_id"]
            seen.add(pid)
            if pid in self.pairs:
                continue
            p = Pair(f, fee_params.get(f["kalshi_market_uid"]), fee_params.get(f["polyus_market_uid"]), labels.get(pid))
            self.pairs[pid] = p
            self.by_uid[p.kalshi_uid].append(p)
            self.by_uid[p.polyus_uid].append(p)
            new_k.add(p.kalshi_uid)
            new_p.add(p.polyus_uid)
        for pid in [x for x in self.pairs if x not in seen]:
            p = self.pairs.pop(pid)
            self.by_uid[p.kalshi_uid].remove(p)
            self.by_uid[p.polyus_uid].remove(p)
            for c in p.combos:
                self.pending.pop(c.key, None)
        return new_k, new_p

    # ------------------------------------------------------------ detection

    def on_book(self, uid):
        """Called for every book update. Detects / refreshes / drops pending opportunities on affected combos."""
        now = now_ms()
        for pair in self.by_uid.get(uid, ()):
            bk, bp = self.books.by_uid.get(pair.kalshi_uid), self.books.by_uid.get(pair.polyus_uid)
            if bk is None or bp is None or not (bk.yes_bids or bk.no_bids) or not (bp.yes_bids or bp.no_bids):
                continue
            if not (bk.tradeable and bp.tradeable):   # halted / suspended / closed / auction / stale after a seq gap
                for c in pair.combos:
                    if c.key in self.pending:
                        self._vanish(c.key, now, "not_tradeable")
                self.counters["not_tradeable"] += 1
                continue
            for c in pair.combos:
                opp = edge.walk(c, bk, bp, pair.fee_k, pair.fee_p, cap_per_side=self.cap)
                if opp is None or opp.locked_pnl <= 0:
                    if c.key in self.pending:
                        self._vanish(c.key, now, "before_confirm")
                    continue
                self._log_edge(opp, now)
                if opp.skew_ms > self.max_skew:
                    self.counters["skew_rejected"] += 1
                    continue
                if c.key not in self.pending:
                    self.pending[c.key] = {"opp": opp, "detected_ms": now, "pair": pair, "combo": c}
                    self.counters["detected"] += 1
                else:
                    self.pending[c.key]["opp"] = opp
        if any(now - p["detected_ms"] >= self.confirm_ms for p in self.pending.values()):
            self.tick()   # confirm on the update itself; don't wait for the timer

    def _vanish(self, key, now, stage):
        """A detected edge disappeared before it fired. Logs how long it lived, so the confirm window is tuned from data."""
        pend = self.pending.pop(key)
        lived = now - pend["detected_ms"]
        self.counters[f"vanished_{stage}"] += 1
        o = pend["opp"]
        _append(PAPER / "vanished.jsonl", {"ts_ms": now, "combo_key": key, "detected_ms": pend["detected_ms"], "lived_ms": lived,
                                           "stage": stage, "edge_c": o.edge_c, "qty": o.qty, "locked_pnl": o.locked_pnl,
                                           "vwap_k_c": o.vwap_k_c, "vwap_p_c": o.vwap_p_c, "skew_ms": o.skew_ms})

    def _log_edge(self, opp, now):
        bucket = now // 1000
        if self.last_edge_log.get(opp.combo_key) == bucket:
            return
        self.last_edge_log[opp.combo_key] = bucket
        _append(PAPER / "edges.jsonl", {"ts_ms": now, **opp.to_dict()})

    # ------------------------------------------------------------ confirmation and fire

    def tick(self):
        """Called every ~10 ms and on book updates. Fires pending opportunities that have persisted CONFIRM_MS,
        re-walked on the books as they are now."""
        now = now_ms()
        for key, pend in list(self.pending.items()):
            if now - pend["detected_ms"] < self.confirm_ms:
                continue
            pair, c = pend["pair"], pend["combo"]
            bk, bp = self.books.by_uid[pair.kalshi_uid], self.books.by_uid[pair.polyus_uid]
            lf = self.last_fire.get(key)
            if lf and (now - lf["ms"] < self.cooldown_ms or (lf["vk"] == bk.version and lf["vp"] == bp.version)):
                continue   # cooling down, or neither book has changed since we consumed it
            room_k = self.pair_cap - pair.spent_k
            room_p = self.pair_cap - pair.spent_p
            cap = min(self.cap, room_k, room_p, self.cash["KALSHI"], self.cash["POLYUS"])
            if cap <= 1.0:
                self.counters["cap_blocked"] += 1
                del self.pending[key]
                continue
            if not (bk.tradeable and bp.tradeable):
                self._vanish(key, now, "not_tradeable")
                continue
            opp = edge.walk(c, bk, bp, pair.fee_k, pair.fee_p, cap_per_side=cap)
            if opp is None or opp.locked_pnl <= 0:
                self._vanish(key, now, "at_confirm")
                continue
            del self.pending[key]
            if opp.skew_ms > self.max_skew:
                self.counters["skew_rejected"] += 1
                continue
            self._fire(pair, c, opp, pend["detected_ms"], now, bk, bp)

    def _fire(self, pair, c, opp, detected_ms, fired_ms, bk, bp):
        t = {
            "trade_id": f"{fired_ms}-{self.counters['fired'] + 1}", "status": "open",
            "detected_ms": detected_ms, "fired_ms": fired_ms, "confirm_latency_ms": fired_ms - detected_ms,
            "book_recv_k_ms": bk.recv_ms, "book_recv_p_ms": bp.recv_ms, "book_exch_k_ms": bk.exch_ms, "book_exch_p_ms": bp.exch_ms,
            "state_k": bk.state, "state_p": bp.state, "close_k_ms": bk.close_ms,
            "skew_ms": opp.skew_ms,
            "pair_id": pair.pair_id, "direction": pair.direction, "note": pair.note, "combo": c.key,
            "legs": [
                {"platform": "KALSHI", "market_uid": c.kalshi.market_uid, "side": c.kalshi.side, "label": c.kalshi.label, "qty": opp.qty,
                 "avg_price_c": opp.vwap_k_c, "top_price_c": opp.top_k_c, "levels": opp.levels_k, "cost": opp.cost_k, "fee": opp.fee_k},
                {"platform": "POLYUS", "market_uid": c.polyus.market_uid, "side": c.polyus.side, "label": c.polyus.label, "qty": opp.qty,
                 "avg_price_c": opp.vwap_p_c, "top_price_c": opp.top_p_c, "levels": opp.levels_p, "cost": opp.cost_p, "fee": opp.fee_p},
            ],
            "legs_text": c.describe(),
            "qty": opp.qty, "cost_k": opp.cost_k, "cost_p": opp.cost_p, "fee_k": opp.fee_k, "fee_p": opp.fee_p,
            "cost_total": round(opp.cost_k + opp.cost_p + opp.fee_k + opp.fee_p, 4),
            "sum_c": round(opp.vwap_k_c + opp.vwap_p_c, 4), "edge_c": opp.edge_c,
            "locked_pnl": opp.locked_pnl, "rebate_expected": opp.rebate_p,
            "settled_ms": None, "result": None, "payout": None, "settled_pnl": None,
        }
        self.cash["KALSHI"] -= opp.cost_k + opp.fee_k
        self.cash["POLYUS"] -= opp.cost_p + opp.fee_p
        pair.spent_k += opp.cost_k
        pair.spent_p += opp.cost_p
        self.rebate_expected += opp.rebate_p
        bk.consume(c.kalshi.side, opp.qty)
        bp.consume(c.polyus.side, opp.qty)
        self.last_fire[c.key] = {"ms": fired_ms, "vk": bk.version, "vp": bp.version}
        self.trades.append(t)
        self.counters["fired"] += 1
        _append(PAPER / "trades.jsonl", t)
        log.info("PAPER FIRE %s qty=%.2f K.%s@%.2f + P.%s@%.2f sum=%.2f fees=%.2f locked=$%.2f skew=%dms confirm=%dms",
                 pair.pair_id, opp.qty, c.kalshi.side, opp.vwap_k_c, c.polyus.side, opp.vwap_p_c, t["sum_c"],
                 opp.fee_k + opp.fee_p, opp.locked_pnl, opp.skew_ms, t["confirm_latency_ms"])

    # ------------------------------------------------------------ settlement

    def settle(self, kalshi_uid, result, settled_ms=None):
        """result: 'yes' | 'no' | 'tie'. Settles every open trade on this Kalshi market."""
        settled_ms = settled_ms or now_ms()
        n = 0
        for t in self.trades:
            if t["status"] != "open" or t["legs"][0]["market_uid"] != kalshi_uid:
                continue
            k_side, p_side, d = t["legs"][0]["side"], t["legs"][1]["side"], t["direction"]
            if result == "tie":
                pay_k = pay_p = 0.5 * t["qty"]
            else:
                k_pays = (k_side == "yes") == (result == "yes")
                p_yes_pays = (result == "yes") if d == "same" else (result == "no")
                p_pays = (p_side == "yes") == p_yes_pays
                pay_k, pay_p = (t["qty"] if k_pays else 0.0), (t["qty"] if p_pays else 0.0)
                assert (pay_k > 0) != (pay_p > 0), "exactly one leg must pay"
            self.cash["KALSHI"] += pay_k
            self.cash["POLYUS"] += pay_p
            pnl = round(pay_k + pay_p - t["cost_total"], 4)
            self.realised["KALSHI"] += pay_k - t["cost_k"] - t["fee_k"]
            self.realised["POLYUS"] += pay_p - t["cost_p"] - t["fee_p"]
            t.update(status="settled", settled_ms=settled_ms, result=result, payout=round(pay_k + pay_p, 4), settled_pnl=pnl)
            n += 1
        if n:
            self._rewrite_trades()
            log.info("settled %d trades on %s: result=%s", n, kalshi_uid, result)
        return n

    def _rewrite_trades(self):
        path = PAPER / "trades.jsonl"
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(t, default=str) + "\n" for t in self.trades))
        os.replace(tmp, path)

    # ------------------------------------------------------------ live view

    def live_rows(self):
        rows = []
        for pair in self.pairs.values():
            bk, bp = self.books.by_uid.get(pair.kalshi_uid), self.books.by_uid.get(pair.polyus_uid)
            for c in pair.combos:
                if bk is None or bp is None:
                    rows.append({"combo_key": c.key, "pair_id": pair.pair_id, "note": pair.note, "legs": c.describe(), "edge_c": None})
                    continue
                q = edge.quote(c, bk, bp, pair.fee_k, pair.fee_p)
                q.update(pair_id=pair.pair_id, note=pair.note, legs=c.describe(), kalshi_side=c.kalshi.side, polyus_side=c.polyus.side,
                         kalshi_uid=pair.kalshi_uid, polyus_uid=pair.polyus_uid, pending=c.key in self.pending,
                         book_state_p=bp.state, book_state_k=bk.state, tradeable=bk.tradeable and bp.tradeable)
                rows.append(q)
        rows.sort(key=lambda r: (r.get("edge_c") is None, -(r.get("edge_c") or -999)))
        return rows
