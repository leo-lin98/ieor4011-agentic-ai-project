"""Developer tests, task 2: config, prompt, OpenAI client, propose_pair. No network, no key."""
import asyncio
import json
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import httpx2
import openai
import pydantic
from openai.types.responses import (
    ParsedResponse,
    ParsedResponseOutputMessage,
    ParsedResponseOutputText,
    ResponseReasoningItem,
)
from openai.types.responses.response_output_refusal import ResponseOutputRefusal

import collector.config
from proposer import config
from proposer.contract import Event, Market
from proposer.llm import InvalidResponseError, OpenAIProposerClient, ProposerCallError
from proposer.prompt import build_messages, build_retry_messages, event_payload
from proposer.propose import Accepted, Rejected, RejectionKind, propose_pair
from proposer.response import (
    Explanation,
    LegCondition,
    ProposedMatch,
    ProposerResponse,
    Relationship,
)
from proposer.validate import find_failures

HASH = 'HASH-CANARY-1'


def mk_market(uid, rules_hash):
    return Market(market_uid=uid, market_title=f'title {uid}', yes_means=f'yes {uid}', rules=f'rules {uid}',
                  close_time_ms=1767225600000, rules_hash=rules_hash)


KALSHI = Event(event_uid='KALSHI:EV', title='Kalshi title', category='Politics',
               markets=(mk_market('KALSHI:K1', HASH), mk_market('KALSHI:K2', None)))
POLYUS = Event(event_uid='POLYUS:EV', title='Poly title', category=None,
               markets=(mk_market('POLYUS:P1', 'h'),))


class ConfigTests(unittest.TestCase):
    def test_constants_follow_the_plan(self):
        self.assertEqual(config.MODEL, 'gpt-6-luna')
        self.assertEqual(config.CANDIDATES_SENT, 3)
        self.assertEqual(config.CONCURRENCY, 16)
        self.assertEqual(config.MAX_OUTPUT_TOKENS, 16000)
        self.assertEqual(config.REASONING_EFFORT, 'medium')
        self.assertEqual(config.API_MAX_RETRIES, 5)
        self.assertEqual(config.API_TIMEOUT_S, 120)
        self.assertEqual(config.PROPOSALS_DIR, collector.config.DATA / 'proposals')


class EventPayloadTests(unittest.TestCase):
    def test_payload_has_exactly_the_d13_fields_and_no_hash(self):
        payload = event_payload(KALSHI)
        self.assertEqual(payload, {
            'title': 'Kalshi title', 'category': 'Politics',
            'markets': [
                {'market_uid': 'KALSHI:K1', 'market_title': 'title KALSHI:K1', 'yes_means': 'yes KALSHI:K1',
                 'rules': 'rules KALSHI:K1', 'close_time_ms': 1767225600000},
                {'market_uid': 'KALSHI:K2', 'market_title': 'title KALSHI:K2', 'yes_means': 'yes KALSHI:K2',
                 'rules': 'rules KALSHI:K2', 'close_time_ms': 1767225600000},
            ]})
        self.assertNotIn(HASH, json.dumps(payload))

    def test_payload_keeps_missing_values_as_null(self):
        self.assertIsNone(event_payload(POLYUS)['category'])


class BuildMessagesTests(unittest.TestCase):
    def test_user_message_is_one_json_document_with_both_events(self):
        messages = build_messages(KALSHI, POLYUS)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        document = json.loads(messages[1]['content'])
        self.assertEqual(document, {'kalshi_event': event_payload(KALSHI), 'polymarket_event': event_payload(POLYUS)})

    def test_non_ascii_event_text_appears_literally_in_the_user_message(self):
        event = Event(event_uid='KALSHI:ES', title='Sánchez wins', category=None,
                      markets=(mk_market('KALSHI:S1', None),))
        content = build_messages(event, POLYUS)[1]['content']
        self.assertIn('Sánchez wins', content)

    def test_system_message_defines_relationships_and_treats_events_as_untrusted_data(self):
        system = build_messages(KALSHI, POLYUS)[0]['content']
        for definition in (
            'pay YES in exactly the same outcomes',
            'exactly the outcomes where the other pays NO',
            'cannot pay YES at the same time',
        ):
            self.assertIn(definition, system)
        for phrase in ('none', 'R wins by more than 10', 'untrusted', 'not instructions',
                       'yes_conditions', 'assumptions', 'rule_differences', 'summary'):
            self.assertIn(phrase, system)

    def test_system_message_holds_no_event_data(self):
        system = build_messages(KALSHI, POLYUS)[0]['content']
        for text in ('Kalshi title', 'KALSHI:K1', 'rules POLYUS:P1'):
            self.assertNotIn(text, system)


