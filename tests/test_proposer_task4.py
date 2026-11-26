"""Developer tests, task 4: runner and CLI behaviours, driven with a fake ProposerClient and temp directories."""
import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import collector.config
import collector.flags
from proposer import __main__ as cli
from proposer import config, runner
from proposer.llm import OpenAIProposerClient
from proposer.response import (
    Explanation,
    LegCondition,
    ProposedMatch,
    ProposerResponse,
    Relationship,
)
from proposer.runner import run
from proposer.store import MatchStore

SHA = 'b' * 64
REVISION = 'a' * 40


def raw_event(platform, event_id):
    uid = f'{platform}:{event_id}'
    return {
        'platform': platform, 'event_uid': uid, 'title': event_id, 'category': 'Politics',
        'contracts': [{
            'market_uid': f'{platform}:{event_id}-A', 'market_title': 'm', 'yes_means': 'y', 'rules': 'r',
            'close_time_ms': 1767225600000.0, 'rules_hash': f'hash-{event_id}',
        }],
    }


def raw_result(kalshi_id, candidate_count):
    candidates = [{'event': raw_event('POLYUS', f'{kalshi_id}P{i}'), 'score': 1.0 - i / 10}
                  for i in range(1, candidate_count + 1)]
    return {'query': raw_event('KALSHI', kalshi_id), 'candidates': candidates}


def write_candidates(path, results):
    metadata = {'model': 'm', 'resolved_revision': REVISION, 'mapping_sha256': SHA}
    text = '{"metadata":' + json.dumps(metadata) + ',"results":[' + ','.join(json.dumps(r) for r in results) + ']}'
    Path(path).write_text(text, encoding='utf-8')


class EmptyClient:
    """Fake ProposerClient that finds no matches."""

    def __init__(self):
        self.calls = 0

    async def propose(self, messages):
        self.calls += 1
        return ProposerResponse(matches=[])


def identical(kalshi_id, polyus_id):
    legs = [f'KALSHI:{kalshi_id}-A', f'POLYUS:{polyus_id}-A']
    explanation = Explanation(
        yes_conditions=[LegCondition(market_uid=uid, yes_condition='yes') for uid in legs],
        assumptions=[], rule_differences=[], summary='same')
    return ProposedMatch(relationship=Relationship.identical, kalshi_legs=legs[:1], polyus_legs=legs[1:],
                         explanation=explanation)


class PairClient:
    """Fake ProposerClient that works out which pair it was given and answers with respond(kalshi_id, polyus_id)."""

    def __init__(self, respond):
        self.respond = respond
        self.pairs = []

    async def propose(self, messages):
        text = '\n'.join(message['content'] for message in messages)
        kalshi_id = next(k for k in ('K1', 'K2', 'K3') if f'KALSHI:{k}-A' in text)
        polyus_id = next(f'{kalshi_id}P{i}' for i in range(1, 6) if f'POLYUS:{kalshi_id}P{i}-A' in text)
        self.pairs.append((kalshi_id, polyus_id))
        outcome = self.respond(kalshi_id, polyus_id)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def identical_for_every_pair(kalshi_id, polyus_id):
    return ProposerResponse(matches=[identical(kalshi_id, polyus_id)])


class SlowClient:
    """Fake ProposerClient that yields to the event loop for a while and records how many calls overlap."""

    def __init__(self, yields):
        self.yields = yields
        self.in_flight = 0
        self.max_in_flight = 0
        self.on_call = None

    async def propose(self, messages):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.on_call is not None:
            self.on_call()
        try:
            for _ in range(self.yields):
                await asyncio.sleep(0)
        finally:
            self.in_flight -= 1
        return ProposerResponse(matches=[])


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.candidates = root / 'candidates.json'
        self.proposals = root / 'proposals'
        patcher = mock.patch.object(collector.flags, 'FLAGS_PATH', root / 'flags' / 'match_flags.parquet')
        patcher.start()
        self.addCleanup(patcher.stop)

    def go(self, client, run_id, concurrency):
        return asyncio.run(run(client, self.candidates, self.proposals, run_id, concurrency))

    def lines(self, run_id, name):
        path = self.proposals / run_id / name
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class RunDirectoryTests(RunnerCase):
    def test_existing_run_directory_raises_before_any_client_call(self):
        write_candidates(self.candidates, [raw_result('K1', 1)])
        (self.proposals / 'r1').mkdir(parents=True)
        client = EmptyClient()
        with self.assertRaises(FileExistsError):
            self.go(client, 'r1', 1)
        self.assertEqual(client.calls, 0)


