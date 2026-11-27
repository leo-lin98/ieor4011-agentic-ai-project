"""Run bounded non-sports retrieval, proposal, and rule review from one command."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from collector.config import DATA, ROOT

from . import core

# Keep Leo's existing CLI and T2 adapter. Point their outputs at this run so the
# sports refresh loop and earlier proposal runs can keep using their own data.
PROPOSER_ENTRY = """
from pathlib import Path
import sys
from collector import flags
from proposer import config
from proposer.__main__ import main
run = Path(sys.argv[1])
flags.FLAGS_PATH = run / "match_flags.parquet"
config.PROPOSALS_DIR = run / "proposals"
main(sys.argv[2:])
"""


def _snapshot_inputs(data, run, queries):
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    profiles = pq.read_table(data / "market_profiles" / "market_profiles.parquet")
    categories = pc.utf8_lower(pc.utf8_trim_whitespace(profiles["category"]))
    non_sports = profiles.filter(pc.fill_null(pc.and_(pc.not_equal(categories, "sports"),
                                                     pc.not_equal(categories, "")), False))
    kalshi = non_sports.filter(pc.equal(non_sports["platform"], "KALSHI"))
    available = set(kalshi["event_uid"].to_pylist())
    if not available:
        raise ValueError("No categorized non-sports Kalshi profiles are available")
    missing = sorted(set(queries) - available)
    if missing:
        raise ValueError(f"Expected non-sports Kalshi events in the profiles: {', '.join(missing)}")
    if "POLYUS" not in non_sports["platform"].to_pylist():
        raise ValueError("No categorized non-sports Polymarket US profiles are available")

    catalog = run / "catalog"
    catalog.mkdir()
    for platform in ("KALSHI", "POLYUS"):
        file = f"{platform}.parquet"
        shutil.copyfile(data / "market_catalog" / "current" / file, catalog / file)
    pq.write_table(non_sports, run / "profiles.parquet")


def _review_input(run):
    import pyarrow.parquet as pq

    flags = run / "match_flags.parquet"
    rows = pq.read_table(flags, filters=[("source", "=", "proposer")]).to_pylist() if flags.exists() else []
    path = run / "pairs.json"
    path.write_text(json.dumps(rows, indent=2) + "\n")
    return path


def run_pipeline(args):
    proposer = args.proposer_root.resolve()
    data = args.data_dir.resolve() if args.data_dir else proposer / "data"
    cache = args.cache.resolve() if args.cache else DATA / "embeddings" / "pipeline-events.sqlite3"
    for entry in ("embeddings/embed.py", "proposer/__main__.py"):
        if not (proposer / entry).is_file():
            raise ValueError(f"{entry} is missing. Set --proposer-root to Leo's checkout.")
    if not args.dry_run and not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("Export OPENAI_API_KEY in this terminal before running the proposer and judge.")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    print(f"Run directory: {run}", flush=True)
    if args.live:
        data = run / "live"
        refresh_env = {**os.environ, "IEOR_COLLECTOR_DATA_DIR": str(data)}
        subprocess.run([sys.executable, "-m", "collector", "catalog", "--no-stats-sweep"],
                       cwd=ROOT, env=refresh_env, check=True)
    _snapshot_inputs(data, run, args.query_id or [])
    cache.parent.mkdir(parents=True, exist_ok=True)
    candidates = run / "candidates.json"
    command = [
        sys.executable, "-m", "embeddings.embed", "--profiles", str(run / "profiles.parquet"),
        "--output", str(candidates), "--cache", str(cache), "--top-k", "3",
        "--model", args.embedding_model,
    ]
    if args.query_id:
        for uid in args.query_id:
            command.extend(["--query-id", uid])
    else:
        command.extend(["--queries", str(args.queries or 3)])
    subprocess.run(command, cwd=proposer, check=True)
    if args.dry_run:
        print(f"Retrieval complete: {candidates}")
        return run

    subprocess.run([
        sys.executable, "-c", PROPOSER_ENTRY, str(run), "--candidates", str(candidates),
        "--concurrency", str(args.concurrency),
    ], cwd=proposer, check=True)
    pairs = _review_input(run)
    report = run / "reviews.json"
    pending = run / "reviews.pending"
    if args.tier or (args.live and not args.structured):
        command = [sys.executable, "-m", "judge", "tier", "run", "--pairs", str(pairs)]
    else:
        command = [sys.executable, "-m", "judge", "pairs", "--input", str(pairs)]
        if args.structured:
            command.append("--structured")
        if args.check_order:
            command.append("--check-order")
    command.extend(["--catalog-dir", str(run / "catalog"), "--model", args.judge_model])
    with pending.open("w") as stream:
        subprocess.run(command, cwd=ROOT, stdout=stream, check=True)
    pending.replace(report)
    print(f"Rules review complete: {report}")
    return run


def main(argv=None):
    parser = argparse.ArgumentParser(prog="judge pipeline", description=__doc__)
    parser.add_argument("--proposer-root", type=Path, default=ROOT,
                        help="Checkout containing Leo's embeddings and proposer modules")
    parser.add_argument("--data-dir", type=Path,
                        help="Collector data directory. Defaults to data in the proposer checkout.")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--query-id", action="append",
                           help="Kalshi non-sports event UID. Repeat to review multiple events.")
    selection.add_argument("--queries", type=int, help="Sample this many open non-sports Kalshi events (default 3).")
    parser.add_argument("--live", action="store_true", help="Refresh both venue catalogs before matching open markets.")
    parser.add_argument("--output-dir", type=Path, default=DATA / "non_sports_runs")
    parser.add_argument("--cache", type=Path, help="Optional existing embeddings cache")
    parser.add_argument("--embedding-model", default="google/embeddinggemma-300m")
    parser.add_argument("--judge-model", default=core.MODEL)
    parser.add_argument("--concurrency", type=int, default=2)
    judge_mode = parser.add_mutually_exclusive_group()
    judge_mode.add_argument("--structured", action="store_true", help="Use fixed-scenario payout comparison")
    judge_mode.add_argument("--tier", action="store_true", help="Use settlement-risk tiers (default for --live)")
    parser.add_argument("--check-order", action="store_true", help="Reassess the same scenarios in reverse venue order")
    parser.add_argument("--dry-run", action="store_true", help="Run retrieval without calling OpenAI")
    args = parser.parse_args(argv)
    if args.concurrency < 1 or (args.queries is not None and args.queries < 1):
        parser.error("--concurrency and --queries must be positive")
    if args.live and args.data_dir:
        parser.error("--live refreshes its own data directory")
    if args.check_order and not args.structured:
        parser.error("--check-order requires --structured")
    try:
        run_pipeline(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Pipeline failed: {error}\n")