RETRY_FAILURES = ['match 0: market X is not in the Kalshi event', 'match 1: basket needs 2 legs']


class BuildRetryMessagesTests(unittest.TestCase):
    def test_previous_response_becomes_an_assistant_message_then_failures_as_user_message(self):
        original = build_messages(KALSHI, POLYUS)
        previous = ProposerResponse(matches=[])
        retry = build_retry_messages(original, previous, RETRY_FAILURES)
        self.assertEqual(retry[:2], original)
        self.assertEqual([m['role'] for m in retry[2:]], ['assistant', 'user'])
        self.assertEqual(ProposerResponse.model_validate_json(retry[2]['content']), previous)
        for failure in RETRY_FAILURES:
            self.assertIn(failure, retry[3]['content'])

    def test_without_a_previous_response_only_the_failures_are_added(self):
        original = build_messages(KALSHI, POLYUS)
        retry = build_retry_messages(original, None, RETRY_FAILURES)
        self.assertEqual([m['role'] for m in retry[2:]], ['user'])

    def test_original_messages_are_not_mutated(self):
        original = build_messages(KALSHI, POLYUS)
        snapshot = [dict(m) for m in original]
        retry = build_retry_messages(original, None, RETRY_FAILURES)
        self.assertEqual(original, snapshot)
        self.assertIsNot(retry, original)


def parsed_response(parsed, status='completed', incomplete_reason=None, refusal=None, leading=()):
    """A ParsedResponse built without validation: one message whose text parsed to `parsed`, or a refusal."""
    if refusal is not None:
        part = ResponseOutputRefusal.model_construct(type='refusal', refusal=refusal)
    else:
        part = ParsedResponseOutputText.model_construct(type='output_text', text='{}', annotations=[], parsed=parsed)
    message = ParsedResponseOutputMessage.model_construct(
        type='message', id='m', role='assistant', status='completed', content=[part])
    details = None if incomplete_reason is None else SimpleNamespace(reason=incomplete_reason)
    return ParsedResponse.model_construct(id='r', status=status, output=[*leading, message],
                                          incomplete_details=details)


class FakeResponses:
    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = []

    async def parse(self, **kwargs):
        self.kwargs.append(kwargs)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeAsyncOpenAI:
    def __init__(self, outcome):
        self.responses = FakeResponses(outcome)


MESSAGES = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'usr'}]


def propose_with(outcome):
    fake = FakeAsyncOpenAI(outcome)
    client = OpenAIProposerClient(fake, 'model-x', 'low', 99)
    return fake, asyncio.run(client.propose(MESSAGES))


REQUEST = httpx2.Request('POST', 'https://api.openai.com/v1/responses')


def status_error(cls, status, code):
    response = httpx2.Response(status, request=REQUEST)
    return cls('boom', response=response, body={'code': code, 'message': 'boom', 'type': 'x', 'param': None})


