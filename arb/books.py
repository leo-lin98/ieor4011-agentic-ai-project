"""Live order books for flagged markets, over both platforms' authenticated WebSockets (DECISIONS D11.2).

Kalshi   wss://external-api-ws.kalshi.com/trade-api/ws/v2, channel orderbook_delta: one snapshot per market,
         then deltas with a contiguous `seq`. Bids only (yes_dollars_fp / no_dollars_fp); asks are derived:
         YES ask = 100 - NO bid, NO ask = 100 - YES bid. A seq gap triggers a fresh snapshot.
Polymarket US  wss://api.polymarket.us/v1/ws/markets, SUBSCRIPTION_TYPE_MARKET_DATA: full book per update
         (bids / offers on the YES side, px.value dollars, qty decimal), <= 100 slugs per subscription.

Every book exposes asks(side) -> [(price_c, qty)] ascending, plus local receive time and the exchange's own
timestamp, so the edge calculator can measure skew between the two legs.
"""
import asyncio
import json
import logging
import time
from collections import defaultdict

import websockets

from collector.auth import signers
from collector.util import iso_ms, now_ms

log = logging.getLogger("arb.books")

KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_WS_PATH = "/trade-api/ws/v2"
POLYUS_WS = "wss://api.polymarket.us/v1/ws/markets"
POLYUS_WS_PATH = "/v1/ws/markets"


def _c(dollars):
    return round(float(dollars) * 100, 6)


class Book:
    """One binary market. Internally: YES bids and NO bids in cents (Kalshi's native form). Polymarket's YES
    offers are stored as NO bids at 100 - price, which is the same thing."""
    __slots__ = ("market_uid", "platform", "yes_bids", "no_bids", "recv_ms", "exch_ms", "version", "seq", "state", "stale",
                 "close_ms")

    def __init__(self, market_uid, platform):
        self.market_uid, self.platform = market_uid, platform
        self.yes_bids, self.no_bids = {}, {}
        self.recv_ms = self.exch_ms = None
        self.version = 0          # bumps on every change; consumers use it to detect "book changed"
        self.seq = None
        self.state = None         # Polymarket: MARKET_STATE_*; Kalshi: status from the REST poll (active, closed, ...)
        self.stale = False        # Kalshi: a seq gap was seen; book unusable until the next snapshot
        self.close_ms = None      # Kalshi: close_time from the REST poll

    @property
    def tradeable(self):
        if self.stale:
            return False
        if self.platform == "POLYUS":
            return self.state == "MARKET_STATE_OPEN"
        return self.state == "active" and (self.close_ms is None or self.close_ms > now_ms())

    MIN_QTY = 0.01   # Polymarket's minimum trade quantity; smaller resting dust cannot be hit

    def asks(self, side):
        """Ascending [(price_c, qty)] for BUYING `side` (yes|no). Levels below MIN_QTY are dust and ignored."""
        src = self.no_bids if side == "yes" else self.yes_bids
        return sorted(((round(100 - p, 6), q) for p, q in src.items() if q >= self.MIN_QTY))

    def best_ask(self, side):
        a = self.asks(side)
        return a[0] if a else (None, 0.0)

    def consume(self, side, qty):
        """Paper fill: remove `qty` from the ask ladder of `side` (best levels first)."""
        src = self.no_bids if side == "yes" else self.yes_bids
        for p in sorted(src, reverse=True):   # highest opposing bid = best ask
            if qty <= 0:
                break
            take = min(src[p], qty)
            src[p] -= take
            qty -= take
            if src[p] <= 1e-9:
                del src[p]
        self.version += 1

    def replace(self, yes_bids, no_bids, recv_ms, exch_ms, state=None):
        self.yes_bids, self.no_bids = dict(yes_bids), dict(no_bids)
        self.recv_ms, self.exch_ms = recv_ms, exch_ms
        if state is not None:
            self.state = state
        self.stale = False
        self.version += 1

    def apply_delta(self, side, price_c, delta, recv_ms, exch_ms):
        d = self.yes_bids if side == "yes" else self.no_bids
        q = d.get(price_c, 0.0) + delta
        if q <= 1e-9:
            d.pop(price_c, None)
        else:
            d[price_c] = q
        self.recv_ms, self.exch_ms = recv_ms, exch_ms or self.exch_ms
        self.version += 1

    def snapshot(self):
        return {"yes": self.asks("yes")[:5], "no": self.asks("no")[:5], "recv_ms": self.recv_ms, "exch_ms": self.exch_ms,
                "state": self.state, "version": self.version}