class ConcurrencyValidationTests(RunnerCase):
    def test_concurrency_below_one_raises_before_creating_the_run_directory(self):
        write_candidates(self.candidates, [raw_result('K1', 1)])
        for concurrency in (0, -1):
            with self.subTest(concurrency=concurrency):
                client = EmptyClient()
                with self.assertRaisesRegex(ValueError, str(concurrency)):
                    asyncio.run(asyncio.wait_for(
                        run(client, self.candidates, self.proposals, 'r1', concurrency), timeout=5))
                self.assertFalse((self.proposals / 'r1').exists())
                self.assertEqual(client.calls, 0)


class AuditTests(RunnerCase):
    def test_one_audit_line_per_kalshi_event_with_full_top_k_and_top_three_sent(self):
        write_candidates(self.candidates, [raw_result('K1', 5), raw_result('K2', 2)])
        summary = self.go(EmptyClient(), 'r1', 1)
        audit = self.lines('r1', 'audit.jsonl')
        self.assertEqual(summary.kalshi_events, 2)
        self.assertEqual([line['kalshi_event_uid'] for line in audit], ['KALSHI:K1', 'KALSHI:K2'])
        first = audit[0]
        self.assertEqual((first['run_id'], first['model'], first['resolved_revision'], first['mapping_sha256']),
                         ('r1', 'm', REVISION, SHA))
        self.assertEqual([(t['event_uid'], t['rank'], t['score']) for t in first['top_k']],
                         [(f'POLYUS:K1P{i}', i, 1.0 - i / 10) for i in range(1, 6)])
        self.assertEqual(first['sent'], ['POLYUS:K1P1', 'POLYUS:K1P2', 'POLYUS:K1P3'])
        self.assertEqual(audit[1]['sent'], ['POLYUS:K2P1', 'POLYUS:K2P2'])


class AcceptedPairTests(RunnerCase):
    def stored(self, run_id):
        with MatchStore(self.proposals / 'matches.sqlite3') as store:
            return list(store.matches_for_run(run_id))

    def test_each_sent_candidate_is_proposed_and_its_matches_are_stored_with_rank_score_and_hashes(self):
        write_candidates(self.candidates, [raw_result('K1', 5)])
        client = PairClient(identical_for_every_pair)
        summary = self.go(client, 'r1', 1)
        self.assertEqual(client.pairs, [('K1', 'K1P1'), ('K1', 'K1P2'), ('K1', 'K1P3')])
        self.assertEqual((summary.pairs_sent, summary.matches, summary.rejected), (3, 3, 0))
        second = next(r for r in self.stored('r1') if r.polyus_event_uid == 'POLYUS:K1P2')
        self.assertEqual((second.run_id, second.candidate_rank, second.candidate_score), ('r1', 2, 0.8))
        self.assertEqual(second.kalshi_event_uid, 'KALSHI:K1')
        self.assertEqual(second.kalshi_legs, ('KALSHI:K1-A',))
        self.assertEqual(second.polyus_legs, ('POLYUS:K1P2-A',))
        self.assertEqual(second.rules_hashes, {'KALSHI:K1-A': 'hash-K1', 'POLYUS:K1P2-A': 'hash-K1P2'})

    def test_stored_match_id_is_the_sha256_of_relationship_and_leg_uids(self):
        write_candidates(self.candidates, [raw_result('K1', 1)])
        self.go(PairClient(identical_for_every_pair), 'r1', 1)
        (record,) = self.stored('r1')
        expected = hashlib.sha256(b'identical|KALSHI:K1-A|POLYUS:K1P1-A').hexdigest()
        self.assertEqual(record.match_id, expected)