class OpenAIProposerClientTests(unittest.TestCase):
    def test_one_parse_call_with_strict_schema_and_no_temperature(self):
        expected = ProposerResponse(matches=[])
        fake, result = propose_with(parsed_response(expected))
        self.assertIs(result, expected)
        self.assertEqual(fake.responses.kwargs, [{
            'model': 'model-x', 'input': MESSAGES, 'text_format': ProposerResponse,
            'reasoning': {'effort': 'low'}, 'max_output_tokens': 99}])

    def test_incomplete_response_raises_even_when_text_parsed(self):
        with self.assertRaisesRegex(InvalidResponseError, 'max_output_tokens'):
            propose_with(parsed_response(ProposerResponse(matches=[]), 'incomplete', 'max_output_tokens'))

    def test_reasoning_item_before_the_message_is_skipped(self):
        reasoning = ResponseReasoningItem.model_construct(type='reasoning', id='rs', summary=[])
        expected = ProposerResponse(matches=[])
        _, result = propose_with(parsed_response(expected, leading=(reasoning,)))
        self.assertIs(result, expected)

    def test_incomplete_without_details_raises_invalid_response(self):
        with self.assertRaises(InvalidResponseError):
            propose_with(parsed_response(ProposerResponse(matches=[]), 'incomplete', None))

    def test_refusal_text_is_in_the_error(self):
        with self.assertRaisesRegex(InvalidResponseError, 'I cannot help'):
            propose_with(parsed_response(None, refusal='I cannot help'))

    def test_schema_validation_error_raises_invalid_response(self):
        with self.assertRaises(pydantic.ValidationError) as caught:
            ProposerResponse.model_validate_json('{"matches": 3}')
        with self.assertRaises(InvalidResponseError):
            propose_with(caught.exception)

    def test_unparsed_output_raises_invalid_response(self):
        with self.assertRaises(InvalidResponseError):
            propose_with(parsed_response(None))

    def test_server_errors_become_proposer_call_error(self):
        for status in (500, 503):
            with self.subTest(status=status), self.assertRaises(ProposerCallError):
                propose_with(status_error(openai.InternalServerError, status, None))

    def test_rate_limit_errors_become_proposer_call_error(self):
        for code in ('rate_limit_exceeded', None):
            with self.subTest(code=code), self.assertRaises(ProposerCallError):
                propose_with(status_error(openai.RateLimitError, 429, code))

    def test_transient_failure_logs_a_warning_with_structured_fields(self):
        error = status_error(openai.RateLimitError, 429, 'rate_limit_exceeded')
        with self.assertLogs('proposer.llm', level='WARNING') as logs, self.assertRaises(ProposerCallError):
            propose_with(error)
        record = logs.records[0]
        self.assertEqual(record.levelname, 'WARNING')
        self.assertEqual(record.error_type, 'RateLimitError')
        self.assertEqual(record.error_code, 'rate_limit_exceeded')
        self.assertEqual(record.model, 'model-x')
        self.assertNotIn('rate_limit_exceeded', record.getMessage())

    def test_context_length_bad_request_becomes_proposer_call_error(self):
        with self.assertRaises(ProposerCallError):
            propose_with(status_error(openai.BadRequestError, 400, 'context_length_exceeded'))

    def test_other_api_errors_propagate_unchanged(self):
        errors = (status_error(openai.BadRequestError, 400, 'invalid_request_error'),
                  status_error(openai.AuthenticationError, 401, 'invalid_api_key'),
                  status_error(openai.PermissionDeniedError, 403, None),
                  status_error(openai.NotFoundError, 404, 'model_not_found'))
        for error in errors:
            with self.subTest(error=type(error).__name__), self.assertRaises(openai.APIStatusError) as caught:
                propose_with(error)
            self.assertIs(caught.exception, error)

    def test_unrelated_exceptions_propagate_unchanged(self):
        error = RuntimeError('bug')
        with self.assertRaises(RuntimeError) as caught:
            propose_with(error)
        self.assertIs(caught.exception, error)

    def test_insufficient_quota_propagates_unchanged(self):
        error = status_error(openai.RateLimitError, 429, 'insufficient_quota')
        with self.assertRaises(openai.RateLimitError) as caught:
            propose_with(error)
        self.assertIs(caught.exception, error)

    def test_connection_and_timeout_errors_become_proposer_call_error(self):
        for error in (openai.APIConnectionError(request=REQUEST), openai.APITimeoutError(REQUEST)):
            with self.subTest(error=type(error).__name__), self.assertRaises(ProposerCallError):
                propose_with(error)


