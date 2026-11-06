"""CLI.

  python -m collector once                 # catalog + one pricing tick + counts + flagged books
  python -m collector run                  # long-running scheduler (Ctrl+C to stop)
  python -m collector catalog              # refresh market_catalog for both platforms
  python -m collector books                # one order-book pull for flagged markets
  python -m collector flag add KALSHI:<ticker> POLYUS:<id> [--relationship identical] [--note ...]
  python -m collector flag list | flag off <pair_id> | flag on <pair_id>
  python -m collector status               # what is on disk
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone

from . import config
from . import flags as flagstore
from .runner import Collector
from .util import now_ms, utc_date


def setup_logging(verbose=False):
    (config.DATA / "logs").mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers = [logging.StreamHandler(sys.stdout),
                logging.FileHandler(config.DATA / "logs" / f"collector_{utc_date(now_ms())}.log")]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, handlers=handlers)
    logging.Formatter.converter = time.gmtime  # log times in UTC
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_status(_):
    root = config.DATA
    if not root.exists():
        print("no data yet")
        return
    for table in sorted(p for p in root.iterdir() if p.is_dir()):
        files = [f for f in table.rglob("*") if f.is_file() and not f.name.endswith(".tmp")]
        size = sum(f.stat().st_size for f in files)
        latest = max((f.stat().st_mtime for f in files), default=None)
        when = datetime.fromtimestamp(latest, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if latest else "-"
        print(f"{table.name:22s} files={len(files):6d} size={size / 1e6:9.1f}MB latest={when}")
    flags = flagstore.load_flags()
    print(f"match_flags: {len(flags)} total, {sum(1 for f in flags if f['is_active'])} active")


async def amain(args):
    c = Collector(price_interval=args.price_interval, book_interval=args.book_interval,
                  catalog_interval=args.catalog_interval, stats_sweep=not args.no_stats_sweep)
    try:
        if args.cmd == "once":
            tick, res, books = await c.run_once()
            print(json.dumps({"tick_ts": tick, "pricing": res, "books": books}, indent=2, default=str))
        elif args.cmd == "run":
            await c.run_forever()
        elif args.cmd == "catalog":
            await c.polyus_catalog()
            prev, c.catalog_interval = c.catalog_interval, 0
            await c.ensure_series()
            c.catalog_interval = prev
            r = await c.kalshi_tick((now_ms() // 1000) * 1000, with_catalog=True)
            print(json.dumps(r, indent=2, default=str))
        elif args.cmd == "books":
            print(await c.book_tick((now_ms() // 1000) * 1000))
    finally:
        await c.aclose()


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--price-interval", type=float, default=config.PRICE_INTERVAL_S)
    common.add_argument("--book-interval", type=float, default=config.BOOK_INTERVAL_S)
    common.add_argument("--catalog-interval", type=float, default=config.CATALOG_INTERVAL_S)
    common.add_argument("--no-stats-sweep", action="store_true", help="disable background Polymarket US /bbo sweep")
    ap = argparse.ArgumentParser(prog="collector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("once", "run", "catalog", "books"):
        sub.add_parser(name, parents=[common])
    sub.add_parser("status")
    sub.add_parser("display", help="rebuild data/display/*.ipynb (static view of each table)")
    sub.add_parser("profiles", help="rebuild T0 market_profiles from the current catalog")
    fl = sub.add_parser("flag")
    fsub = fl.add_subparsers(dest="flag_cmd", required=True)
    fa = fsub.add_parser("add")
    fa.add_argument("kalshi_uid")
    fa.add_argument("polyus_uid")
    fa.add_argument("--relationship", default="identical", choices=["identical", "complementary", "basket", "none"])
    fa.add_argument("--note")
    fsub.add_parser("list")
    for name in ("on", "off"):
        p = fsub.add_parser(name)
        p.add_argument("pair_id")
    args = ap.parse_args()

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "profiles":
        from .profiles import build_profiles
        print("market_profiles rows:", build_profiles())
        return
    if args.cmd == "display":
        from .display import build_all
        for p in build_all():
            print("wrote", p)
        return
    if args.cmd == "flag":
        if args.flag_cmd == "add":
            row, (k_ok, p_ok) = flagstore.add_flag(args.kalshi_uid, args.polyus_uid, args.relationship, args.note)
            print("added", row["pair_id"])
            if not (k_ok and p_ok):
                print("warning: market not found in catalog yet:",
                      [u for u, ok in ((args.kalshi_uid, k_ok), (args.polyus_uid, p_ok)) if not ok])
        elif args.flag_cmd == "list":
            for f in flagstore.load_flags():
                print(("ACTIVE " if f["is_active"] else "off    ") + f["pair_id"], f["relationship"],
                      f["rules_review_status"] or "", f["note"] or "")
        else:
            flagstore.set_active(args.pair_id, args.flag_cmd == "on")
            print(args.flag_cmd, args.pair_id)
        return

    setup_logging(args.verbose)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        logging.getLogger("collector").info("stopped")


if __name__ == "__main__":
    main()