class RejectionTests(RunnerCase):
    def test_rejected_pair_is_recorded_with_its_rank_and_the_batch_continues(self):
        def respond(kalshi_id, polyus_id):
            match = identical(kalshi_id, polyus_id)
            if polyus_id == 'K1P2':
                match = match.model_copy(update={'polyus_legs': ['POLYUS:NOT-IN-EVENT']})
            return ProposerResponse(matches=[match])
        write_candidates(self.candidates, [raw_result('K1', 3)])
        summary = self.go(PairClient(respond), 'r1', 1)
        rejected = self.lines('r1', 'rejected.jsonl')
        self.assertEqual(len(rejected), 1)
        self.assertEqual((rejected[0]['run_id'], rejected[0]['kind'], rejected[0]['rank']),
                         ('r1', 'invalid_response', 2))
        self.assertEqual((rejected[0]['kalshi_event_uid'], rejected[0]['polyus_event_uid']),
                         ('KALSHI:K1', 'POLYUS:K1P2'))
        self.assertTrue(rejected[0]['reason'])
        self.assertEqual((summary.pairs_sent, summary.matches, summary.rejected), (3, 2, 1))

    def test_malformed_retrieval_record_becomes_invalid_input_and_the_batch_continues(self):
        write_candidates(self.candidates, [raw_result('K1', 1), {'query': raw_event('KALSHI', 'K2')}, raw_result('K3', 1)])
        client = PairClient(identical_for_every_pair)
        summary = self.go(client, 'r1', 1)
        rejected = self.lines('r1', 'rejected.jsonl')
        self.assertEqual([(r['kind'], r['run_id'], r['kalshi_event_uid'], r['polyus_event_uid'], r['rank'])
                          for r in rejected], [('invalid_input', 'r1', 'KALSHI:K2', None, None)])
        self.assertTrue(rejected[0]['reason'])
        self.assertEqual(client.pairs, [('K1', 'K1P1'), ('K3', 'K3P1')])
        self.assertEqual((summary.kalshi_events, summary.rejected), (2, 1))


class ConcurrencyTests(RunnerCase):
    def test_pairs_run_concurrently_up_to_the_limit(self):
        write_candidates(self.candidates, [raw_result('K1', 3), raw_result('K2', 3)])
        client = SlowClient(yields=5)
        summary = self.go(client, 'r1', 3)
        self.assertEqual(client.max_in_flight, 3)
        self.assertEqual(summary.pairs_sent, 6)

    def test_concurrency_one_processes_pairs_in_file_order(self):
        write_candidates(self.candidates, [raw_result('K1', 2), raw_result('K2', 2), raw_result('K3', 1)])
        client = PairClient(identical_for_every_pair)
        self.go(client, 'r1', 1)
        self.assertEqual(client.pairs, [('K1', 'K1P1'), ('K1', 'K1P2'), ('K2', 'K2P1'), ('K2', 'K2P2'), ('K3', 'K3P1')])

    def test_normal_run_writes_t2_rows_inactive_with_rules_hashes(self):
        write_candidates(self.candidates, [raw_result('K1', 1)])
        self.go(PairClient(identical_for_every_pair), 'r1', 1)
        (row,) = collector.flags.load_flags()
        self.assertEqual((row['pair_id'], row['source'], row['is_active']),
                         ('KALSHI:K1-A__POLYUS:K1P1-A', 'proposer', False))
        self.assertEqual((row['kalshi_rules_hash'], row['polyus_rules_hash']), ('hash-K1', 'hash-K1P1'))

    def test_producer_blocks_while_the_queue_is_full(self):
        write_candidates(self.candidates, [raw_result(f'K{i}', 1) for i in range(1, 41)])
        read = []
        original = runner.adapter.iter_retrieval_results

        def counting(path):
            for record in original(path):
                read.append(record)
                yield record

        client = SlowClient(yields=20)
        backlog = []
        client.on_call = lambda: backlog.append(len(read) - len(backlog) - 1)
        with mock.patch.object(runner.adapter, 'iter_retrieval_results', counting):
            self.go(client, 'r1', 2)
        self.assertEqual(len(read), 40)
        self.assertLessEqual(max(backlog), 4)
        self.assertLessEqual(client.max_in_flight, 2)


