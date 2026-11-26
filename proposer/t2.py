"""Merge proposed matches into the T2 match_flags table (D11, ADR 0001, R1, R2, R9)."""
import logging
from collections.abc import Iterable

from collector.flags import load_flags, save_flags
from proposer.response import Relationship
from proposer.store import MatchRecord

logger = logging.getLogger(__name__)

Row = dict[str, object]

_PAIR_RELATIONSHIPS = (Relationship.identical, Relationship.complementary)
_REVIEW_FIELDS = ('judge_verdict', 'numeric_checks_passed', 'rules_review_status', 'human_review_status')


def merge_proposals(existing: list[Row], matches: Iterable[MatchRecord], now_ms: int) -> list[Row]:
    """Return the flag rows after merging identical and complementary matches. Inputs are not mutated.

    Baskets are skipped. A pair proposed as both identical and complementary is skipped with a warning.
    """
    by_pair = _group_by_pair(matches)
    rows = [dict(row) for row in existing]
    row_index = {row['pair_id']: position for position, row in enumerate(rows)}
    for pair_id, pair_matches in by_pair.items():
        if len({match.relationship for match in pair_matches}) > 1:
            logger.warning(
                'pair proposed as both identical and complementary; skipped',
                extra={'pair_id': pair_id, 'run_ids': sorted({match.run_id for match in pair_matches})})
            continue
        match = pair_matches[-1]
        if pair_id not in row_index:
            rows.append(_new_row(pair_id, match, now_ms))
        else:
            rows[row_index[pair_id]] = _updated_row(rows[row_index[pair_id]], match, now_ms)
    return rows


def write_to_t2(matches: Iterable[MatchRecord], now_ms: int) -> None:
    """Merge the matches into the stored match_flags table and save it."""
    save_flags(merge_proposals(load_flags(), matches, now_ms))


def _group_by_pair(matches: Iterable[MatchRecord]) -> dict[str, list[MatchRecord]]:
    """Group non-basket matches by pair_id, in first-seen order."""
    grouped: dict[str, list[MatchRecord]] = {}
    for match in matches:
        if match.relationship in _PAIR_RELATIONSHIPS:
            grouped.setdefault(_pair_id(match), []).append(match)
    return grouped


def _pair_id(match: MatchRecord) -> str:
    """Return '<kalshi_uid>__<polyus_uid>' (DECISIONS.md D2) from the match's single legs."""
    return f'{match.kalshi_legs[0]}__{match.polyus_legs[0]}'


def _new_row(pair_id: str, match: MatchRecord, now_ms: int) -> Row:
    """A freshly proposed pair: inactive and unreviewed (R1)."""
    return {
        'pair_id': pair_id,
        'kalshi_market_uid': match.kalshi_legs[0],
        'polyus_market_uid': match.polyus_legs[0],
        'market_identical': match.relationship == Relationship.identical,
        'relationship': match.relationship.value,
        'proposer_explanation': match.explanation.model_dump_json(),
        'judge_verdict': None,
        'numeric_checks_passed': None,
        'rules_review_status': None,
        'human_review_status': None,
        'kalshi_rules_hash': match.rules_hashes.get(match.kalshi_legs[0]),
        'polyus_rules_hash': match.rules_hashes.get(match.polyus_legs[0]),
        'flagged_ts': now_ms,
        'updated_ts': now_ms,
        'is_active': False,
        'source': 'proposer',
        'note': None,
    }


def _updated_row(row: Row, match: MatchRecord, now_ms: int) -> Row:
    """Apply R2 to an existing row: only proposer rows change, and review state resets on a real change."""
    if row['source'] != 'proposer':
        return row
    kalshi_hash, kalshi_changed = _merge_hash(row['kalshi_rules_hash'], match.rules_hashes.get(match.kalshi_legs[0]))
    polyus_hash, polyus_changed = _merge_hash(row['polyus_rules_hash'], match.rules_hashes.get(match.polyus_legs[0]))
    relationship_changed = row['relationship'] != match.relationship.value
    updated: Row = {
        **row,
        'relationship': match.relationship.value,
        'market_identical': match.relationship == Relationship.identical,
        'proposer_explanation': match.explanation.model_dump_json(),
        'kalshi_rules_hash': kalshi_hash,
        'polyus_rules_hash': polyus_hash,
        'updated_ts': now_ms,
    }
    if relationship_changed or kalshi_changed or polyus_changed:
        updated.update({field: None for field in _REVIEW_FIELDS})
        updated['is_active'] = False
    return updated


def _merge_hash(old: object, new: str | None) -> tuple[object, bool]:
    """Return the hash to store and whether it counts as a change (both non-null and different)."""
    if new is None:
        return old, False
    if old is None:
        return new, False
    return new, old != new
