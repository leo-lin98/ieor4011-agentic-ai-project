"""The proposer's input contract: what it ingests from retrieval, independent of upstream's file shape."""
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

KALSHI_PREFIX = 'KALSHI:'
POLYUS_PREFIX = 'POLYUS:'


class ContractModel(BaseModel):
    """Base for all contract models: immutable and strict about unknown fields."""

    model_config = ConfigDict(frozen=True, extra='forbid')


class Market(ContractModel):
    """One tradable Yes/No outcome. Integral floats are accepted for close_time_ms; fractions are rejected."""

    market_uid: str
    market_title: str | None
    yes_means: str | None
    rules: str | None
    close_time_ms: int | None
    rules_hash: str | None


class Event(ContractModel):
    """A group of markets on one platform, all sharing the event's platform prefix."""

    event_uid: str
    title: str | None
    category: str | None
    markets: tuple[Market, ...]

    @model_validator(mode='after')
    def _check_markets(self) -> Self:
        """Require at least one market, unique market uids, and uids carrying the event's platform prefix."""
        prefix = platform_prefix(self.event_uid)
        if not self.markets:
            raise ValueError(f'event {self.event_uid} has no markets')
        uids = [m.market_uid for m in self.markets]
        if len(set(uids)) != len(uids):
            raise ValueError(f'event {self.event_uid} has duplicate market uids')
        wrong = [u for u in uids if not u.startswith(prefix)]
        if wrong:
            raise ValueError(f'markets {wrong} do not match the platform of event {self.event_uid}')
        return self


def platform_prefix(event_uid: str) -> str:
    """Return the platform prefix of an event uid, or raise ValueError if it is not KALSHI or POLYUS."""
    for prefix in (KALSHI_PREFIX, POLYUS_PREFIX):
        if event_uid.startswith(prefix):
            return prefix
    raise ValueError(f'event uid {event_uid!r} must start with {KALSHI_PREFIX} or {POLYUS_PREFIX}')


class Candidate(ContractModel):
    """A ranked Polymarket event with its retrieval similarity score."""

    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    event: Event


class RetrievalMetadata(ContractModel):
    """Provenance of the retrieval run that produced the results."""

    model: str
    resolved_revision: str
    mapping_sha256: str


class RetrievalResult(ContractModel):
    """One Kalshi event with its ranked Polymarket candidates (possibly none)."""

    kalshi_event: Event
    candidates: tuple[Candidate, ...]

    @model_validator(mode='after')
    def _check_platforms_and_ranks(self) -> Self:
        """Require a Kalshi query, Polymarket candidates, and candidate ranks 1..n in order."""
        if not self.kalshi_event.event_uid.startswith(KALSHI_PREFIX):
            raise ValueError(f'query event {self.kalshi_event.event_uid} is not a Kalshi event')
        for candidate in self.candidates:
            if not candidate.event.event_uid.startswith(POLYUS_PREFIX):
                raise ValueError(f'candidate event {candidate.event.event_uid} is not a Polymarket event')
        ranks = [c.rank for c in self.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError(f'candidate ranks must be 1..n in order, got {ranks}')
        return self