class Fatal(Exception):
    """Stands in for a non-retryable API failure such as an authentication error."""


class T2FailureTests(RunnerCase):
    def test_t2_failure_during_an_abort_is_logged_and_the_original_error_propagates(self):
        write_candidates(self.candidates, [raw_result('K1', 1), raw_result('K2', 1)])
        client = PairClient(lambda k, p: Fatal('authentication failed') if k == 'K2' else identical_for_every_pair(k, p))
        with mock.patch.object(runner, 'write_to_t2', side_effect=OSError('disk full')), \
                self.assertLogs('proposer.runner', level='ERROR') as logs, \
                self.assertRaises(Fatal):
            asyncio.run(asyncio.wait_for(run(client, self.candidates, self.proposals, 'r1', 1), timeout=5))
        (record,) = logs.records
        self.assertEqual((record.run_id, record.error_type), ('r1', 'OSError'))  # pyright: ignore[reportAttributeAccessIssue]
        self.assertNotIn('disk full', record.getMessage())
        self.assertIsNotNone(record.exc_info)
        self.assertIsInstance((record.exc_info or (None, None, None))[1], OSError)

    def test_t2_failure_on_a_normal_run_raises(self):
        write_candidates(self.candidates, [raw_result('K1', 1)])
        with mock.patch.object(runner, 'write_to_t2', side_effect=OSError('disk full')), \
                self.assertRaisesRegex(OSError, 'disk full'):
            self.go(PairClient(identical_for_every_pair), 'r1', 1)


class StreamFailureTests(RunnerCase):
    def test_iterator_failure_mid_stream_propagates_and_t2_holds_every_stored_match_of_the_run(self):
        write_candidates(self.candidates, [raw_result('K1', 2), raw_result('K2', 1)])
        original = runner.adapter.iter_retrieval_results

        def failing_after_first(path):
            yield next(original(path))
            raise RuntimeError('stream broke')

        client = PairClient(identical_for_every_pair)
        with mock.patch.object(runner.adapter, 'iter_retrieval_results', failing_after_first), \
                self.assertRaisesRegex(RuntimeError, 'stream broke'):
            asyncio.run(asyncio.wait_for(run(client, self.candidates, self.proposals, 'r1', 2), timeout=5))
        with MatchStore(self.proposals / 'matches.sqlite3') as store:
            stored = {f'{m.kalshi_legs[0]}__{m.polyus_legs[0]}' for m in store.matches_for_run('r1')}
        self.assertEqual({row['pair_id'] for row in collector.flags.load_flags()}, stored)
        self.assertLessEqual(set(client.pairs), {('K1', 'K1P1'), ('K1', 'K1P2')})


class FatalErrorTests(RunnerCase):
    def run_with_fatal_on_k2(self):
        write_candidates(self.candidates, [raw_result('K1', 3), raw_result('K2', 1), raw_result('K3', 1)])
        client = PairClient(lambda k, p: Fatal('authentication failed') if k == 'K2' else identical_for_every_pair(k, p))
        with self.assertRaises(Fatal):
            asyncio.run(asyncio.wait_for(run(client, self.candidates, self.proposals, 'r1', 1), timeout=5))
        return client

    def test_fatal_error_reraises_the_original_and_stops_sending(self):
        client = self.run_with_fatal_on_k2()
        self.assertEqual(client.pairs, [('K1', 'K1P1'), ('K1', 'K1P2'), ('K1', 'K1P3'), ('K2', 'K2P1')])

    def test_fatal_error_still_writes_t2_for_matches_already_stored(self):
        self.run_with_fatal_on_k2()
        pairs = {row['pair_id'] for row in collector.flags.load_flags()}
        self.assertEqual(pairs, {f'KALSHI:K1-A__POLYUS:K1P{i}-A' for i in (1, 2, 3)})


