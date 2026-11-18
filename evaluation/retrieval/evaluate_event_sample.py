"""Compare a retrieval run with event-review judgments exported by the browser."""
import argparse
from collections import Counter
import csv
import json

from embeddings.reporting.results import iter_results


def load_cases(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    required = {'set_id', 'kalshi_uid', 'outcome', 'related_events', 'suggestions'}
    if not rows or not required.issubset(rows[0]):
        raise ValueError('Expected an evaluation-set CSV exported by the browser')
    if len({r['kalshi_uid'] for r in rows}) != len(rows):
        raise ValueError('CSV contains duplicate Kalshi events')
    for row in rows:
        row['related_events'] = json.loads(row['related_events'])
        row['suggestions'] = json.loads(row['suggestions'])
    return rows


def evaluate(rows, results=None, pool=None, include_proposed=False):
    """Calculate event-level top-10 hits against independently recorded labels.

    Missing labels and targets outside the candidate pool are reported separately.
    Possible labels are not counted as likely-exact labels.
    """
    outcomes = Counter(row['outcome'] for row in rows)
    metrics = {judgment: {'events_with_labels': 0, 'events_with_targets_in_pool': 0,
                          'events_with_all_targets_in_pool': 0,
                          'events_with_targets_outside_pool': 0,
                          'events_with_any_target_in_top_10': 0,
                          'events_with_any_target_in_top_5': 0}
               for judgment in ('likely_exact', 'possible')}
    examples = []
    for row in rows:
        uid = row['kalshi_uid']
        if results is None:
            suggestions = row['suggestions']
        else:
            if uid not in results:
                raise ValueError(f'Query absent from retrieval results: {uid}')
            suggestions = results[uid]
        ranked = [x['candidate_uid'] for x in suggestions[:10]]
        example = {'kalshi_uid': uid, 'outcome': row['outcome']}
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
            example[judgment] = {'known_ids': sorted(targets), 'outside_pool': sorted(outside_pool),
                                 'top_10_ids': [x for x in ranked if x in targets]}
        examples.append(example)
    for metric in metrics.values():
        denominator = metric['events_with_targets_in_pool']
        metric['event_hit_rate_at_10'] = metric['events_with_any_target_in_top_10'] / denominator if denominator else None
        metric['event_hit_rate_at_5'] = metric['events_with_any_target_in_top_5'] / denominator if denominator else None
    set_ids = list(dict.fromkeys(row['set_id'] for row in rows))
    return {'set_id': set_ids[0] if len(set_ids) == 1 else None, 'set_ids': set_ids,
            'events': len(rows), 'outcomes': dict(outcomes),
            'proposed_labels_included': include_proposed,
            'case_origins': dict(Counter(row.get('case_origin', 'unspecified') for row in rows)),
            'source': 'exported baseline suggestions' if results is None else 'retrieval run',
            'candidate_pool_verified': pool is not None, 'metrics': metrics, 'examples': examples,
            'note': 'Conditional event-level hit rates use reviewed targets in the candidate pool. Labels are incomplete and originally came from baseline suggestions; review additional candidates before interpreting model comparisons.'}


def load_results(path, wanted):
    iterator = iter_results(path)
    metadata = next(iterator)
    pool = set(metadata['candidate_ids'])
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
    rows = load_cases(args.reviews)
    results, pool, metadata = load_results(args.results, {r['kalshi_uid'] for r in rows}) if args.results else (None, None, None)
    report = evaluate(rows, results, pool, args.include_proposed)
    if args.include_proposed and pool is None:
        parser.error('--include-proposed requires --results so candidate-pool exclusions are not mistaken for ranking failures')
    if metadata:
        report['run'] = {k: metadata.get(k) for k in ('model', 'resolved_revision', 'representation', 'mapping_sha256', 'top_only')}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
