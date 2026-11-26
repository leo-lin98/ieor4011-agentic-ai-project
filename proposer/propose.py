"""The per-pair loop: one LLM call, validated, with at most one retry (D16)."""
from dataclasses import dataclass
from enum import StrEnum

from proposer.contract import Event
from proposer.llm import InvalidResponseError, ProposerCallError, ProposerClient
from proposer.prompt import Message, build_messages, build_retry_messages
from proposer.response import ProposedMatch, ProposerResponse
from proposer.validate import find_failures


class RejectionKind(StrEnum):
    """Why an event pair or record was rejected."""

    invalid_response = 'invalid_response'
    api_error = 'api_error'
    invalid_input = 'invalid_input'


@dataclass(frozen=True)
class Accepted:
    """The response passed validation. An empty list means the pair has no matches."""

    matches: list[ProposedMatch]


@dataclass(frozen=True)
class Rejected:
    """The pair produced no usable response."""

    kind: RejectionKind
    reason: str


async def propose_pair(
    client: ProposerClient, kalshi_event: Event, polyus_event: Event
) -> Accepted | Rejected:
    """Ask the model for the matches between two events. At most two calls (D16)."""
    try:
        return await _propose_with_one_retry(client, kalshi_event, polyus_event)
    except ProposerCallError as exc:
        return Rejected(kind=RejectionKind.api_error, reason=str(exc))


async def _propose_with_one_retry(
    client: ProposerClient, kalshi_event: Event, polyus_event: Event
) -> Accepted | Rejected:
    """Call the model, and once more with the failures attached if the first response is unusable."""
    messages = build_messages(kalshi_event, polyus_event)
    response, failures = await _attempt(client, messages, kalshi_event, polyus_event)
    if response is not None and not failures:
        return Accepted(matches=response.matches)
    retry_messages = build_retry_messages(messages, response, failures)
    response, failures = await _attempt(client, retry_messages, kalshi_event, polyus_event)
    if response is not None and not failures:
        return Accepted(matches=response.matches)
    return Rejected(kind=RejectionKind.invalid_response, reason='; '.join(failures))


async def _attempt(
    client: ProposerClient, messages: list[Message], kalshi_event: Event, polyus_event: Event
) -> tuple[ProposerResponse | None, list[str]]:
    """Make one call. Return the parsed response (None if unusable) and its failures."""
    try:
        response = await client.propose(messages)
    except InvalidResponseError as exc:
        return None, [str(exc)]
    return response, find_failures(response, kalshi_event, polyus_event)