class JsonLineFormatterTests(unittest.TestCase):
    def make_record(self, exc_info):
        record = logging.LogRecord('proposer.x', logging.ERROR, __file__, 1, 'api call failed', None, exc_info)
        record.error_type = 'RateLimitError'
        record.pair_id = 'A__B'
        return record

    def test_each_record_is_one_json_line_with_standard_fields_and_extras(self):
        line = cli.JsonLineFormatter().format(self.make_record(None))
        self.assertNotIn('\n', line)
        data = json.loads(line)
        self.assertEqual((data['level'], data['logger'], data['message']), ('ERROR', 'proposer.x', 'api call failed'))
        self.assertEqual((data['error_type'], data['pair_id']), ('RateLimitError', 'A__B'))
        self.assertTrue(data['time'])
        self.assertNotIn('exception', data)

    def test_exception_text_is_included_when_present(self):
        try:
            raise OSError('disk full')
        except OSError:
            record = self.make_record(sys.exc_info())
        line = cli.JsonLineFormatter().format(record)
        self.assertNotIn('\n', line)
        self.assertIn('disk full', json.loads(line)['exception'])


class CliTests(unittest.TestCase):
    def test_missing_or_empty_key_exits_naming_the_variable_and_runs_nothing(self):
        for key in (None, ''):
            with self.subTest(key=key), mock.patch.dict(os.environ), \
                    mock.patch.object(cli, 'run', mock.AsyncMock()) as fake_run:
                os.environ.pop('OPENAI_API_KEY', None)
                if key is not None:
                    os.environ['OPENAI_API_KEY'] = key
                with self.assertRaises(SystemExit) as caught:
                    cli.main(['--candidates', 'x.json'])
                self.assertIn('OPENAI_API_KEY', str(caught.exception.code))
                fake_run.assert_not_called()

    def test_wires_the_openai_client_config_and_arguments_into_run(self):
        summary = runner.RunSummary(kalshi_events=1, pairs_sent=2, matches=3, rejected=4)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
                mock.patch.object(cli, 'AsyncOpenAI') as fake_openai, \
                mock.patch.object(cli, 'run', mock.AsyncMock(return_value=summary)) as fake_run, \
                contextlib.redirect_stdout(out):
            cli.main(['--candidates', 'x.json', '--concurrency', '3'])
        fake_openai.assert_called_once_with(
            api_key='test-key', max_retries=config.API_MAX_RETRIES, timeout=config.API_TIMEOUT_S)
        (client, candidates, proposals, run_id, concurrency), _ = fake_run.call_args
        self.assertIsInstance(client, OpenAIProposerClient)
        self.assertEqual((candidates, proposals, concurrency), (Path('x.json'), config.PROPOSALS_DIR, 3))
        self.assertLess(abs(datetime.now(UTC) - datetime.strptime(run_id, '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)),
                        timedelta(minutes=1))
        self.assertIn('pairs_sent=2', out.getvalue())
        self.assertNotIn('test-key', out.getvalue())

    def test_openai_client_is_entered_for_the_run_and_closed_afterwards(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
                mock.patch.object(cli, 'AsyncOpenAI') as fake_openai, \
                mock.patch.object(cli, 'run', mock.AsyncMock(
                    return_value=runner.RunSummary(0, 0, 0, 0))) as fake_run, \
                contextlib.redirect_stdout(io.StringIO()):
            instance = fake_openai.return_value
            instance.__aenter__.return_value = instance
            instance.__aexit__.side_effect = lambda *exc: self.assertEqual(fake_run.await_count, 1)
            cli.main(['--candidates', 'x.json'])
        instance.__aenter__.assert_awaited_once()
        instance.__aexit__.assert_awaited_once()
        self.assertIs(fake_run.call_args.args[0]._client, instance)

    def test_defaults_are_the_upstream_candidates_file_and_configured_concurrency(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key'}), \
                mock.patch.object(cli, 'AsyncOpenAI'), \
                mock.patch.object(cli, 'run', mock.AsyncMock(
                    return_value=runner.RunSummary(0, 0, 0, 0))) as fake_run, \
                contextlib.redirect_stdout(io.StringIO()):
            cli.main([])
        (_, candidates, _, _, concurrency), _ = fake_run.call_args
        self.assertEqual(candidates, collector.config.ROOT / 'data' / 'embeddings' / 'candidates.json')
        self.assertEqual(concurrency, config.CONCURRENCY)


if __name__ == '__main__':
    unittest.main()
