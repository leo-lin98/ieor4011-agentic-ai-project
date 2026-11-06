"""Collection jobs and the long-running scheduler."""
import asyncio
import logging
import time

from . import flags as flagstore
from . import kalshi, polyus
from .config import (BOOK_INTERVAL_S, CATALOG_INTERVAL_S, HEARTBEAT_S, KALSHI_BASE, KALSHI_RATE,
                     POLYUS_BASE, POLYUS_RATE, POLYUS_STATS_SWEEP_RPS, RAW_ARCHIVE_DAILY, STATS_FLUSH_S,
                     PRICE_INTERVAL_S)
from .http import AdaptiveLimiter, Api
from .profiles import build_profiles
from .schemas import MARKET_STATS, ORDERBOOK_SNAPSHOTS, PLATFORM_COUNTS, PRICING_SNAPSHOTS
from .store import (CatalogWriter, ParquetSink, RawArchive, catalog_current_path, raw_due, read_catalog,
                    tick_path, write_rows)
from .util import now_ms

log = logging.getLogger("collector")

SIG_FIELDS = [f for f in PRICING_SNAPSHOTS.names
              if f not in ("tick_ts", "request_sent_ts", "response_received_ts", "write_reason")]


class ChangeFilter:
    """Change-only writes: emit a row when anything but timestamps changed, or on heartbeat."""

    def __init__(self, heartbeat_ms):
        self.heartbeat_ms = heartbeat_ms
        self.last = {}

    def check(self, row, tick_ts):
        sig = tuple(row[k] for k in SIG_FIELDS)
        prev = self.last.get(row["market_uid"])
        if prev is None:
            reason = "first"
        elif prev[0] != sig:
            reason = "change"
        elif tick_ts - prev[1] >= self.heartbeat_ms:
            reason = "heartbeat"
        else:
            return None
        self.last[row["market_uid"]] = (sig, tick_ts)
        return reason


