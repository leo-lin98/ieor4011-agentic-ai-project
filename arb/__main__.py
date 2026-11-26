"""Run the live monitor + paper trader for every active match_flags pair.

    python -m arb                       # streams, detects, paper-trades, writes data/paper and data/arb/live.json
    python -m arb --cap-side 1000 --pair-cap 10000 --skew-ms 500 --confirm-ms 300

Loops:
  books.changed  -> engine.on_book(uid)          every WebSocket message
  every 10 ms    -> engine.tick()                 confirm + fire (also run directly on a book update once CONFIRM_MS has passed)
  every 1 s      -> write state.json / live.json  (dashboard)
  every 60 s     -> reload active flags; subscribe new markets; drop deactivated pairs
  every 5 min    -> settlement check on Kalshi for markets with open paper positions
"""
import argparse
import asyncio
import glob
import logging
import sys
import time

import httpx
import pyarrow.parquet as pq

from collector import flags as F
from collector.config import DATA, KALSHI_BASE, USER_AGENT
from collector.store import read_catalog
from collector.util import iso_ms, now_ms, utc_date

from . import paper
from .books import Books, make_streams

log = logging.getLogger("arb")


def fee_params_from_t3():
    """Latest T3 row per market -> fee parameters (Kalshi fee_type/fee_multiplier; Polymarket theta stays the API default)."""
    out = {}
    for plat in ("KALSHI", "POLYUS"):
        files = sorted(glob.glob(str(DATA / "pricing_snapshots" / plat / "*" / "*.parquet")))[-3:]
        for f in files:
            t = pq.read_table(f, columns=["market_uid", "fee_type", "fee_multiplier", "fee_type_override", "fee_multiplier_override"])
            for u, ft, fm, fto, fmo in zip(*(t.column(i).to_pylist() for i in range(5))):
                if plat == "KALSHI":
                    out[u] = {"fee_type": fto or ft or "quadratic", "fee_multiplier": fmo if fmo is not None else (fm if fm is not None else 1.0)}
    return out


def pair_labels():
    """pair_id -> team labels from every candidate set (what each YES/NO side means, for logs and the Live tab)."""
    out = {}
    for f in glob.glob(str(DATA / "candidate_pairs" / "**" / "candidate_pairs.parquet"), recursive=True):
        t = pq.read_table(f, columns=["pair_id", "kalshi_yes", "polyus_yes"])
        for pid, ky, py in zip(*(t.column(i).to_pylist() for i in range(3))):
            out[pid] = {"kalshi_yes": ky, "polyus_yes": py, "polyus_no": None}
    return out


def polyus_slugs(uids):
    cat = read_catalog("POLYUS", ["market_uid", "market_slug"])
    return {s: u for u, s in zip(cat.column(0).to_pylist(), cat.column(1).to_pylist()) if u in uids and s}


