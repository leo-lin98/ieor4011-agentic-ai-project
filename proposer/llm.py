"""The LLM connector: one structured-output call to OpenAI."""
import logging
from typing import Literal, Protocol

from openai import (
    APIConnectionError,
    APIError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from openai.types.responses import EasyInputMessageParam, ParsedResponse
from openai.types.shared_params import Reasoning
from pydantic import ValidationError

from proposer.prompt import Message
from proposer.response import ProposerResponse

logger = logging.getLogger(__name__)

# The SDK's ReasoningEffort alias also allows None, which would send {"effort": None}.
ReasoningEffortName = Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']

QUOTA_CODE = 'insufficient_quota'
CONTEXT_LENGTH_CODE = 'context_length_exceeded'


class InvalidResponseError(Exception):
    """The model's answer is unusable: a refusal, incomplete, unparsable or empty."""


class ProposerCallError(Exception):
    """A non-fatal API failure (R8): the pair is rejected and the batch continues."""


class ProposerClient(Protocol):
    """Anything that can answer one proposer call."""

    async def propose(self, messages: list[Message]) -> ProposerResponse:
        """Return the model's structured answer to the messages."""
        ...


def _refusal(response: ParsedResponse[ProposerResponse]) -> str | None:
    """Return the model's refusal text, or None if it did not refuse."""
    for item in response.output:
        if item.type == 'message':
            for part in item.content:
                if part.type == 'refusal':
                    return part.refusal
    return None


def _is_transient(error: APIError) -> bool:
    """True for failures that reject one pair (R8): connection/timeout, 5xx and non-quota 429."""
    if isinstance(error, RateLimitError):
        return error.code != QUOTA_CODE
    if isinstance(error, BadRequestError):
        return error.code == CONTEXT_LENGTH_CODE
    return isinstance(error, (APIConnectionError, InternalServerError))


class OpenAIProposerClient(ProposerClient):
    """Calls OpenAI's Responses API with strict structured outputs. The AsyncOpenAI client is injected."""

    def __init__(self, client: AsyncOpenAI, model: str, reasoning_effort: ReasoningEffortName, max_output_tokens: int):
        """Store the injected client and the call settings."""
        self._client = client
        self._model = model
        self._reasoning_effort: ReasoningEffortName = reasoning_effort
        self._max_output_tokens = max_output_tokens

    async def propose(self, messages: list[Message]) -> ProposerResponse:
        """Make one call. temperature is never sent."""
        try:
            response = await self._client.responses.parse(
                model=self._model,
                input=[EasyInputMessageParam(role=m['role'], content=m['content']) for m in messages],
                text_format=ProposerResponse,
                reasoning=Reasoning(effort=self._reasoning_effort),
                max_output_tokens=self._max_output_tokens,
            )
        except ValidationError as exc:
            raise InvalidResponseError(f'the response does not match the schema: {exc}') from exc
        except APIError as exc:
            if not _is_transient(exc):
                raise
            logger.warning(
                'proposer API call failed',
                extra={'error_type': type(exc).__name__, 'error_code': exc.code, 'model': self._model},
            )
            raise ProposerCallError(f'API call failed: {type(exc).__name__}: {exc}') from exc
        if response.status == 'incomplete':
            reason = response.incomplete_details.reason if response.incomplete_details else None
            raise InvalidResponseError(f'the response is incomplete: {reason}')
        refusal = _refusal(response)
        if refusal is not None:
            raise InvalidResponseError(f'the model refused: {refusal}')
        parsed = response.output_parsed
        if parsed is None:
            raise InvalidResponseError('the model returned no parsed output')
        return parsed
