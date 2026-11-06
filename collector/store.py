"""Parquet storage (DECISIONS.md D9.7-8).

Layout (no hive '=' dirs, so `platform` stays a plain column):
  data/<table>/<PLATFORM>/<YYYY-MM-DD>/<table>_<tick_ts>.parquet   tick tables
  data/market_catalog/current/<PLATFORM>.parquet                  latest version of every market ever seen
  data/market_catalog/history/<PLATFORM>/<date>/catalog_<ts>.parquet  new/changed rows per refresh
  data/match_flags/match_flags.parquet
  data/raw/<PLATFORM>/<date>/catalog_<ts>.jsonl.gz                daily raw API archive
"""
import gzip
import json
import logging
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .config import DATA
from .schemas import MARKET_CATALOG
from .util import sha, utc_date

log = logging.getLogger("collector.store")


def tick_path(table, platform, ts_ms, prefix=None):
    return DATA / table / platform / utc_date(ts_ms) / f"{prefix or table}_{ts_ms}.parquet"


def conform(tbl: pa.Table, schema: pa.Schema) -> pa.Table:
    """Select/cast to schema, adding null columns that an older file lacks."""
    cols = []
    for f in schema:
        if f.name in tbl.column_names:
            cols.append(tbl.column(f.name).cast(f.type))
        else:
            cols.append(pa.nulls(tbl.num_rows, f.type))
    return pa.Table.from_arrays(cols, schema=schema)


class ParquetSink:
    """One parquet file written in batches; atomically renamed into place on close."""

    def __init__(self, path: Path, schema: pa.Schema):
        self.path = Path(path)
        # Leading dot: dataset readers (pyarrow/pandas/duckdb) skip hidden in-progress files.
        self.tmp = self.path.with_name("." + self.path.name + ".tmp")
        self.schema = schema
        self.writer = None
        self.rows = 0
        self.buf = []  # batch rows into large row groups (small row groups bloat the file)

    FLUSH_ROWS = 50_000

    def write(self, rows):
        if rows:
            self.buf.extend(rows)
            if len(self.buf) >= self.FLUSH_ROWS:
                self._flush()

    def _flush(self):
        if self.buf:
            tbl = pa.Table.from_pylist(self.buf, schema=self.schema)
            self.buf = []
            self.write_table(tbl)

    def write_table(self, tbl):
        if tbl.num_rows == 0:
            return
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(self.tmp, self.schema, compression="zstd")
        self.writer.write_table(tbl)
        self.rows += tbl.num_rows

    def close(self):
        self._flush()
        if self.writer is not None:
            self.writer.close()
            os.replace(self.tmp, self.path)
            self.writer = None
        return self.rows

    def abort(self):
        self.buf = []
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        if self.tmp.exists():
            self.tmp.unlink()


def write_rows(table, schema, platform, ts_ms, rows, prefix=None):
    if not rows:
        return 0
    sink = ParquetSink(tick_path(table, platform, ts_ms, prefix), schema)
    sink.write(rows)
    return sink.close()


# ---------------------------------------------------------------- catalog

CATALOG_EXCLUDE_FROM_HASH = {"content_hash", "first_seen_ms", "last_seen_ms", "catalog_pulled_ms",
                             "updated_time_ms"}


def catalog_current_path(platform):
    return DATA / "market_catalog" / "current" / f"{platform}.parquet"


def read_catalog(platform, columns=None):
    p = catalog_current_path(platform)
    if not p.exists():
        return None
    return pq.read_table(p, columns=columns)


class CatalogWriter:
    """Rewrites current/<PLATFORM>.parquet; appends new/changed rows to history.

    Markets no longer returned by the open sweep are carried over unchanged so the
    catalog keeps every market ever seen (closed markets drop out of the APIs' open lists).
    """

    def __init__(self, platform, pulled_ms):
        self.platform = platform
        self.pulled_ms = pulled_ms
        self.prev = {}
        prev = read_catalog(platform, ["market_uid", "content_hash", "first_seen_ms"])
        if prev is not None:
            for u, h, f in zip(prev.column(0).to_pylist(), prev.column(1).to_pylist(), prev.column(2).to_pylist()):
                self.prev[u] = (h, f)
        self.current = ParquetSink(catalog_current_path(platform), MARKET_CATALOG)
        self.history = ParquetSink(
            DATA / "market_catalog" / "history" / platform / utc_date(pulled_ms) / f"catalog_{pulled_ms}.parquet",
            MARKET_CATALOG)
        self.seen = set()
        self.rules = {}
        self.n_new = self.n_changed = self.n_carried = 0

    def write(self, rows):
        out, changed = [], []
        for r in rows:
            u = r["market_uid"]
            if u in self.seen:
                continue
            self.seen.add(u)
            r["content_hash"] = sha(*[r.get(k) for k in MARKET_CATALOG.names if k not in CATALOG_EXCLUDE_FROM_HASH])
            prev = self.prev.get(u)
            r["first_seen_ms"] = prev[1] if prev else self.pulled_ms
            r["last_seen_ms"] = self.pulled_ms
            r["catalog_pulled_ms"] = self.pulled_ms
            if prev is None:
                self.n_new += 1
                changed.append(r)
            elif prev[0] != r["content_hash"]:
                self.n_changed += 1
                changed.append(r)
            self.rules[u] = r["rules_hash"]
            out.append(r)
        self.current.write(out)
        self.history.write(changed)

    def close(self):
        prev_path = catalog_current_path(self.platform)
        if prev_path.exists() and self.prev:
            seen = pa.array(list(self.seen), pa.string())
            for batch in pq.ParquetFile(prev_path).iter_batches(batch_size=50_000):
                tbl = conform(pa.Table.from_batches([batch]), MARKET_CATALOG)
                keep = tbl.filter(pc.invert(pc.is_in(tbl.column("market_uid"), value_set=seen)))
                self.n_carried += keep.num_rows
                self.current.write_table(keep)
        total = self.current.close()
        self.history.close()
        return {"rows": total, "seen": len(self.seen), "new": self.n_new, "changed": self.n_changed,
                "carried": self.n_carried}

    def abort(self):
        self.current.abort()
        self.history.abort()


# ---------------------------------------------------------------- raw archive

def raw_due(platform, ts_ms) -> bool:
    d = DATA / "raw" / platform / utc_date(ts_ms)
    return not (d.exists() and any(d.glob("catalog_*.jsonl.gz")))


class RawArchive:
    def __init__(self, platform, ts_ms):
        self.path = DATA / "raw" / platform / utc_date(ts_ms) / f"catalog_{ts_ms}.jsonl.gz"
        self.tmp = self.path.with_name("." + self.path.name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = gzip.open(self.tmp, "wt", encoding="utf-8")

    def write(self, endpoint, resp, payload):
        self.f.write(json.dumps({"endpoint": endpoint, "sent_ms": resp.sent_ms, "recv_ms": resp.recv_ms,
                                 "data": payload}, separators=(",", ":")) + "\n")

    def close(self):
        self.f.close()
        os.replace(self.tmp, self.path)

    def abort(self):
        self.f.close()
        if self.tmp.exists():
            self.tmp.unlink()
