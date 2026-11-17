"""Retrieve Polymarket events for a varied sample of Kalshi events."""
import argparse
import json
import time
from pathlib import Path
import re
import numpy as np
import pandas as pd
from .prepare import load, build_events
from .cache import embed_events
from .search import configs, rank, eligible

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
    p.add_argument('--queries', type=int, default=10)
    p.add_argument('--query-id', action='append', help='Kalshi event ID; repeat to select specific events')
    p.add_argument('--candidates', type=int, default=None, help='Limit candidate events (default: all)')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--top-only', action='store_true', help='Stream top matches only; full-rank evaluation is unavailable')
    p.add_argument('--representation', choices=('title', 'title_options'), default='title')
    p.add_argument('--model', default='google/embeddinggemma-300m')
    p.add_argument('--revision', default='main')
    p.add_argument('--device', choices=('cpu', 'mps', 'cuda'))
    a = p.parse_args()
    if a.queries < 1 or a.top_k < 1 or (a.candidates is not None and a.candidates < 1): p.error('sizes must be positive')
    start = time.perf_counter()
    def progress(message): print(f'[{time.perf_counter()-start:,.1f}s elapsed] {message}', flush=True)
    progress('Loading profiles and grouping events...')
    df = load(a.profiles).astype(object).where(lambda x: pd.notna(x), None)
    events, issues = build_events(df.to_dict('records'))
    progress(f'Profile issues: {issues}')
    queries = [e for e in events if e['platform'] == 'KALSHI']
    candidates = [e for e in events if e['platform'] == 'POLYUS']
    if a.query_id:
        wanted = set(a.query_id); queries = [e for e in queries if e['event_uid'] in wanted]
        missing = wanted - {e['event_uid'] for e in queries}
        if missing: raise ValueError(f'Unknown query events: {sorted(missing)}')
    elif len(queries) > a.queries:
        queries = [queries[i] for i in np.linspace(0, len(queries)-1, a.queries, dtype=int)]
    if a.candidates: candidates = candidates[:a.candidates]
    if not queries or not candidates: raise ValueError('Both platforms must have events')
    from sentence_transformers import SentenceTransformer
    progress(f'Resolving revision {a.revision} for {a.model}...')
    revision = resolve_revision(a.model, a.revision)
    progress(f'Loading model {a.model}; {len(queries):,} queries and {len(candidates):,} candidates.')
    model = SentenceTransformer(a.model, revision=revision, device=a.device)
    progress(f'Model device: {model.device}; revision: {revision}')
    q = embed_events(queries, model, a.model, revision, a.representation, 'query', a.cache, progress)
    c = embed_events(candidates, model, a.model, revision, a.representation, 'document', a.cache, progress)
    categories, sports, mapping_hash = configs()
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    metadata = {'model': a.model, 'revision': a.revision, 'resolved_revision': revision, 'representation': a.representation,
                'mapping_sha256': mapping_hash, 'candidate_ids': [x['event_uid'] for x in candidates],
                'profile_issues': issues, 'top_only': a.top_only}
    if a.top_only:
        metadata['candidate_events'] = candidates
    def serialize(value):
        return json.dumps(value, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x))
    # Stream each result to avoid retaining the entire catalog's review records in memory.
    with out.open('w', encoding='utf-8') as f:
        f.write('{"metadata":' + serialize(metadata) + ',"results":[')
        for i, query in enumerate(queries):
            if i % 100 == 0: progress(f'Ranking {i+1:,}/{len(queries):,}: {query["event_uid"]}')
            matches = rank(query, candidates, q[i], c, a.top_k if a.top_only else len(candidates), categories, sports)
            result = {'query': query, 'candidates': [({'event_uid': candidates[j]['event_uid'], 'score': score} if a.top_only else {'event': candidates[j], 'score': score}) for j, score in matches[:a.top_k]]}
            if not a.top_only:
                result['ranked_candidate_ids'] = [candidates[j]['event_uid'] for j, _ in matches]
                result['excluded_candidate_ids'] = [e['event_uid'] for e in candidates if not eligible(query, e, categories, sports)]
            if i: f.write(',')
            f.write(serialize(result))
        f.write(']}')
    progress(f'Wrote {len(queries):,} event queries to {out}.')
if __name__ == '__main__': main()
