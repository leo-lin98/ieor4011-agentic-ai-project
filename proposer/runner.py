"""One batch run: retrieval results in, audit, rejected, match store and T2 out (D1, D7, D12, D14, D16, D18, D20)."""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from collector.util import now_ms
from proposer import adapter, config
from proposer.audit import AuditRecord, RejectedRecord, TopKEntry, append_record
from proposer.contract import Candidate, Event, RetrievalMetadata, RetrievalResult
from proposer.llm import ProposerClient
from proposer.propose import Accepted, RejectionKind, propose_pair
from proposer.response import ProposedMatch, match_id
from proposer.store import MatchRecord, MatchStore
from proposer.t2 import write_to_t2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    """Counts for one run. pairs_sent counts distinct pairs; matches includes baskets."""

    kalshi_events: int
    pairs_sent: int
    matches: int
    rejected: int


@dataclass(frozen=True)
class _Pair:
    """One Kalshi event and one of its sent candidates, with the rank and score it was retrieved at."""

    kalshi_event: Event
    candidate: Candidate


@dataclass
class _Output:
    """Where one run writes. Only the event loop's thread touches it, and never across an await."""

    run_id: str
    run_dir: Path
    store: MatchStore
    kalshi_events: int
    pairs_sent: int
    matches: int
    rejected: int

    def audit(self, record: AuditRecord) -> None:
        """Append the audit line for one Kalshi event."""
        append_record(self.run_dir / 'audit.jsonl', record)
        self.kalshi_events += 1

    def reject(self, record: RejectedRecord) -> None:
        """Append a rejected-pair or rejected-record line."""
        append_record(self.run_dir / 'rejected.jsonl', record)
        self.rejected += 1

    def store_match(self, record: MatchRecord) -> None:
        """Upsert one match into the store."""
        self.store.upsert(record)
        self.matches += 1


async def run(
    client: ProposerClient, candidates_path: Path, proposals_dir: Path, run_id: str, concurrency: int
) -> RunSummary:
    """Run the proposer over every Kalshi event in the candidates file. The run directory must not exist."""
    if concurrency < 1:
        raise ValueError(f'concurrency must be at least 1, got {concurrency}')
    proposals_dir.mkdir(parents=True, exist_ok=True)
    run_dir = proposals_dir / run_id
    run_dir.mkdir()
    metadata = adapter.read_metadata(candidates_path)
    with MatchStore(proposals_dir / 'matches.sqlite3') as store:
        output = _Output(run_id, run_dir, store, kalshi_events=0, pairs_sent=0, matches=0, rejected=0)
        queue: asyncio.Queue[_Pair] = asyncio.Queue(maxsize=concurrency)
        workers = [asyncio.create_task(_worker(client, queue, output)) for _ in range(concurrency)]
        feeder = asyncio.create_task(_feed(candidates_path, metadata, queue, output))
        tasks = [feeder, *workers]
        try:
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                task.result()
        except BaseException:
            await _stop(tasks)
            try:
                write_to_t2(store.matches_for_run(run_id), now_ms())
            except Exception as t2_error:  # any T2 failure must not replace the original error
                logger.exception('T2 write failed during abort', extra={
                    'run_id': run_id, 'error_type': type(t2_error).__name__})
            raise
        await _stop(tasks)
        write_to_t2(store.matches_for_run(run_id), now_ms())
    return RunSummary(output.kalshi_events, output.pairs_sent, output.matches, output.rejected)


async def _stop(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel the tasks and wait for them to finish, discarding their outcomes."""
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _feed(
    candidates_path: Path, metadata: RetrievalMetadata, queue: asyncio.Queue[_Pair], output: _Output
) -> None:
    """Produce every pair, then wait until the workers have processed all of them."""
    await _produce(candidates_path, metadata, queue, output)
    await queue.join()


async def _produce(
    candidates_path: Path, metadata: RetrievalMetadata, queue: asyncio.Queue[_Pair], output: _Output
) -> None:
    """Stream the retrieval file; audit each Kalshi event and enqueue its sent pairs, blocking while the queue is full."""
    for record in adapter.iter_retrieval_results(candidates_path):
        if isinstance(record, adapter.InvalidRecord):
            output.reject(RejectedRecord(
                run_id=output.run_id, kalshi_event_uid=record.kalshi_event_uid, polyus_event_uid=None, rank=None,
                kind=RejectionKind.invalid_input, reason=record.reason))
            continue
        output.audit(_audit_record(output.run_id, metadata, record))
        for candidate in record.candidates[:config.CANDIDATES_SENT]:
            output.pairs_sent += 1
            await queue.put(_Pair(record.kalshi_event, candidate))


async def _worker(client: ProposerClient, queue: asyncio.Queue[_Pair], output: _Output) -> None:
    """Propose pairs from the queue until cancelled, recording each outcome."""
    while True:
        pair = await queue.get()
        outcome = await propose_pair(client, pair.kalshi_event, pair.candidate.event)
        if isinstance(outcome, Accepted):
            for proposed in outcome.matches:
                output.store_match(_match_record(output.run_id, pair.kalshi_event, pair.candidate, proposed))
        else:
            output.reject(RejectedRecord(
                run_id=output.run_id, kalshi_event_uid=pair.kalshi_event.event_uid,
                polyus_event_uid=pair.candidate.event.event_uid, rank=pair.candidate.rank,
                kind=outcome.kind, reason=outcome.reason))
        queue.task_done()


def _audit_record(run_id: str, metadata: RetrievalMetadata, result: RetrievalResult) -> AuditRecord:
    """Describe the retrieval for one Kalshi event: the full top-k and which candidates are sent."""
    return AuditRecord(
        run_id=run_id, kalshi_event_uid=result.kalshi_event.event_uid, model=metadata.model,
        resolved_revision=metadata.resolved_revision, mapping_sha256=metadata.mapping_sha256,
        top_k=[TopKEntry(event_uid=c.event.event_uid, rank=c.rank, score=c.score) for c in result.candidates],
        sent=[c.event.event_uid for c in result.candidates[:config.CANDIDATES_SENT]])


def _match_record(run_id: str, kalshi_event: Event, candidate: Candidate, proposed: ProposedMatch) -> MatchRecord:
    """Build the stored record for one proposed match, carrying the legs' rules hashes (R10)."""
    hashes = {m.market_uid: m.rules_hash for event in (kalshi_event, candidate.event) for m in event.markets}
    legs = [*proposed.kalshi_legs, *proposed.polyus_legs]
    return MatchRecord(
        match_id=match_id(proposed.relationship, proposed.kalshi_legs, proposed.polyus_legs),
        relationship=proposed.relationship, kalshi_legs=tuple(proposed.kalshi_legs),
        polyus_legs=tuple(proposed.polyus_legs), explanation=proposed.explanation, run_id=run_id,
        kalshi_event_uid=kalshi_event.event_uid, polyus_event_uid=candidate.event.event_uid,
        candidate_rank=candidate.rank, candidate_score=candidate.score,
        rules_hashes={uid: hashes[uid] for uid in legs}, proposed_ms=now_ms())
