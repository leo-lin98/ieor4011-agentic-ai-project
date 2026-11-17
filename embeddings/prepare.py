"""Load and prepare standardized collector profiles."""
import pandas as pd

FIELDS = ("title", "market_title", "yes_means")

def text_for(row, include_rules=False):
    fields = FIELDS + (("rules",) if include_rules else ())
    return "\n".join(f"{key}: {row[key].strip()}" for key in fields
                     if isinstance(row.get(key), str) and row[key].strip())

def load(path):
    df = pd.read_parquet(path)
    required = {"market_uid", "event_uid", "platform", *FIELDS}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing profile columns: {sorted(missing)}")
    return df