def next_boundary(interval_s):
    ms = now_ms()
    step = int(interval_s * 1000)
    return (ms // step + 1) * step


async def sleep_until(ts_ms):
    delay = (ts_ms - now_ms()) / 1000
    if delay > 0:
        await asyncio.sleep(delay)


class Collector:
    def __init__(self, price_interval=PRICE_INTERVAL_S, book_interval=BOOK_INTERVAL_S,
                 catalog_interval=CATALOG_INTERVAL_S, stats_sweep=True):
        self.price_interval = price_interval
        self.book_interval = book_interval
        self.catalog_interval = catalog_interval
        self.stats_sweep_enabled = stats_sweep
        self.k = Api("KALSHI", KALSHI_BASE, AdaptiveLimiter("kalshi", **KALSHI_RATE))
        self.p = Api("POLYUS", POLYUS_BASE, AdaptiveLimiter("polyus", **POLYUS_RATE))
        self.series, self.series_ms = {}, 0
        self.filters = {"KALSHI": ChangeFilter(HEARTBEAT_S * 1000), "POLYUS": ChangeFilter(HEARTBEAT_S * 1000)}
        self.status = {}        # market_uid -> status_canonical (latest tick)
        self.polyus_slug = {}   # market_uid -> slug
        self.polyus_event = {}  # market_uid -> event_uid (from catalog)
        self.book_hash = {}     # market_uid -> last written book hash
        self.first_tick_done = asyncio.Event()  # stats sweep waits for a full status map
        self._load_polyus_maps()

    async def aclose(self):
        await self.k.aclose()
        await self.p.aclose()

    # ------------------------------------------------------------ helpers

    def _load_polyus_maps(self):
        cat = read_catalog("POLYUS", ["market_uid", "event_uid", "market_slug"])
        if cat is None:
            return
        for u, e, sl in zip(*(cat.column(i).to_pylist() for i in range(3))):
            self.polyus_event[u] = e
            if sl:
                self.polyus_slug.setdefault(u, sl)

    def catalog_due(self, platform):
        p = catalog_current_path(platform)
        return not p.exists() or (time.time() - p.stat().st_mtime) >= self.catalog_interval

    async def ensure_series(self):
        if not self.series or now_ms() - self.series_ms >= self.catalog_interval * 1000:
            self.series, _ = await kalshi.fetch_series(self.k)
            self.series_ms = now_ms()
            log.info("kalshi series loaded: %d", len(self.series))

    # ------------------------------------------------------------ pricing (+ Kalshi catalog)

    async def kalshi_tick(self, tick_ts, with_catalog):
        await self.ensure_series()
        t0, req0, thr0 = time.monotonic(), self.k.requests, self.k.throttles
        sink = ParquetSink(tick_path("pricing_snapshots", "KALSHI", tick_ts, "pricing"), PRICING_SNAPSHOTS)
        cw = CatalogWriter("KALSHI", tick_ts) if with_catalog else None
        raw = RawArchive("KALSHI", tick_ts) if with_catalog and RAW_ARCHIVE_DAILY and raw_due("KALSHI", tick_ts) else None
        n_markets = n_open = open_events = nbytes = 0
        try:
            async for events, r in kalshi.iter_open_event_pages(self.k):
                nbytes += r.nbytes
                if raw:
                    raw.write("GET /events", r, events)
                prow, crow = [], []
                for e in events:
                    ser = self.series.get(e.get("series_ticker"))
                    ev_open = False
                    for m in e.get("markets") or []:
                        row = kalshi.pricing_row(e, m, ser, tick_ts, r)
                        self.status[row["market_uid"]] = row["status_canonical"]
                        n_markets += 1
                        if row["status_canonical"] == "open":
                            n_open += 1
                            ev_open = True
                        reason = self.filters["KALSHI"].check(row, tick_ts)
                        if reason:
                            row["write_reason"] = reason
                            prow.append(row)
                        if cw:
                            crow.append(kalshi.catalog_row(e, m, ser, tick_ts))
                    open_events += ev_open
                await asyncio.to_thread(sink.write, prow)
                if cw:
                    await asyncio.to_thread(cw.write, crow)
        except BaseException:
            sink.abort()
            if cw:
                cw.abort()
            if raw:
                raw.abort()
            raise
        written = sink.close()
        cat = None
        if cw:
            cat = await asyncio.to_thread(cw.close)
            stale = flagstore.mark_stale("KALSHI", cw.rules)
            log.info("kalshi catalog: %s stale_flags=%d", cat, stale)
            await self.rebuild_profiles()
        if raw:
            raw.close()
        return {"platform": "KALSHI", "open_events": open_events, "open_markets": n_open, "markets_seen": n_markets,
                "rows_written": written, "sweep_requests": self.k.requests - req0,
                "sweep_throttles": self.k.throttles - thr0, "sweep_seconds": round(time.monotonic() - t0, 2),
                "sweep_mb": round(nbytes / 1e6, 2), "catalog": cat}

    async def polyus_tick(self, tick_ts):
        t0, req0, thr0 = time.monotonic(), self.p.requests, self.p.throttles
        sink = ParquetSink(tick_path("pricing_snapshots", "POLYUS", tick_ts, "pricing"), PRICING_SNAPSHOTS)
        seen, open_events = set(), set()
        n_open = nbytes = 0
        try:
            async for markets, r in polyus.iter_market_pages(self.p):
                nbytes += r.nbytes
                prow = []
                for m in markets:
                    row = polyus.pricing_row(m, tick_ts, r)
                    u = row["market_uid"]
                    if u in seen:
                        continue
                    seen.add(u)
                    self.status[u] = row["status_canonical"]
                    if m.get("slug"):
                        self.polyus_slug[u] = m["slug"]
                    if row["status_canonical"] == "open":
                        n_open += 1
                        ev = self.polyus_event.get(u)
                        if ev:
                            open_events.add(ev)
                    reason = self.filters["POLYUS"].check(row, tick_ts)
                    if reason:
                        row["write_reason"] = reason
                        prow.append(row)
                await asyncio.to_thread(sink.write, prow)
        except BaseException:
            sink.abort()
            raise
        written = sink.close()
        unmapped = sum(1 for u in seen if u not in self.polyus_event)
        if unmapped:
            log.info("polyus: %d priced markets not yet in catalog (picked up next catalog refresh)", unmapped)
        return {"platform": "POLYUS", "open_events": len(open_events), "open_markets": n_open,
                "markets_seen": len(seen), "rows_written": written, "sweep_requests": self.p.requests - req0,
                "sweep_throttles": self.p.throttles - thr0, "sweep_seconds": round(time.monotonic() - t0, 2),
                "sweep_mb": round(nbytes / 1e6, 2)}

    async def polyus_catalog(self):
        pulled = now_ms()
        t0, req0 = time.monotonic(), self.p.requests
        cw = CatalogWriter("POLYUS", pulled)
        raw = RawArchive("POLYUS", pulled) if RAW_ARCHIVE_DAILY and raw_due("POLYUS", pulled) else None
        n_events = 0
        try:
            async for events, r in polyus.iter_catalog_events(self.p, raw=raw):
                rows = []
                for e in events:
                    n_events += 1
                    for m in e.get("markets") or []:
                        rows.append(polyus.catalog_row(e, m, pulled))
                await asyncio.to_thread(cw.write, rows)
        except BaseException:
            cw.abort()
            if raw:
                raw.abort()
            raise
        cat = await asyncio.to_thread(cw.close)
        if raw:
            raw.close()
        self._load_polyus_maps()
        stale = flagstore.mark_stale("POLYUS", cw.rules)
        cat.update(events=n_events, requests=self.p.requests - req0, seconds=round(time.monotonic() - t0, 1))
        log.info("polyus catalog: %s stale_flags=%d", cat, stale)
        await self.rebuild_profiles()
        return cat

    async def rebuild_profiles(self):
        n = await asyncio.to_thread(build_profiles)
        log.info("market_profiles rebuilt: %d open markets", n)

    async def price_tick(self, tick_ts):
        k_cat = self.catalog_due("KALSHI")
        res = await asyncio.gather(self.kalshi_tick(tick_ts, k_cat), self.polyus_tick(tick_ts),
                                   return_exceptions=True)
        out = []
        active = flagstore.active_flags()
        open_both = sum(1 for f in active if self.status.get(f["kalshi_market_uid"]) == "open"
                        and self.status.get(f["polyus_market_uid"]) == "open")
        for r in res:
            if isinstance(r, BaseException):
                log.error("pricing tick %d failed: %r", tick_ts, r)
                continue
            row = {k: r[k] for k in PLATFORM_COUNTS.names if k in r}
            row.update(tick_ts=tick_ts, flagged_pairs_active=len(active), flagged_pairs_open_both=open_both)
            write_rows("platform_counts", PLATFORM_COUNTS, r["platform"], tick_ts, [row], "counts")
            log.info("tick %d %s open_markets=%d open_events=%d written=%d req=%d 429s=%d %.1fs %.0fMB",
                     tick_ts, r["platform"], r["open_markets"], r["open_events"], r["rows_written"],
                     r["sweep_requests"], r["sweep_throttles"], r["sweep_seconds"], r["sweep_mb"])
            out.append(r)
        return out

    # ------------------------------------------------------------ order books (flagged markets)

    async def book_tick(self, tick_ts):
        active = flagstore.active_flags()
        uids = sorted({f["kalshi_market_uid"] for f in active} | {f["polyus_market_uid"] for f in active})
        if not uids:
            return {"markets": 0}

        async def one(u):
            if u.startswith("KALSHI:"):
                r = await self.k.get(f"/markets/{u.split(':', 1)[1]}/orderbook", priority=0)
                rows, h = kalshi.book_rows(u, r.data, tick_ts, r)
                return u, rows, None, h, r.status
            slug = self.polyus_slug.get(u)
            if not slug:
                log.warning("no slug for %s yet; skipping book", u)
                return u, [], None, None, None
            r = await self.p.get(f"/v1/markets/{slug}/book", priority=0)
            rows, stats, h = polyus.book_rows(u, r.data, tick_ts, r)
            return u, rows, stats, h, r.status

        res = await asyncio.gather(*[one(u) for u in uids], return_exceptions=True)
        books = {"KALSHI": [], "POLYUS": []}
        stats = []
        for item in res:
            if isinstance(item, BaseException):
                log.error("book fetch failed: %r", item)
                continue
            u, rows, st, h, status = item
            if status == 404:
                log.warning("book 404 for %s", u)
            if st:
                stats.append(st)
            if rows and self.book_hash.get(u) != h:
                self.book_hash[u] = h
                books[u.split(":", 1)[0]].extend(rows)
        n = 0
        for plat, rows in books.items():
            n += await asyncio.to_thread(write_rows, "orderbook_snapshots", ORDERBOOK_SNAPSHOTS, plat, tick_ts,
                                         rows, "books")
        await asyncio.to_thread(write_rows, "market_stats", MARKET_STATS, "POLYUS", tick_ts, stats, "stats_book")
        return {"markets": len(uids), "level_rows_written": n}

    # ------------------------------------------------------------ background Polymarket US volume sweep

    async def stats_sweep_loop(self):
        buf, last_flush = [], time.monotonic()
        gap = 1.0 / POLYUS_STATS_SWEEP_RPS
        await self.first_tick_done.wait()
        try:
            while True:
                targets = [(u, sl) for u, sl in self.polyus_slug.items() if self.status.get(u) == "open"]
                if not targets:
                    await asyncio.sleep(10)
                    continue
                log.info("polyus stats sweep: %d markets (~%.0f min)", len(targets), len(targets) * gap / 60)
                for u, sl in targets:
                    t = time.monotonic()
                    try:
                        r = await self.p.get(f"/v1/markets/{sl}/bbo", priority=2)
                        row = polyus.bbo_stats_row(u, r.data, now_ms(), r)
                        if row:
                            buf.append(row)
                    except Exception as e:  # keep sweeping
                        log.warning("bbo %s failed: %r", sl, e)
                    if buf and (len(buf) >= 5000 or time.monotonic() - last_flush >= STATS_FLUSH_S):
                        await asyncio.to_thread(write_rows, "market_stats", MARKET_STATS, "POLYUS", now_ms(),
                                                buf, "stats_bbo")
                        buf, last_flush = [], time.monotonic()
                    await asyncio.sleep(max(0.0, gap - (time.monotonic() - t)))
        finally:  # flush partial buffer on shutdown
            if buf:
                write_rows("market_stats", MARKET_STATS, "POLYUS", now_ms(), buf, "stats_bbo")

    # ------------------------------------------------------------ loops

    async def price_loop(self):
        while True:
            tick = next_boundary(self.price_interval)
            await sleep_until(tick)
            await self.price_tick(tick)
            self.first_tick_done.set()
            late = now_ms() - tick
            if late > self.price_interval * 1000:
                log.warning("pricing tick took %.1fs (> %ss interval); next boundary skipped", late / 1000,
                            self.price_interval)

    async def book_loop(self):
        while True:
            tick = next_boundary(self.book_interval)
            await sleep_until(tick)
            try:
                await self.book_tick(tick)
            except Exception as e:
                log.error("book tick failed: %r", e)

    async def polyus_catalog_loop(self):
        while True:
            if self.catalog_due("POLYUS"):
                try:
                    await self.polyus_catalog()
                except Exception as e:
                    log.error("polyus catalog failed: %r", e)
            await asyncio.sleep(60)

    async def run_forever(self):
        tasks = [self.price_loop(), self.book_loop(), self.polyus_catalog_loop()]
        if self.stats_sweep_enabled:
            tasks.append(self.stats_sweep_loop())
        await asyncio.gather(*tasks)

    async def run_once(self):
        """Catalog + one aligned pricing tick + counts + flagged books. Used for sizing / smoke tests."""
        await asyncio.gather(self.polyus_catalog(), self.ensure_series())
        tick = (now_ms() // 1000) * 1000
        prev_interval, self.catalog_interval = self.catalog_interval, 0  # force Kalshi catalog this tick
        try:
            res = await self.price_tick(tick)
        finally:
            self.catalog_interval = prev_interval
        books = await self.book_tick(tick)
        return tick, res, books
