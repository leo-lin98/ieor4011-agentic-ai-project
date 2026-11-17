"""Summarize the latest observed Kalshi traded-contract volume by event.

Pricing snapshots are reduced to the latest row for each market and joined to
Kalshi market profiles. The output maps event IDs to total observed volume and
coverage counts, along with the latest snapshot timestamp.
"""
from pathlib import Path
import pandas as pd

def event_volumes(snapshot_dir, profile_path):
    # Take the latest snapshot per market and join it to Kalshi event profiles.
    files = sorted(Path(snapshot_dir).rglob('*.parquet'))
    if not files:
        raise ValueError(f'No pricing snapshots in {snapshot_dir}')
    snapshots = pd.concat((pd.read_parquet(f, columns=['market_uid', 'tick_ts', 'volume']) for f in files), ignore_index=True)
    latest = snapshots.sort_values('tick_ts', kind='stable').drop_duplicates('market_uid', keep='last')
    profiles = pd.read_parquet(profile_path, columns=['market_uid', 'event_uid', 'platform'])
    profiles = profiles[profiles['platform'] == 'KALSHI'].drop_duplicates('market_uid')
    rows = profiles.merge(latest, on='market_uid', how='left')
    groups = rows.groupby('event_uid', dropna=True)
    # Sum observed market volumes and report how many contracts had data.
    result = {}
    for uid, group in groups:
        values = pd.to_numeric(group['volume'], errors='coerce')
        total = values.sum(min_count=1)
        result[uid] = {'total': float(total) if pd.notna(total) else None,
                       'observed': int(values.notna().sum()), 'contracts': len(group)}
    return result, int(latest['tick_ts'].max())