async def kalshi_status_loop(engine, books, every_s=15):
    """Kalshi's stream carries no market status. Poll GET /markets?tickers=... (signed, batched) so a book is only
    tradeable while its market is `active` and before close_time. Also used for settlement detection."""
    from collector.auth import signers
    k_signer, _, _ = signers()
    async with httpx.AsyncClient(base_url=KALSHI_BASE, headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        while True:
            tickers = sorted({p.kalshi_uid.split(":", 1)[1] for p in engine.pairs.values()})
            for i in range(0, len(tickers), 100):
                chunk = tickers[i:i + 100]
                try:
                    hdr = k_signer.headers("GET", "/trade-api/v2/markets") if k_signer else {}
                    r = await client.get("/markets", params={"tickers": ",".join(chunk), "limit": len(chunk)}, headers=hdr)
                    for m in (r.json() or {}).get("markets") or []:
                        b = books.get(f"KALSHI:{m['ticker']}", "KALSHI")
                        b.state = m.get("status")
                        b.close_ms = iso_ms(m.get("close_time"))
                except Exception as e:  # noqa: BLE001
                    log.warning("kalshi status poll: %r", e)
            engine.counters["kalshi_status_polls"] += 1
            await asyncio.sleep(every_s)


async def settlement_loop(engine, every_s=300):
    async with httpx.AsyncClient(base_url=KALSHI_BASE, headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        while True:
            await asyncio.sleep(every_s)
            open_k = {t["legs"][0]["market_uid"] for t in engine.trades if t["status"] == "open"}
            for uid in sorted(open_k):
                try:
                    r = await client.get(f"/markets/{uid.split(':', 1)[1]}")
                    m = (r.json() or {}).get("market") or {}
                except Exception as e:  # noqa: BLE001
                    log.warning("settlement check %s: %r", uid, e)
                    continue
                st, res = m.get("status"), (m.get("result") or "").lower()
                if st in ("finalized", "settled", "determined") and res in ("yes", "no"):
                    engine.settle(uid, res)
                elif st in ("finalized", "settled") and res not in ("yes", "no"):
                    engine.settle(uid, "tie")


async def main(a):
    books = Books()
    engine = paper.Engine(books, cap_per_side=a.cap_side, pair_cap=a.pair_cap, max_skew_ms=a.skew_ms, confirm_ms=a.confirm_ms)
    flags = F.active_flags()
    if not flags:
        sys.exit("no active match_flags: run `python -m candidates.flag` first")
    fee_params = fee_params_from_t3()
    engine.set_pairs(flags, fee_params, pair_labels())
    k_tickers = sorted({p.kalshi_uid.split(":", 1)[1] for p in engine.pairs.values()})
    slugs = polyus_slugs({p.polyus_uid for p in engine.pairs.values()})
    log.info("%d pairs, %d kalshi markets, %d polyus markets", len(engine.pairs), len(k_tickers), len(slugs))
    ks, ps = make_streams(books, k_tickers, slugs)
    tasks = [asyncio.create_task(ks.run()), asyncio.create_task(ps.run()), asyncio.create_task(settlement_loop(engine)),
             asyncio.create_task(kalshi_status_loop(engine, books))]
    warned_clock = False

    async def consume():
        while True:
            uid = await books.changed.get()
            try:
                engine.on_book(uid)
            except Exception as e:  # noqa: BLE001
                log.exception("on_book %s: %r", uid, e)

    async def ticker():
        nonlocal warned_clock
        last_state = last_reload = time.monotonic()
        while True:
            await asyncio.sleep(0.01)
            try:
                engine.tick()
            except Exception as e:  # noqa: BLE001
                log.exception("tick: %r", e)
            now = time.monotonic()
            if now - last_state >= 1.0:
                clock = books.clock_report()
                engine.stream_stats = {**dict(books.stats), "kalshi_connected": ks.ws is not None, "polyus_connected": ps.ws is not None,
                                       "clock_offset_ms": clock}
                engine.write_state(engine.live_rows())
                last_state = now
                if not warned_clock and clock and max(abs(v) for v in clock.values()) > 50:
                    warned_clock = True
                    log.warning("clock offset vs exchanges %s ms (exchange ts - our receive ts). Our clock is %s; "
                                "check NTP (timedatectl / sntp). Skew between legs is unaffected (same clock both sides).",
                                clock, "slow" if max(clock.values()) > 0 else "fast")
            if now - last_reload >= a.reload_s:
                last_reload = now
                try:
                    new_k, new_p = engine.set_pairs(F.active_flags(), fee_params_from_t3(), pair_labels())
                    if new_k:
                        ks.add(u.split(":", 1)[1] for u in new_k)
                    if new_p:
                        ps.add(polyus_slugs(new_p))
                    if new_k or new_p:
                        log.info("flags reloaded: +%d kalshi, +%d polyus markets; %d pairs", len(new_k), len(new_p), len(engine.pairs))
                except Exception as e:  # noqa: BLE001
                    log.exception("flag reload: %r", e)

    tasks += [asyncio.create_task(consume()), asyncio.create_task(ticker())]
    try:
        await asyncio.gather(*tasks)
    finally:
        engine.write_state(engine.live_rows())


def cli():
    ap = argparse.ArgumentParser(prog="arb")
    ap.add_argument("--cap-side", type=float, default=paper.CAP_PER_SIDE, help="max $ cost per leg per paper trade")
    ap.add_argument("--pair-cap", type=float, default=paper.PAIR_CAP_PER_SIDE, help="max cumulative $ per leg per pair")
    ap.add_argument("--skew-ms", type=int, default=paper.MAX_SKEW_MS)
    ap.add_argument("--confirm-ms", type=int, default=paper.CONFIRM_MS)
    ap.add_argument("--reload-s", type=int, default=60)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(DATA / "logs" / f"arb_{utc_date(now_ms())}.log")])
    logging.Formatter.converter = time.gmtime
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(main(a))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    cli()
