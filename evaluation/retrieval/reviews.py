"""SQLite review decisions and frozen benchmarks."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime

from embeddings.catalog import rule_fingerprint

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    kalshi_market_uid TEXT NOT NULL,
    polyus_market_uid TEXT NOT NULL,
    label TEXT NOT NULL CHECK (label IN ('match', 'no_match')),
    reviewed_at TEXT NOT NULL,
    kalshi_fingerprint TEXT NOT NULL,
    polyus_fingerprint TEXT NOT NULL,
    PRIMARY KEY (kalshi_market_uid, polyus_market_uid)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS benchmarks (
    name TEXT PRIMARY KEY,
    frozen_at TEXT
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS benchmark_pairs (
    benchmark_name TEXT NOT NULL REFERENCES benchmarks (name),
    kalshi_market_uid TEXT NOT NULL,
    polyus_market_uid TEXT NOT NULL,
    kalshi_fingerprint TEXT NOT NULL,
    polyus_fingerprint TEXT NOT NULL,
    independent INTEGER NOT NULL CHECK (independent IN (0, 1)),
    PRIMARY KEY (benchmark_name, kalshi_market_uid, polyus_market_uid)
) WITHOUT ROWID;
"""


def now():
    return datetime.now(UTC).isoformat()


def pair_fingerprints(kalshi, polyus):
    return rule_fingerprint(kalshi), rule_fingerprint(polyus)


def review_state(review, kalshi, polyus):
    if review is None:
        return "unreviewed"
    if pair_fingerprints(kalshi, polyus) != (review["kalshi_fingerprint"], review["polyus_fingerprint"]):
        return "stale"
    return review["label"]


def init_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA)


@contextmanager
def connection(path, write=False):
    with closing(sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        with db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db


def decisions(path, kalshi_uid):
    with connection(path) as db:
        return {row["polyus_market_uid"]: row for row in db.execute(
            "SELECT * FROM reviews WHERE kalshi_market_uid = ?", (kalshi_uid,),
        )}


def save_decision(path, kalshi, polyus, label):
    if label not in ("match", "no_match"):
        raise ValueError("Choose match or no match.")
    values = (kalshi["market_uid"], polyus["market_uid"], label, now(), *pair_fingerprints(kalshi, polyus))
    with connection(path, write=True) as db:
        db.execute("""
            INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (kalshi_market_uid, polyus_market_uid) DO UPDATE SET
                label = excluded.label,
                reviewed_at = excluded.reviewed_at,
                kalshi_fingerprint = excluded.kalshi_fingerprint,
                polyus_fingerprint = excluded.polyus_fingerprint
        """, values)


def benchmarks(path):
    with connection(path) as db:
        return db.execute("""
            SELECT name, frozen_at, count(kalshi_market_uid) AS pair_count
            FROM benchmarks LEFT JOIN benchmark_pairs ON name = benchmark_name
            GROUP BY name ORDER BY name
        """).fetchall()


def create_benchmark(path, name):
    name = name.strip()
    if not name or len(name) > 100:
        raise ValueError("Enter a benchmark name between 1 and 100 characters.")
    with connection(path, write=True) as db:
        if db.execute("SELECT 1 FROM benchmarks WHERE name = ?", (name,)).fetchone():
            raise ValueError("A benchmark with this name already exists.")
        db.execute("INSERT INTO benchmarks (name) VALUES (?)", (name,))


def require_draft(db, name):
    row = db.execute("SELECT frozen_at FROM benchmarks WHERE name = ?", (name,)).fetchone()
    if row is None or row["frozen_at"] is not None:
        raise ValueError("Choose an editable benchmark.")


def add_benchmark_pair(path, name, kalshi, polyus, independent):
    ids = (kalshi["market_uid"], polyus["market_uid"])
    with connection(path, write=True) as db:
        require_draft(db, name)
        review = db.execute("""
            SELECT * FROM reviews WHERE kalshi_market_uid = ? AND polyus_market_uid = ?
        """, ids).fetchone()
        if review_state(review, kalshi, polyus) != "match":
            raise ValueError("Save a match for the displayed rules before adding it to a benchmark.")
        db.execute("""
            INSERT INTO benchmark_pairs VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (benchmark_name, kalshi_market_uid, polyus_market_uid) DO UPDATE SET
                kalshi_fingerprint = excluded.kalshi_fingerprint,
                polyus_fingerprint = excluded.polyus_fingerprint,
                independent = excluded.independent
        """, (name, *ids, *pair_fingerprints(kalshi, polyus), int(independent)))


def remove_benchmark_pair(path, name, kalshi_uid, polyus_uid):
    with connection(path, write=True) as db:
        require_draft(db, name)
        db.execute("""
            DELETE FROM benchmark_pairs
            WHERE benchmark_name = ? AND kalshi_market_uid = ? AND polyus_market_uid = ?
        """, (name, kalshi_uid, polyus_uid))


def freeze_benchmark(path, name):
    with connection(path, write=True) as db:
        require_draft(db, name)
        if not db.execute("SELECT 1 FROM benchmark_pairs WHERE benchmark_name = ?", (name,)).fetchone():
            raise ValueError("Add at least one match before freezing the benchmark.")
        db.execute("UPDATE benchmarks SET frozen_at = ? WHERE name = ?", (now(), name))


def benchmark_pairs(db, name):
    return db.execute("""
        SELECT kalshi_market_uid, polyus_market_uid, kalshi_fingerprint, polyus_fingerprint, independent
        FROM benchmark_pairs WHERE benchmark_name = ?
        ORDER BY kalshi_market_uid, polyus_market_uid
    """, (name,)).fetchall()


def read_benchmark(path, name):
    with connection(path) as db:
        row = db.execute("SELECT frozen_at FROM benchmarks WHERE name = ?", (name,)).fetchone()
        if row is None or row["frozen_at"] is None:
            raise ValueError(f"Benchmark {name!r} must exist and be frozen")
        pairs = [dict(pair) for pair in benchmark_pairs(db, name)]
    payload = json.dumps(pairs, ensure_ascii=False, sort_keys=True).encode()
    return {"name": name, "frozen_at": row["frozen_at"], "sha256": hashlib.sha256(payload).hexdigest()}, pairs