class Books:
    """All books plus an asyncio queue of market_uids whose book changed."""

    def __init__(self):
        self.by_uid = {}
        self.changed = asyncio.Queue()
        self.stats = defaultdict(int)
        self.clock_offset = {"KALSHI": []}   # Kalshi message ts_ms - our receive ts, recent samples (ms)

    def sample_clock(self, platform, exch_ms, recv_ms):
        """Track how far our clock is from Kalshi's (its ts_ms is a message timestamp). Positive = Kalshi stamped
        the message AFTER we received it, i.e. our clock is slow. A warning and a Live-tab figure; not for trading."""
        if platform not in self.clock_offset:
            return
        if exch_ms is None:
            return
        s = self.clock_offset[platform]
        s.append(exch_ms - recv_ms)
        if len(s) > 200:
            del s[:100]

    def clock_report(self):
        out = {}
        for p, s in self.clock_offset.items():
            if s:
                srt = sorted(s)
                out[p] = srt[len(srt) // 2]
        return out

    def get(self, uid, platform):
        if uid not in self.by_uid:
            self.by_uid[uid] = Book(uid, platform)
        return self.by_uid[uid]

    def notify(self, uid):
        self.stats["updates"] += 1
        self.changed.put_nowait(uid)


# ---------------------------------------------------------------- Kalshi

class KalshiStream:
    def __init__(self, books: Books, tickers, signer):
        self.books, self.tickers, self.signer = books, set(tickers), signer
        self.ws = None
        self.sid = None
        self.seq = None
        self.next_id = 1

    def add(self, tickers):
        new = set(tickers) - self.tickers
        self.tickers |= new
        return new

    async def run(self):
        backoff = 1
        while True:
            try:
                await self._session()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("kalshi ws: %r; reconnecting in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _send(self, cmd, params):
        msg = {"id": self.next_id, "cmd": cmd, "params": params}
        self.next_id += 1
        await self.ws.send(json.dumps(msg))

    async def _session(self):
        headers = self.signer.headers("GET", KALSHI_WS_PATH)
        async with websockets.connect(KALSHI_WS, additional_headers=headers, ping_interval=None, max_size=None) as ws:
            self.ws, self.seq, self.sid = ws, None, None
            await self._send("subscribe", {"channels": ["orderbook_delta"], "market_tickers": sorted(self.tickers)})
            log.info("kalshi ws connected, %d markets", len(self.tickers))
            pending = set()
            async for raw in ws:
                recv = now_ms()
                m = json.loads(raw)
                t = m.get("type")
                seq = m.get("seq")
                if seq is not None:   # every message on the subscription carries a seq, including the subscribed ack
                    if self.seq is not None and seq != self.seq + 1:
                        log.warning("kalshi ws seq gap %s -> %s; books stale until re-snapshotted", self.seq, seq)
                        self.books.stats["kalshi_gaps"] += 1
                        for b in self.books.by_uid.values():
                            if b.platform == "KALSHI":
                                b.stale = True
                        await self._send("update_subscription", {"sids": [self.sid], "market_tickers": sorted(self.tickers),
                                                                 "action": "get_snapshot"})
                    self.seq = seq
                if t == "subscribed":
                    self.sid = m.get("msg", {}).get("sid")
                    continue
                if t == "error":
                    log.warning("kalshi ws error: %s", m)
                    continue
                if t not in ("orderbook_snapshot", "orderbook_delta"):
                    continue
                msg = m["msg"]
                uid = f"KALSHI:{msg['market_ticker']}"
                b = self.books.get(uid, "KALSHI")
                if t == "orderbook_snapshot":
                    yes = {_c(p): float(q) for p, q in msg.get("yes_dollars_fp") or []}
                    no = {_c(p): float(q) for p, q in msg.get("no_dollars_fp") or []}
                    b.replace(yes, no, recv, msg.get("ts_ms"))        # clears stale
                else:
                    b.apply_delta(msg["side"], _c(msg["price_dollars"]), float(msg["delta_fp"]), recv, msg.get("ts_ms"))
                self.books.sample_clock("KALSHI", msg.get("ts_ms"), recv)
                self.books.notify(uid)
                # markets added while connected
                new = self.tickers - pending - {u.split(":", 1)[1] for u in self.books.by_uid if u.startswith("KALSHI:")}
                if new:
                    pending |= new
                    await self._send("update_subscription", {"sids": [self.sid], "market_tickers": sorted(new), "action": "add_markets"})


# ---------------------------------------------------------------- Polymarket US

class PolyusStream:
    def __init__(self, books: Books, slug_to_uid, signer):
        self.books, self.slug_to_uid, self.signer = books, dict(slug_to_uid), signer
        self.ws = None
        self.subscribed = set()

    def add(self, slug_to_uid):
        new = {s: u for s, u in slug_to_uid.items() if s not in self.slug_to_uid}
        self.slug_to_uid.update(new)
        return set(new)

    async def run(self):
        backoff = 1
        while True:
            try:
                await self._session()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("polyus ws: %r; reconnecting in %ss", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _subscribe(self, slugs):
        slugs = sorted(slugs)
        for i in range(0, len(slugs), 100):
            chunk = slugs[i:i + 100]
            await self.ws.send(json.dumps({"subscribe": {"requestId": f"md-{i // 100}-{int(time.time())}",
                                                         "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA",
                                                         "marketSlugs": chunk, "responsesDebounced": False}}))
            self.subscribed |= set(chunk)

    async def _session(self):
        headers = self.signer.headers("GET", POLYUS_WS_PATH)
        async with websockets.connect(POLYUS_WS, additional_headers=headers, ping_interval=20, max_size=None) as ws:
            self.ws, self.subscribed = ws, set()
            await self._subscribe(self.slug_to_uid)
            log.info("polyus ws connected, %d markets", len(self.slug_to_uid))
            async for raw in ws:
                recv = now_ms()
                m = json.loads(raw)
                if "heartbeat" in m:
                    continue
                md = m.get("marketData") or m.get("market_data")
                if not md:
                    if "error" in m or "subscribeResponse" in m or "subscribe_response" in m:
                        log.info("polyus ws: %s", str(m)[:300])
                    continue
                slug = md.get("marketSlug") or md.get("market_slug") or md.get("slug") or m.get("marketSlug")
                uid = self.slug_to_uid.get(slug)
                if not uid:
                    self.books.stats["polyus_unknown_slug"] += 1
                    continue
                yes_bids = {_c(x["px"]["value"]): float(x["qty"]) for x in md.get("bids") or []}
                no_bids = {round(100 - _c(x["px"]["value"]), 6): float(x["qty"]) for x in md.get("offers") or []}
                b = self.books.get(uid, "POLYUS")
                # transactTime is the book's LAST TRANSACTION time, not the message send time (observed 25 s behind
                # on a quiet book), so it is stored for the record but never used for clock or staleness checks.
                exch = iso_ms(md.get("transactTime") or md.get("transact_time"))
                b.replace(yes_bids, no_bids, recv, exch, md.get("state"))
                self.books.notify(uid)
                new = set(self.slug_to_uid) - self.subscribed
                if new:
                    await self._subscribe(new)


def make_streams(books, kalshi_tickers, polyus_slug_to_uid):
    k, p, problems = signers()
    if problems:
        raise SystemExit("API keys: " + "; ".join(problems))
    return KalshiStream(books, kalshi_tickers, k), PolyusStream(books, polyus_slug_to_uid, p)
