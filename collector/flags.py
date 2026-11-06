"""match_flags table (T2). Small, so it is read and rewritten whole."""
import os

import pyarrow as pa
import pyarrow.parquet as pq

from .config import DATA
from .schemas import MATCH_FLAGS
from .store import conform, read_catalog
from .util import now_ms

FLAGS_PATH = DATA / "match_flags" / "match_flags.parquet"


def load_flags():
    if not FLAGS_PATH.exists():
        return []
    return conform(pq.read_table(FLAGS_PATH), MATCH_FLAGS).to_pylist()


def save_flags(rows):
    FLAGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = FLAGS_PATH.with_name(FLAGS_PATH.name + ".tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=MATCH_FLAGS), tmp, compression="zstd")
    os.replace(tmp, FLAGS_PATH)


def active_flags():
    return [f for f in load_flags() if f.get("is_active")]


def _rules_hash(platform, market_uid):
    cat = read_catalog(platform, ["market_uid", "rules_hash"])
    if cat is None:
        return None
    for u, h in zip(cat.column(0).to_pylist(), cat.column(1).to_pylist()):
        if u == market_uid:
            return h
    return None


def add_flag(kalshi_uid, polyus_uid, relationship="identical", note=None, source="manual"):
    if not kalshi_uid.startswith("KALSHI:") or not polyus_uid.startswith("POLYUS:"):
        raise ValueError("expected KALSHI:<ticker> and POLYUS:<market id>")
    pair_id = f"{kalshi_uid}__{polyus_uid}"
    rows = [r for r in load_flags() if r["pair_id"] != pair_id]
    ts = now_ms()
    k_hash, p_hash = _rules_hash("KALSHI", kalshi_uid), _rules_hash("POLYUS", polyus_uid)
    rows.append({
        "pair_id": pair_id, "kalshi_market_uid": kalshi_uid, "polyus_market_uid": polyus_uid,
        "market_identical": relationship == "identical", "relationship": relationship,
        "proposer_explanation": None, "judge_verdict": None, "numeric_checks_passed": None,
        "rules_review_status": None, "human_review_status": "manual" if source == "manual" else None,
        "kalshi_rules_hash": k_hash, "polyus_rules_hash": p_hash,
        "flagged_ts": ts, "updated_ts": ts, "is_active": True, "source": source, "note": note,
    })
    save_flags(rows)
    return rows[-1], (k_hash is not None, p_hash is not None)


def set_active(pair_id, active: bool):
    rows = load_flags()
    for r in rows:
        if r["pair_id"] == pair_id:
            r["is_active"] = active
            r["updated_ts"] = now_ms()
    save_flags(rows)


def mark_stale(platform, rules_by_uid: dict) -> int:
    """After a catalog refresh: flag pairs whose rules text changed since they were flagged."""
    rows = load_flags()
    col = "kalshi" if platform == "KALSHI" else "polyus"
    n = 0
    for r in rows:
        u = r[f"{col}_market_uid"]
        new = rules_by_uid.get(u)
        if new is None:
            continue
        old = r[f"{col}_rules_hash"]
        if old is None:
            r[f"{col}_rules_hash"] = new
            n += 1
        elif old != new and r.get("rules_review_status") != "stale_rules_changed":
            r["rules_review_status"] = "stale_rules_changed"
            r["updated_ts"] = now_ms()
            n += 1
    if n:
        save_flags(rows)
    return n
