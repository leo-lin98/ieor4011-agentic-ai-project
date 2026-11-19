"""Frozen collector catalogs and market eligibility."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from collector.kalshi import RULES_FIELDS

PLATFORMS = ("KALSHI", "POLYUS")
RULE_INPUT = {"KALSHI": "rules_primary", "POLYUS": "description"}
TEXT_FIELDS = (("Event", "event_title"), ("Market", "market_title"), ("Yes title", "yes_title"))
RULE_DETAILS = {
    "KALSHI": (
        ("Primary rules", "rules_primary"), ("Secondary rules", "rules_secondary"),
        ("Early close condition", "early_close_condition"), ("Settlement sources", "settlement_sources"),
    ),
    "POLYUS": (
        ("Description", "description"), ("Rules disclaimer", "rules_disclaimer"),
        ("Event description", "event_description"), ("Resolution source", "resolution_source"),
    ),
}
FINGERPRINT_FIELDS = (*RULES_FIELDS, "early_close_condition", "can_close_early",
                      "event_end_ms", "yes_side_label", "no_side_label", "contract_terms_url", "contract_url")
RETRIEVAL_COLUMNS = (
    "market_uid", "platform", "event_uid", "category", "status_canonical", "close_time_ms",
    "catalog_pulled_ms", "last_seen_ms", *(field for _, field in TEXT_FIELDS),
)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def input_parts(market):
    """Return the title lines and optional final rule line separately."""
    seen = set()
    titles = []
    for label, field in TEXT_FIELDS:
        value = (market.get(field) or "").strip()
        key = " ".join(value.split()).casefold()
        if key and key not in seen:
            seen.add(key)
            titles.append(f"{label}: {value}")
    rules = (market.get(RULE_INPUT[market["platform"]]) or "").strip()
    include_rules = rules and " ".join(rules.split()).casefold() not in seen
    return "\n".join(titles), f"Rules: {rules}" if include_rules else ""


def rule_fingerprint(market):
    fields = [market.get(field) for field in FINGERPRINT_FIELDS]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def same_group(query, candidate):
    return query is None or candidate is None or query == candidate


class Catalog:
    """Derive eligibility from frozen T1 data, including older schemas with optional fields."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.latest = {}
        self.markets = {}
        self.open = {}
        self.groups = {}
        self.categories = {}
        for platform in PLATFORMS:
            path = self.path(platform)
            names = set(pq.ParquetFile(path).schema_arrow.names)
            required = set(RETRIEVAL_COLUMNS) - {"category", *(field for _, field in TEXT_FIELDS)}
            if missing := required - names:
                raise ValueError(f"{path} is missing catalog columns: {', '.join(sorted(missing))}")
            columns = [key for key in (*RETRIEVAL_COLUMNS, RULE_INPUT[platform]) if key in names]
            rows = pq.read_table(path, columns=columns).to_pylist()
            timestamps = [row["catalog_pulled_ms"] for row in rows if row["catalog_pulled_ms"] is not None]
            if rows and not timestamps:
                raise ValueError(f"{path} is missing catalog pull timestamps")
            self.latest[platform] = max(timestamps, default=None)
            markets = {}
            for row in rows:
                uid = row["market_uid"]
                if not uid or not uid.startswith(f"{platform}:") or row["platform"] != platform:
                    raise ValueError(f"Invalid market UID or platform in {path}: {uid}")
                if uid in markets:
                    raise ValueError(f"Duplicate market UID: {uid}")
                markets[uid] = row
            self.markets[platform] = markets
        self.as_of = max((t for t in self.latest.values() if t is not None), default=None)
        for platform, markets in self.markets.items():
            current = [
                row for row in markets.values()
                if row["last_seen_ms"] == self.latest[platform] and self.latest[platform] is not None
            ]
            categories = defaultdict(set)
            for row in current:
                if event := row["event_uid"]:
                    values = categories[event]
                    if category := (row.get("category") or "").strip().casefold():
                        values.add(category)
            conflicts = {event for event, values in categories.items() if len(values) > 1}
            self.categories[platform] = {
                "missing_event_uids": sorted(event for event, values in categories.items() if not values),
                "conflicting_event_uids": sorted(conflicts),
                "markets_missing_event_uid": sum(not row["event_uid"] for row in current),
            }
            for kind in ("missing", "conflicting"):
                self.categories[platform][f"{kind}_event_count"] = len(
                    self.categories[platform][f"{kind}_event_uids"]
                )
            self.open[platform] = {
                row["market_uid"]: row for row in current
                if row["status_canonical"] == "open"
                and (row["close_time_ms"] is None or row["close_time_ms"] > self.as_of)
            }
            self.groups[platform] = {
                uid: ("sports" in values if (values := categories.get(row["event_uid"])) else None)
                for uid, row in self.open[platform].items()
                if row["event_uid"] not in conflicts
            }

    def path(self, platform):
        return self.directory / f"{platform}.parquet"

    def full_rows(self, platform, uids):
        uids = set(uids) & self.markets[platform].keys()
        if not uids:
            return {}
        rows = pq.read_table(self.path(platform), filters=[("market_uid", "in", sorted(uids))])
        return {row["market_uid"]: row for row in rows.to_pylist()}

    def exclusion(self, kalshi, polyus):
        for platform, uid in zip(PLATFORMS, (kalshi, polyus)):
            if uid not in self.markets[platform]:
                return f"{platform.lower()}_absent"
            if uid not in self.open[platform]:
                return f"{platform.lower()}_not_open"
            if uid not in self.groups[platform]:
                return f"{platform.lower()}_category_conflict"
        if not same_group(self.groups["KALSHI"][kalshi], self.groups["POLYUS"][polyus]):
            return "sports_mismatch"
        return None
