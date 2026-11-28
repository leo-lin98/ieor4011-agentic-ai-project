"""Compare a retrieval run with event-review judgments exported by the browser."""
import argparse
from collections import Counter
import csv
import json

from embeddings.reporting.results import iter_results, retrieval_direction


def query_uid(row):
    return row.get('query_uid') or row.get('kalshi_uid')


def validate_cases(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected at least one evaluation row')
    for row in rows:
        if not isinstance(row, dict) or not all(
            isinstance(value, str) and value for value in (query_uid(row), row.get('set_id'), row.get('outcome'))
        ):
            raise ValueError('Invalid evaluation row')
        for key, id_key in (('related_events', 'event_uid'), ('suggestions', 'candidate_uid')):
            if not isinstance(row.get(key), list) or any(
                not isinstance(item, dict) or not isinstance(item.get(id_key), str) for item in row[key]
            ):
                raise ValueError(f'Invalid {key} for {query_uid(row)}')
        if any(not isinstance(item.get('judgment', ''), str) for item in row['related_events']):
            raise ValueError(f'Invalid judgment for {query_uid(row)}')
    if len({query_uid(row) for row in rows}) != len(rows):
        raise ValueError('Evaluation contains duplicate query events')
    directions = {retrieval_direction(row) for row in rows}
    if len(directions) != 1:
        raise ValueError('Evaluation rows must use one retrieval direction')
    return directions.pop()


def load_cases(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    required = {'set_id', 'outcome', 'related_events', 'suggestions'}
    if not rows or not required.issubset(rows[0]):
        raise ValueError('Expected an evaluation-set CSV exported by the browser')
    for row in rows:
        row['related_events'] = json.loads(row['related_events'])
        row['suggestions'] = json.loads(row['suggestions'])
    validate_cases(rows)
    return rows


def evaluate(rows, results=None, pool=None, include_proposed=False, *, allow_missing_queries=False):
    """Calculate event-level top-10 hits against independently recorded labels.

    Missing labels and targets outside the candidate pool are reported separately.
    Possible labels are not counted as likely-exact labels.
    """
    outcomes = Counter(row['outcome'] for row in rows)
    metrics = {judgment: {'events_with_labels': 0, 'events_with_targets_in_pool': 0,
                          'events_with_all_targets_in_pool': 0,
                          'events_with_targets_outside_pool': 0,
                          'events_with_any_target_in_top_10': 0,
                          'events_with_any_target_in_top_5': 0,
                          'events_with_any_target_in_top_3': 0}
               for judgment in ('likely_exact', 'possible')}
    examples = []
    missing_queries = []
    for row in rows:
        uid = query_uid(row)
        if results is None:
            suggestions = row['suggestions']
        else:
            if uid not in results:
                if not allow_missing_queries:
                    raise ValueError(f'Query absent from retrieval results: {uid}')
                if row['outcome'] != 'unreviewed':
                    missing_queries.append(uid)
                continue
            suggestions = results[uid]
        if row['outcome'] == 'unreviewed':
            continue
        ranked = [x['candidate_uid'] for x in suggestions[:10]]
        example = {'query_uid': uid, 'outcome': row['outcome']}
        for judgment, metric in metrics.items():
            accepted = {judgment}
            if include_proposed and judgment == 'likely_exact':
                accepted.add('proposed_likely_exact')
            targets = {x['event_uid'] for x in row['related_events'] if x.get('judgment') in accepted and x.get('event_uid')}
            if not targets:
                continue
            metric['events_with_labels'] += 1
            in_pool = targets if pool is None else targets & pool
            outside_pool = targets - in_pool
            if outside_pool:
                metric['events_with_targets_outside_pool'] += 1
            if in_pool:
                metric['events_with_targets_in_pool'] += 1
                if not outside_pool:
                    metric['events_with_all_targets_in_pool'] += 1
                if any(target in ranked for target in in_pool):
                    metric['events_with_any_target_in_top_10'] += 1
                if any(target in ranked[:5] for target in in_pool):
                    metric['events_with_any_target_in_top_5'] += 1
                if any(target in ranked[:3] for target in in_pool):
                    metric['events_with_any_target_in_top_3'] += 1
            example[judgment] = {'known_ids': sorted(targets), 'outside_pool': sorted(outside_pool),
                                 'top_10_ids': [x for x in ranked if x in targets]}
        examples.append(example)
    for metric in metrics.values():
        denominator = metric['events_with_targets_in_pool']
        metric['event_hit_rate_at_10'] = metric['events_with_any_target_in_top_10'] / denominator if denominator else None
        metric['event_hit_rate_at_5'] = metric['events_with_any_target_in_top_5'] / denominator if denominator else None
        metric['event_hit_rate_at_3'] = metric['events_with_any_target_in_top_3'] / denominator if denominator else None
    set_ids = list(dict.fromkeys(row['set_id'] for row in rows))
    return {'set_id': set_ids[0] if len(set_ids) == 1 else None, 'set_ids': set_ids,
            'events': len(rows), 'outcomes': dict(outcomes),
            'reviewed_events': len(rows) - outcomes['unreviewed'], 'missing_query_ids': missing_queries,
            'proposed_labels_included': include_proposed,
            'case_origins': dict(Counter(row.get('case_origin', 'unspecified') for row in rows)),
            'source': 'exported baseline suggestions' if results is None else 'retrieval run',
            'candidate_pool_verified': pool is not None, 'metrics': metrics, 'examples': examples,
            'note': 'Hit rates use reviewed queries present in the run with a labeled target in the candidate pool.'}


def load_results(path, wanted):
    iterator = iter_results(path)
    metadata = next(iterator)
    pool = set(metadata['candidate_ids']) if 'candidate_ids' in metadata else None
    found = {}
    for row in iterator:
        uid = row['query']['event_uid']
        if uid not in wanted:
            continue
        found[uid] = [{'candidate_uid': match.get('event_uid') or match['event']['event_uid']} for match in row['candidates']]
    return found, pool, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reviews', help='Evaluation-set CSV exported from the browser')
    parser.add_argument('--results', help='Retrieval JSON for another run; omit to summarize the saved baseline')
    parser.add_argument('--include-proposed', action='store_true', help='Include proposed likely-exact pairs that have not yet been reviewed by the user')
    args = parser.parse_args()
    try:
        rows = load_cases(args.reviews)
        direction = retrieval_direction(rows[0])
        results, pool, metadata = load_results(args.results, {query_uid(r) for r in rows}) if args.results else (None, None, None)
        if metadata is not None and retrieval_direction(metadata) != direction:
            raise ValueError('The evaluation and retrieval run use different directions')
        if args.include_proposed and pool is None:
            raise ValueError('--include-proposed requires a retrieval run with candidate-pool IDs')
        report = evaluate(rows, results, pool, args.include_proposed, allow_missing_queries=True)
    except (ValueError, TypeError, OSError, StopIteration) as exc:
        parser.error(str(exc))
    report['query_platform'], report['candidate_platform'] = direction
    if metadata:
        report['run'] = {k: metadata.get(k) for k in ('model', 'resolved_revision', 'representation', 'mapping_sha256', 'top_only', 'scope', 'top_k')}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
