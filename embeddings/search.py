"""Filter and rank event candidates using category-aware vector similarity.

Configuration files map categories and sports between platforms. Known conflicts
are excluded, while eligible normalized vectors are ranked by their dot product.
The output is an ordered list of candidate indices and similarity scores.
"""
import hashlib
import json
from pathlib import Path
import numpy as np

CONFIG = Path(__file__).parent / "config"

def norm(value):
    return value.strip().casefold() if isinstance(value, str) else ""

def configs():
    # Validate and normalize the platform mappings; hash them for reproducibility.
    raw = [(CONFIG / f).read_bytes() for f in ("categories.json", "sports.json")]
    categories, sports = (json.loads(x) for x in raw)
    if not isinstance(categories, dict) or set(categories) != {"kalshi", "polyus"}:
        raise ValueError("Invalid category groups")
    category_groups = {}
    for platform, groups in categories.items():
        if not isinstance(groups, dict) or set(groups) != {"sports", "non-sports"}:
            raise ValueError("Invalid category groups")
        lookup = {}
        for group, values in groups.items():
            if not isinstance(values, list) or not values or not all(isinstance(x, str) and norm(x) for x in values):
                raise ValueError("Invalid category groups")
            for value in values:
                category = norm(value)
                if category in lookup:
                    raise ValueError("Category appears in more than one group")
                lookup[category] = group
        category_groups[platform] = lookup
    if not isinstance(sports, dict) or set(sports) != {"kalshi_tags", "polyus_tags", "polyus_subcategories"} or not all(isinstance(m, dict) and all(isinstance(k, str) and norm(k) and isinstance(v, str) and norm(v) for k, v in m.items()) for m in sports.values()):
        raise ValueError("Invalid sports mapping")
    return (category_groups,
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
    # Exclude known category or sport conflicts while retaining uncertain cases.
    query_group = categories["kalshi"].get(norm(query.get("category")))
    candidate_group = categories["polyus"].get(norm(candidate.get("category")))
    if query_group and candidate_group and query_group != candidate_group:
        return False
    if query_group == "sports" and candidate_group == "sports":
        a, b = sport(query, sports, "KALSHI"), sport(candidate, sports, "POLYUS")
        if a and b and a != b: return False
    return True

def rank(query, candidates, query_vector, candidate_vectors, k, categories, sports):
    # Dot products give cosine similarity because the embeddings are normalized.
    mask = np.fromiter((eligible(query, c, categories, sports) for c in candidates), dtype=bool, count=len(candidates))
    indices = np.flatnonzero(mask)
    scores = candidate_vectors[indices] @ query_vector
    order = np.argsort(-scores, kind="stable")[:k]
    return [(int(indices[i]), float(scores[i])) for i in order]
