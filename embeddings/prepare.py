"""Load market profiles and assemble deterministic event records."""
from collections import Counter, defaultdict
import json
import pandas as pd
import numpy as np

FIELDS = ("title", "market_title", "yes_means")

def text_for(row, include_rules=False):
    fields = FIELDS + (("rules",) if include_rules else ())
    return "\n".join(f"{key}: {row[key].strip()}" for key in fields
                     if isinstance(row.get(key), str) and row[key].strip())

def load(path):
    df = pd.read_parquet(path)
    missing = {"market_uid", "event_uid", "platform", *FIELDS} - set(df.columns)
    if missing:
        raise ValueError(f"Missing profile columns: {sorted(missing)}")
    return df

def clean(value):
    return value.strip() if isinstance(value, str) and value.strip() else None

def build_events(profiles):
    groups = defaultdict(list)
    issues = Counter()
    for row in profiles:
        platform, uid = clean(row.get('platform')), clean(row.get('event_uid'))
        if not platform or not uid:
            issues['missing_platform_or_event_id'] += 1
            continue
        groups[(platform, uid)].append(row)
    events = []
    for (platform, uid), contracts in sorted(groups.items()):
        contracts = sorted(contracts, key=lambda x: str(x.get('market_uid') or ''))
        def consensus(key):
            values = sorted({clean(c.get(key)) for c in contracts if clean(c.get(key))})
            if len(values) > 1:
                issues['conflicting_' + key] += 1
                return None
            if key in ('category', 'subcategory') and any(not clean(c.get(key)) for c in contracts):
                return None
            return values[0] if values else None
        title = consensus('title')
        if not title:
            issues['missing_or_conflicting_title'] += 1
        # Multiple values or missing classifications must not cause false exclusions.
        category = consensus('category')
        subcategory = consensus('subcategory')
        tag_sets = []
        for c in contracts:
            value = c.get('tags')
            if isinstance(value, str):
                try: value = json.loads(value)
                except ValueError: value = [value]
            tag_sets.append(tuple(sorted(str(x) for x in value)) if isinstance(value, (list, tuple, np.ndarray)) else ())
        event_tags = list(tag_sets[0]) if tag_sets and all(t == tag_sets[0] for t in tag_sets) else []
        events.append({'platform': platform, 'event_uid': uid, 'title': title,
                       'category': category, 'subcategory': subcategory, 'tags': event_tags,
                       'option_count': len(contracts), 'contracts': contracts})
    return events, dict(issues)

def event_text(event, representation, max_options=12, max_chars=200):
    title = event.get('title') or ''
    if representation == 'title': return title
    if representation != 'title_options': raise ValueError('Unknown representation: ' + representation)
    options = sorted({(clean(c.get('yes_means')) or clean(c.get('market_title')) or '')[:max_chars]
                      for c in event['contracts']} - {''}, key=lambda x: (x.casefold(), x))[:max_options]
    return '\n'.join([title] + ['Option: ' + o for o in options])
