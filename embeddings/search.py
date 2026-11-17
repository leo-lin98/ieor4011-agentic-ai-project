"""Category-aware exact cosine retrieval over normalized vectors."""
import hashlib
import json
from pathlib import Path
import numpy as np

CONFIG = Path(__file__).parent / "config"

def norm(value):
    return value.strip().casefold() if isinstance(value, str) else ""

def configs():
    raw = [(CONFIG / f).read_bytes() for f in ("categories.json", "sports.json")]
    categories, sports = (json.loads(x) for x in raw)
    if not isinstance(categories, dict) or not all(isinstance(k, str) and isinstance(v, list) and v and all(isinstance(x, str) and norm(x) for x in v) for k, v in categories.items()):
        raise ValueError("Invalid category mapping")
    if not isinstance(sports, dict) or set(sports) != {"kalshi_tags", "polyus_tags", "polyus_subcategories"} or not all(isinstance(m, dict) and all(isinstance(k, str) and norm(k) and isinstance(v, str) and norm(v) for k, v in m.items()) for m in sports.values()):
        raise ValueError("Invalid sports mapping")
    return ({norm(k): [norm(x) for x in v] for k, v in categories.items()},
            {s: {norm(k): norm(v) for k, v in m.items()} for s, m in sports.items()},
            hashlib.sha256(b"\0".join(raw)).hexdigest())

def tags(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    return list(value) if isinstance(value, (list, tuple, np.ndarray)) else []

def sport(row, mappings, platform):
    found = set()
    for tag in tags(row.get("tags")):
        match = mappings["kalshi_tags" if platform == "KALSHI" else "polyus_tags"].get(norm(tag))
        if match: found.add(match)
    if platform == "POLYUS":
        match = mappings["polyus_subcategories"].get(norm(row.get("subcategory")))
        if match: found.add(match)
    return next(iter(found)) if len(found) == 1 else None

def eligible(query, candidate, categories, sports):
    allowed = categories.get(norm(query.get("category")))
    category = norm(candidate.get("category"))
    known = {c for values in categories.values() for c in values}
    if allowed and category in known and category not in allowed:
        return False
    if norm(query.get("category")) == "sports" and category == "sports":
        a, b = sport(query, sports, "KALSHI"), sport(candidate, sports, "POLYUS")
        if a and b and a != b: return False
    return True

def rank(query, candidates, query_vector, candidate_vectors, k, categories, sports):
    mask = np.fromiter((eligible(query, c, categories, sports) for c in candidates), dtype=bool, count=len(candidates))
    indices = np.flatnonzero(mask)
    scores = candidate_vectors[indices] @ query_vector
    order = np.argsort(-scores, kind="stable")[:k]
    return [(int(indices[i]), float(scores[i])) for i in order]
