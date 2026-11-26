"""Audit and rejected-pair JSONL records (D7, D12, D16)."""
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from proposer.propose import RejectionKind


class TopKEntry(BaseModel):
    """One retrieved candidate event."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    event_uid: str
    rank: int
    score: float


class AuditRecord(BaseModel):
    """What was retrieved and sent for one Kalshi event.

    `sent` lists the candidates selected for sending. If the run aborts, it may include pairs that were never called.
    """

    model_config = ConfigDict(frozen=True, extra='forbid')

    run_id: str
    kalshi_event_uid: str
    model: str
    resolved_revision: str
    mapping_sha256: str
    top_k: list[TopKEntry]
    sent: list[str]


class RejectedRecord(BaseModel):
    """An event pair or record that produced no usable response."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    run_id: str
    kalshi_event_uid: str | None
    polyus_event_uid: str | None
    rank: int | None
    kind: RejectionKind
    reason: str


def append_record(path: Path, record: AuditRecord | RejectedRecord) -> None:
    """Append the record as one compact JSON line. The parent directory must exist."""
    with path.open('a', encoding='utf-8') as file:
        file.write(record.model_dump_json() + '\n')
