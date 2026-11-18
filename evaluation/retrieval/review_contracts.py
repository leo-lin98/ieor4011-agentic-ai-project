"""Summarize contract reviews and displayed event-candidate coverage."""
import argparse
from collections import Counter
import csv
import json


def summarize(rows):
    counts = Counter()
    for row in rows:
        outcome = row.get('outcome', '').strip()
        if outcome not in {'matching', 'match_not_in_dataset', 'no_polymarket_match', 'not_found', 'unsure'}:
            raise ValueError(f'Unknown review outcome: {outcome!r}')
        counts[outcome] += 1
        if outcome == 'matching':
            if str(row.get('kalshi_in_retrieval_run')) != '1':
                counts['matching_source_not_in_run'] += 1
            elif str(row.get('polyus_in_retrieval_pool')) != '1':
                counts['matching_target_not_in_pool'] += 1
            else:
                counts['eligible_matching_contracts'] += 1
                if str(row.get('candidate_rank', '')).strip():
                    counts['matching_event_displayed'] += 1
    eligible = counts['eligible_matching_contracts']
    return {'reviewed_contracts': sum(counts[key] for key in ('matching', 'match_not_in_dataset', 'no_polymarket_match', 'not_found', 'unsure')),
            'outcomes': {key: counts[key] for key in ('matching', 'match_not_in_dataset', 'no_polymarket_match', 'not_found', 'unsure')},
            'matching_source_not_in_run': counts['matching_source_not_in_run'],
            'matching_target_not_in_pool': counts['matching_target_not_in_pool'],
            'eligible_matching_contracts': eligible,
            'matching_event_displayed': counts['matching_event_displayed'],
            'displayed_event_coverage': counts['matching_event_displayed'] / eligible if eligible else None,
            'interpretation': 'Coverage is per reviewed matching contract, for displayed event candidates only; it is not contract matching accuracy or full-pool recall.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reviews', help='CSV downloaded from the contract review page')
    args = parser.parse_args()
    with open(args.reviews, newline='', encoding='utf-8-sig') as f:
        print(json.dumps(summarize(csv.DictReader(f)), indent=2))


if __name__ == '__main__':
    main()
