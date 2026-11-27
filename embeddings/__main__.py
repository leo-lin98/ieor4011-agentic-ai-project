"""Command line for frozen retrieval runs, local review, and recall evaluation."""

import argparse
import json
import logging
import sqlite3
from pathlib import Path

from collector.config import DATA
from evaluation.retrieval.evaluate import evaluate
from evaluation.retrieval.reviews import DEFAULT_DB

from .run import DEFAULT_MODEL, MODEL_REVISIONS, Run, create_run, resume_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=DATA / "embeddings" / "runs")
    parser.add_argument("--reviews-db", type=Path, default=DEFAULT_DB)
    commands = parser.add_subparsers(dest="command", required=True)
    retrieve = commands.add_parser("run", help="Freeze catalogs and retrieve candidates")
    run_mode = retrieve.add_mutually_exclusive_group()
    run_mode.add_argument("--run-id")
    run_mode.add_argument("--resume", metavar="RUN_ID", help="Continue a checkpointed run with its original settings")
    source = retrieve.add_mutually_exclusive_group()
    source.add_argument("--catalog-dir", type=Path)
    source.add_argument("--snapshot-run", help="Reuse the catalog from a completed run")
    retrieve.add_argument("--device", help="Sentence Transformers device, such as cpu or mps")
    retrieve.add_argument("--model", choices=MODEL_REVISIONS, help=f"Embedding model (default: {DEFAULT_MODEL})")
    retrieve.add_argument("--batch-size", type=int, help="Model batch size (default: 16)")
    retrieve.add_argument("--truncation", choices=("head", "head-tail"), help="Truncation strategy (default: head)")
    review = commands.add_parser("review", help="Start a localhost review server")
    review.add_argument("--run-id", required=True)
    review.add_argument("--port", type=int, default=8765)
    score = commands.add_parser("evaluate", help="Evaluate a frozen SQLite benchmark")
    score.add_argument("--run-id", required=True)
    score.add_argument("--benchmark", required=True)
    score.add_argument("--compare-run")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "run":
            if args.resume is not None:
                if any(value is not None for value in (
                    args.catalog_dir, args.snapshot_run, args.device, args.batch_size, args.truncation, args.model,
                )):
                    raise ValueError("Resume uses the saved catalog and settings. Supply only --resume RUN_ID.")
                print(resume_run(args.runs_dir, args.resume))
                return 0
            batch_size = args.batch_size if args.batch_size is not None else 16
            if batch_size < 1:
                raise ValueError("Batch size must be positive")
            if args.snapshot_run:
                source_run = Run(args.runs_dir, args.snapshot_run)
                catalog_dir = source_run.catalog.directory
                del source_run
            else:
                catalog_dir = args.catalog_dir or DATA / "market_catalog" / "current"
            print(create_run(args.runs_dir, catalog_dir, args.run_id, args.device,
                             batch_size, args.truncation or "head", args.model or DEFAULT_MODEL))
        elif args.command == "review":
            from evaluation.retrieval.browser import server_for

            run = Run(args.runs_dir, args.run_id)
            with server_for(run, args.reviews_db, args.port) as server:
                print(f"http://127.0.0.1:{server.server_port}/", flush=True)
                server.serve_forever()
        else:
            run = Run(args.runs_dir, args.run_id)
            comparison = Run(args.runs_dir, args.compare_run) if args.compare_run else None
            print(json.dumps(evaluate(run, args.reviews_db, args.benchmark, comparison), indent=2))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