def identical_match(kalshi_uid, polyus_uid):
    return ProposedMatch(
        relationship=Relationship.identical, kalshi_legs=[kalshi_uid], polyus_legs=[polyus_uid],
        explanation=Explanation(
            yes_conditions=[LegCondition(market_uid=kalshi_uid, yes_condition='k'),
                            LegCondition(market_uid=polyus_uid, yes_condition='p')],
            assumptions=[], rule_differences=[], summary='s'))


VALID = ProposerResponse(matches=[identical_match('KALSHI:K1', 'POLYUS:P1')])
UNKNOWN_MARKET = ProposerResponse(matches=[identical_match('KALSHI:K1', 'POLYUS:NOPE')])


class ScriptedClient:
    """ProposerClient fake: each call consumes the next scripted response or exception."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def propose(self, messages):
        self.calls.append(list(messages))
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ProposePairTests(unittest.TestCase):
    def test_valid_response_is_accepted_after_one_call(self):
        client = ScriptedClient([VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Accepted(matches=VALID.matches))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0], build_messages(KALSHI, POLYUS))

    def test_invalid_response_is_retried_once_with_its_failures_attached(self):
        failures = find_failures(UNKNOWN_MARKET, KALSHI, POLYUS)
        self.assertTrue(failures)
        client = ScriptedClient([UNKNOWN_MARKET, VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Accepted(matches=VALID.matches))
        self.assertEqual(client.calls[1], build_retry_messages(client.calls[0], UNKNOWN_MARKET, failures))

    def test_invalid_twice_is_rejected_naming_the_failures_after_two_calls(self):
        client = ScriptedClient([UNKNOWN_MARKET, UNKNOWN_MARKET, VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertIsInstance(result, Rejected)
        self.assertEqual(result.kind, RejectionKind.invalid_response)
        self.assertIn('POLYUS:NOPE', result.reason)
        self.assertEqual(len(client.calls), 2)

    def test_invalid_response_error_is_retried_once_with_its_reason_attached(self):
        client = ScriptedClient([InvalidResponseError('REASON-ONE'), VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Accepted(matches=VALID.matches))
        self.assertEqual(client.calls[1], build_retry_messages(client.calls[0], None, ['REASON-ONE']))

    def test_invalid_response_error_twice_is_rejected_with_the_last_reason(self):
        client = ScriptedClient([InvalidResponseError('REASON-A'), InvalidResponseError('REASON-B'), VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Rejected(kind=RejectionKind.invalid_response, reason='REASON-B'))
        self.assertEqual(len(client.calls), 2)

    def test_call_error_is_rejected_as_api_error_without_retry(self):
        client = ScriptedClient([ProposerCallError('CALL-ERR'), VALID])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Rejected(kind=RejectionKind.api_error, reason='CALL-ERR'))
        self.assertEqual(len(client.calls), 1)

    def test_call_error_on_the_retry_is_rejected_as_api_error(self):
        client = ScriptedClient([UNKNOWN_MARKET, ProposerCallError('CALL-ERR2')])
        result = asyncio.run(propose_pair(client, KALSHI, POLYUS))
        self.assertEqual(result, Rejected(kind=RejectionKind.api_error, reason='CALL-ERR2'))
        self.assertEqual(len(client.calls), 2)

    def test_other_exceptions_propagate(self):
        client = ScriptedClient([RuntimeError('bug')])
        with self.assertRaises(RuntimeError):
            asyncio.run(propose_pair(client, KALSHI, POLYUS))

    def test_results_are_frozen(self):
        for result in (Accepted(matches=[]), Rejected(kind=RejectionKind.api_error, reason='r')):
            with self.assertRaises(FrozenInstanceError):
                result.matches = []

    def test_empty_matches_is_accepted(self):
        client = ScriptedClient([ProposerResponse(matches=[])])
        self.assertEqual(asyncio.run(propose_pair(client, KALSHI, POLYUS)), Accepted(matches=[]))


if __name__ == '__main__':
    unittest.main()
