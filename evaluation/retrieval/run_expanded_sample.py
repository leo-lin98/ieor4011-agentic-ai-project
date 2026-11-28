"""Compare filtered and unfiltered rankings for a reviewed event sample."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from embeddings.cache import embed_events
from embeddings.prepare import build_events, load
from embeddings.reporting.results import retrieval_direction
from embeddings.search import configs, eligible, scope_events
from .evaluate_event_sample import load_cases, query_uid


def run(reviews, profiles, cache, output, device, scope='all'):
    start = time.perf_counter()
    def progress(message):
        print(f'[{time.perf_counter()-start:.1f}s] {message}', flush=True)
    cases = [row for row in load_cases(reviews) if row['outcome'] != 'unreviewed']
    if not cases:
        raise ValueError('Expected at least one reviewed query')
    query_platform, candidate_platform = retrieval_direction(cases[0])
    frame = load(profiles).astype(object).where(lambda d: pd.notna(d), None)
    events, issues = build_events(frame.to_dict('records'))
    categories, sports, mapping_hash = configs()
    events, _ = scope_events(events, categories, scope)
    wanted = {query_uid(r) for r in cases}
    queries = {e['event_uid']: e for e in events if e['platform'] == query_platform and e['event_uid'] in wanted}
    missing = wanted - queries.keys()
    if missing:
        raise ValueError(f'Query events unavailable in scope {scope}: {sorted(missing)}')
    candidates = [e for e in events if e['platform'] == candidate_platform]
    if not candidates:
        raise ValueError(f'No {candidate_platform} candidates in scope {scope}')
    model_name = 'google/embeddinggemma-300m'
    revision = '57c266a740f537b4dc058e1b0cda161fd15afa75'
    model = SentenceTransformer(model_name, revision=revision, device=device, local_files_only=True)
    progress(f'Embedding {len(queries)} queries and {len(candidates)} candidates on {model.device}')
    qevents = [queries[query_uid(r)] for r in cases]
    vectors_q = embed_events(qevents, model, model_name, revision, 'title', 'query', cache, progress)
    vectors_c = embed_events(candidates, model, model_name, revision, 'title', 'document', cache, progress)
    index_by_uid = {c['event_uid']: i for i, c in enumerate(candidates)}
    rows = []
    for row, query, vector in zip(cases, qevents, vectors_q):
        scores = vectors_c @ vector
        order_all = np.argsort(-scores, kind='stable')
        mask = np.fromiter((eligible(query, candidate, categories, sports) for candidate in candidates), dtype=bool, count=len(candidates))
        order_filtered = np.flatnonzero(mask)
        order_filtered = order_filtered[np.argsort(-scores[order_filtered], kind='stable')]
        ranks_all = {int(idx): rank for rank, idx in enumerate(order_all, 1)}
        ranks_filtered = {int(idx): rank for rank, idx in enumerate(order_filtered, 1)}
        def top(order):
            return [{'event_uid': candidates[int(idx)]['event_uid'], 'title': candidates[int(idx)]['title'], 'score': float(scores[idx])} for idx in order[:10]]
        targets = []
        for label in row['related_events']:
            idx = index_by_uid.get(label['event_uid'])
            targets.append({'event_uid': label['event_uid'], 'title': label.get('title'), 'judgment': label.get('judgment'),
                            'score': float(scores[idx]) if idx is not None else None,
                            'rank_all': ranks_all.get(idx), 'rank_filtered': ranks_filtered.get(idx),
                            'in_pool': idx is not None})
        rows.append({'event_uid': query['event_uid'], 'title': query['title'], 'origin': row.get('case_origin'),
                     'review_status': row.get('review_status'), 'outcome': row['outcome'],
                     'targets': targets, 'top_10_all': top(order_all), 'top_10_filtered': top(order_filtered)})
    counts = {}
    for judgment in ('likely_exact', 'proposed_likely_exact', 'possible'):
        with_targets = [r for r in rows if any(t['judgment'] == judgment for t in r['targets'])]
        counts[judgment] = {'events_with_labels': len(with_targets),
                            'events_in_pool': sum(any(t['judgment'] == judgment and t['in_pool'] for t in r['targets']) for r in with_targets),
                            'top_10_all': sum(any(t['judgment'] == judgment and t['rank_all'] is not None and t['rank_all'] <= 10 for t in r['targets']) for r in with_targets),
                            'top_10_filtered': sum(any(t['judgment'] == judgment and t['rank_filtered'] is not None and t['rank_filtered'] <= 10 for t in r['targets']) for r in with_targets)}
    result = {'model': model_name, 'revision': revision, 'representation': 'title', 'profile_path': str(profiles),
              'query_platform': query_platform, 'candidate_platform': candidate_platform, 'scope': scope,
              'profile_sha256': hashlib.sha256(Path(profiles).read_bytes()).hexdigest(), 'mapping_sha256': mapping_hash,
              'query_count': len(queries), 'candidate_count': len(candidates), 'profile_issues': issues,
              'metrics': counts, 'events': rows,
              'note': 'Both rankings use the chosen event scope. Filtered rankings also apply category and sport compatibility to each pair.'}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    progress(f'Wrote {output}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews', required=True, help='Evaluation CSV exported from the browser')
    parser.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    parser.add_argument('--cache', default='data/embeddings/events.sqlite3')
    parser.add_argument('--output', default='data/embeddings/expanded-sample-results.json')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--scope', choices=('non-sports', 'sports', 'all'), default='all')
    args = parser.parse_args()
    run(args.reviews, args.profiles, args.cache, args.output, args.device, args.scope)


if __name__ == '__main__':
    main()
