"""Thin adapter from upstream's candidates.json (streamed) to the proposer's input contract."""
from collections import Counter
from collections.abc import Generator, Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from embeddings.reporting.results import iter_results
from proposer.contract import (
    Candidate,
    Event,
    Market,
    RetrievalMetadata,
    RetrievalResult,
)

_METADATA_KEYS = ('model', 'resolved_revision', 'mapping_sha256')


@dataclass(frozen=True)
class InvalidRecord:
    """A retrieval record that could not be converted; kalshi_event_uid is set when readable."""

    kalshi_event_uid: str | None
    reason: str


def read_metadata(path: Path) -> RetrievalMetadata:
    """Return the retrieval metadata, raising ValueError if the file or metadata is malformed."""
    raw, stream = _open(path)
    with closing(stream):
        return _metadata(path, raw)


def iter_retrieval_results(path: Path) -> Iterator[RetrievalResult | InvalidRecord]:
    """Stream one RetrievalResult per upstream record; a malformed record becomes an InvalidRecord."""
    raw, records = _open(path)
    with closing(records):
        _metadata(path, raw)
        candidate_events = _candidate_events(path, raw)
        for record in records:
            yield _convert_record(record, candidate_events)


def _open(path: Path) -> tuple[Any, Generator[Any, None, None]]:
    """Start the upstream stream; return its raw metadata and the iterator over result records."""
    stream = iter_results(path)
    return next(stream), stream


def _metadata(path: Path, raw: Any) -> RetrievalMetadata:
    """Validate raw upstream metadata into RetrievalMetadata, raising ValueError if malformed."""
    try:
        return RetrievalMetadata.model_validate({k: raw.get(k) for k in _METADATA_KEYS})
    except (ValidationError, AttributeError) as error:
        raise ValueError(f'malformed retrieval metadata in {path}: {error}') from error


def _candidate_events(path: Path, raw_metadata: Any) -> dict[str, Any]:
    """Return upstream's candidate_events by uid (top-only files), or {} when absent."""
    events = raw_metadata.get('candidate_events') or []
    try:
        uids = [e['event_uid'] for e in events]
    except (TypeError, KeyError) as error:
        raise ValueError(f'malformed candidate_events in {path}: {error!r}') from error
    duplicated = sorted(u for u, n in Counter(uids).items() if n > 1)
    if duplicated:
        raise ValueError(f'duplicate candidate_events uids in {path}: {duplicated}')
    return dict(zip(uids, events, strict=True))


def _convert_record(record: Any, candidate_events: dict[str, Any]) -> RetrievalResult | InvalidRecord:
    """Convert one upstream result dict, or describe why it is invalid."""
    kalshi_uid = _readable_uid(record)
    try:
        if not isinstance(record['candidates'], list):
            raise TypeError(f"candidates must be a list, got {type(record['candidates']).__name__}")
        candidates = tuple(
            Candidate(rank=rank, score=entry['score'], event=_event(_resolve(entry, candidate_events)))
            for rank, entry in enumerate(record['candidates'], start=1))
        return RetrievalResult(kalshi_event=_event(record['query']), candidates=candidates)
    except (ValidationError, KeyError, TypeError, AttributeError) as error:
        return InvalidRecord(kalshi_event_uid=kalshi_uid, reason=f'{type(error).__name__}: {error}')


def _readable_uid(record: Any) -> str | None:
    """Return the Kalshi event uid of a record if it can be read."""
    try:
        uid = record['query']['event_uid']
    except (KeyError, TypeError):
        return None
    return uid if isinstance(uid, str) else None


def _resolve(entry: Any, candidate_events: dict[str, Any]) -> Any:
    """Return the upstream event dict of a candidate entry (inline, or looked up for top-only shape)."""
    if 'event' in entry:
        return entry['event']
    return candidate_events[entry['event_uid']]


def _event(raw: Any) -> Event:
    """Map an upstream event dict (markets under 'contracts') to an Event."""
    return Event(
        event_uid=raw['event_uid'], title=raw.get('title'), category=raw.get('category'),
        markets=tuple(_market(c) for c in raw['contracts']))


def _market(raw: Any) -> Market:
    """Map an upstream profile row to a Market."""
    return Market(
        market_uid=raw['market_uid'], market_title=raw.get('market_title'), yes_means=raw.get('yes_means'),
        rules=raw.get('rules'), close_time_ms=raw.get('close_time_ms'), rules_hash=raw.get('rules_hash'))
