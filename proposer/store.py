"""SQLite store of proposed matches, upserted by match_id (R3, D18, D19)."""
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator

from proposer.response import Explanation, Relationship

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS matches (
    match_id TEXT PRIMARY KEY,
    relationship TEXT NOT NULL,
    kalshi_legs TEXT NOT NULL,
    polyus_legs TEXT NOT NULL,
    explanation TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kalshi_event_uid TEXT NOT NULL,
    polyus_event_uid TEXT NOT NULL,
    candidate_rank INTEGER NOT NULL,
    candidate_score REAL NOT NULL,
    rules_hashes TEXT NOT NULL,
    first_proposed_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL
)
"""

_UPSERT = """
INSERT INTO matches (
    match_id, relationship, kalshi_legs, polyus_legs, explanation, run_id, kalshi_event_uid,
    polyus_event_uid, candidate_rank, candidate_score, rules_hashes, first_proposed_ms, updated_ms
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(match_id) DO UPDATE SET
    relationship = excluded.relationship,
    kalshi_legs = excluded.kalshi_legs,
    polyus_legs = excluded.polyus_legs,
    explanation = excluded.explanation,
    run_id = excluded.run_id,
    kalshi_event_uid = excluded.kalshi_event_uid,
    polyus_event_uid = excluded.polyus_event_uid,
    candidate_rank = excluded.candidate_rank,
    candidate_score = excluded.candidate_score,
    rules_hashes = excluded.rules_hashes,
    updated_ms = excluded.updated_ms
"""

_SELECT_FOR_RUN = """
SELECT match_id, relationship, kalshi_legs, polyus_legs, explanation, run_id, kalshi_event_uid,
    polyus_event_uid, candidate_rank, candidate_score, rules_hashes, updated_ms
FROM matches WHERE run_id = ?
"""


class MatchRecord(BaseModel):
    """One proposed match with the context it was proposed in."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    match_id: str
    relationship: Relationship
    kalshi_legs: tuple[str, ...]
    polyus_legs: tuple[str, ...]
    explanation: Explanation
    run_id: str
    kalshi_event_uid: str
    polyus_event_uid: str
    candidate_rank: int
    candidate_score: float
    rules_hashes: dict[str, str | None]
    proposed_ms: int

    @field_validator('kalshi_legs', 'polyus_legs')
    @classmethod
    def _sorted_legs(cls, legs: tuple[str, ...]) -> tuple[str, ...]:
        """Store legs sorted so a match has one canonical leg order."""
        return tuple(sorted(legs))


class MatchStore:
    """Connector to the SQLite matches table. Use as a context manager."""

    def __init__(self, path: Path) -> None:
        """Open the database at path, creating its directory and the matches table if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        try:
            self._connection.execute(_CREATE_TABLE)
            self._connection.commit()
        except sqlite3.Error:
            self._connection.close()
            raise

    def __enter__(self) -> Self:
        """Return the open store."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection. Exceptions from the block propagate."""
        self._connection.close()

    def upsert(self, record: MatchRecord) -> None:
        """Insert the match or update it in place, keeping its first_proposed_ms. Commits."""
        self._connection.execute(_UPSERT, (
            record.match_id, record.relationship.value, json.dumps(record.kalshi_legs),
            json.dumps(record.polyus_legs), record.explanation.model_dump_json(), record.run_id,
            record.kalshi_event_uid, record.polyus_event_uid, record.candidate_rank,
            record.candidate_score, json.dumps(record.rules_hashes), record.proposed_ms,
            record.proposed_ms,
        ))
        self._connection.commit()

    def matches_for_run(self, run_id: str) -> Iterator[MatchRecord]:
        """Yield the matches whose latest proposal came from the run, one row at a time."""
        for row in self._connection.execute(_SELECT_FOR_RUN, (run_id,)):
            yield MatchRecord(
                match_id=row[0], relationship=Relationship(row[1]),
                kalshi_legs=tuple(json.loads(row[2])), polyus_legs=tuple(json.loads(row[3])),
                explanation=Explanation.model_validate_json(row[4]), run_id=row[5],
                kalshi_event_uid=row[6], polyus_event_uid=row[7], candidate_rank=row[8],
                candidate_score=row[9], rules_hashes=json.loads(row[10]), proposed_ms=row[11])
