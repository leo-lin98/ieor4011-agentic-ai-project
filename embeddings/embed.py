"""Find event candidates across Polymarket US and Kalshi.

The command groups market profiles into events, selects queries, loads a pinned
embedding model, and ranks eligible candidates by vector similarity. It writes
JSON containing run metadata, event queries, and ranked candidates; the optional
top-only mode writes a smaller result without full ranking information.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
import re
import numpy as np
import pandas as pd
from .prepare import load, build_events
from .cache import embed_events
from .search import configs, rank, scope_events

OTHER_PLATFORM = {'POLYUS': 'KALSHI', 'KALSHI': 'POLYUS'}

def validate_output_path(output, profiles, cache):
    out = Path(output).resolve()
    for name, source in (('profiles', profiles), ('cache', cache)):
        path = Path(source).resolve()
        if out == path or (out.exists() and path.exists() and os.path.samefile(out, path)):
            raise ValueError(f'Output must be separate from {name}')

@contextmanager
def atomic_output(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.candidates-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            yield f
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def resolve_revision(model_name, revision):
    """Resolve the exact Hub snapshot before loading weights or looking up vectors."""
    if re.fullmatch(r'[0-9a-f]{40}', revision):
        return revision
    from huggingface_hub import hf_hub_download
    config_path = Path(hf_hub_download(repo_id=model_name, filename='config.json', revision=revision))
    for parent in config_path.parents:
        if parent.parent.name == 'snapshots' and re.fullmatch(r'[0-9a-f]{40}', parent.name):
            return parent.name
    raise ValueError('Could not resolve model revision; pass a pinned 40-character commit with --revision')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    p.add_argument('--output', default='data/embeddings/candidates.json')
    p.add_argument('--cache', default='data/embeddings/events.sqlite3')
    p.add_argument('--query-platform', choices=tuple(OTHER_PLATFORM), default='POLYUS',
                   help='Venue to query from (default: POLYUS)')
    p.add_argument('--scope', choices=('non-sports', 'sports', 'all'), default='non-sports',
                   help='Event categories to include on both venues (default: non-sports)')
    p.add_argument('--queries', type=int, help='Sample this many query events (default: all in scope)')
    p.add_argument('--query-id', action='append', help='Event ID on the query venue; repeat to select specific events')
    p.add_argument('--candidates', type=int, default=None, help='Limit candidate events (default: all)')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--top-only', action='store_true', help='Stream top matches only; full-rank evaluation is unavailable')
    p.add_argument('--representation', choices=('title', 'title_options'), default='title')
    p.add_argument('--model', default='google/embeddinggemma-300m')
    p.add_argument('--revision', default='main')
    p.add_argument('--device', choices=('cpu', 'mps', 'cuda'))
    a = p.parse_args()
    if any(size is not None and size < 1 for size in (a.queries, a.top_k, a.candidates)):
        p.error('sizes must be positive')
    try:
        validate_output_path(a.output, a.profiles, a.cache)
    except ValueError as exc:
        p.error(str(exc))
    start = time.perf_counter()
    def progress(message): print(f'[{time.perf_counter()-start:,.1f}s elapsed] {message}', flush=True)
    # Build event-level queries and candidates from the market profiles.
    progress('Loading profiles and grouping events...')
    profiles = Path(a.profiles)
    digest = hashlib.sha256()
    with profiles.open('rb') as snapshot:
        for chunk in iter(lambda: snapshot.read(1024 * 1024), b''):
            digest.update(chunk)
        snapshot.seek(0)
        df = load(snapshot).astype(object).where(lambda x: pd.notna(x), None)
    events, issues = build_events(df.to_dict('records'))
    progress(f'Profile issues: {issues}')
    categories, sports, mapping_hash = configs()
    scoped_events, category_counts = scope_events(events, categories, a.scope)
    for platform, counts in category_counts.items():
        progress(f'{platform} category counts: {counts}')
    candidate_platform = OTHER_PLATFORM[a.query_platform]
    queries = [e for e in scoped_events if e['platform'] == a.query_platform]
    candidates = [e for e in scoped_events if e['platform'] == candidate_platform]
    if a.query_id:
        wanted = set(a.query_id)
        queries = [e for e in queries if e['event_uid'] in wanted]
        missing = wanted - {e['event_uid'] for e in queries}
        if missing:
            p.error(f'Query IDs unavailable for {a.query_platform} in scope {a.scope}: {sorted(missing)}')
    elif a.queries is not None and len(queries) > a.queries:
        queries = [queries[i] for i in np.linspace(0, len(queries) - 1, a.queries, dtype=int)]
    if a.candidates is not None:
        candidates = candidates[:a.candidates]
    # Pin the model revision, then create or reuse normalized embeddings.
    progress(f'Resolving revision {a.revision} for {a.model}...')
    revision = resolve_revision(a.model, a.revision)
    progress(f'{len(queries):,} {a.query_platform} queries and {len(candidates):,} {candidate_platform} candidates.')
    if queries and candidates:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(a.model, revision=revision, device=a.device)
        progress(f'Model device: {model.device}; revision: {revision}')
        q = embed_events(queries, model, a.model, revision, a.representation, 'query', a.cache, progress)
        c = embed_events(candidates, model, a.model, revision, a.representation, 'document', a.cache, progress)
    # Record the retrieval configuration alongside the ranked results.
    out = Path(a.output)
    candidate_ids = [event['event_uid'] for event in candidates]
    metadata = {
        'schema_version': 2, 'query_platform': a.query_platform, 'candidate_platform': candidate_platform,
        'scope': a.scope, 'top_k': a.top_k, 'input_snapshot': {'path': str(profiles), 'sha256': digest.hexdigest()},
        'model': a.model, 'revision': a.revision, 'resolved_revision': revision, 'representation': a.representation,
        'mapping_sha256': mapping_hash, 'candidate_ids': candidate_ids, 'profile_issues': issues,
        'category_counts': category_counts, 'top_only': a.top_only,
    }
    if a.top_only:
        metadata['candidate_events'] = candidates
    def serialize(value):
        return json.dumps(value, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x))
    # Stream each result to avoid retaining the entire catalog's review records in memory.
    with atomic_output(out) as f:
        f.write('{"metadata":' + serialize(metadata) + ',"results":[')
        for i, query in enumerate(queries):
            if i % 100 == 0: progress(f'Ranking {i+1:,}/{len(queries):,}: {query["event_uid"]}')
            matches = rank(query, candidates, q[i], c, a.top_k if a.top_only else len(candidates),
                           categories, sports, filter_categories=a.scope != 'non-sports') if candidates else []
            result = {'query': query}
            if a.top_only:
                result['candidates'] = [{'event_uid': candidate_ids[j], 'score': score} for j, score in matches]
            else:
                result['candidates'] = [{'event': candidates[j], 'score': score} for j, score in matches[:a.top_k]]
                ranked_ids = [candidate_ids[j] for j, _ in matches]
                result['ranked_candidate_ids'] = ranked_ids
                excluded_ids = []
                if len(ranked_ids) != len(candidate_ids):
                    ranked_set = set(ranked_ids)
                    excluded_ids = [uid for uid in candidate_ids if uid not in ranked_set]
                result['excluded_candidate_ids'] = excluded_ids
            if i: f.write(',')
            f.write(serialize(result))
        f.write(']}')
    progress(f'Wrote {len(queries):,} event queries to {out}.')
if __name__ == '__main__': main()
