"""Acceptance tests, task 4: runner end to end with a fake ProposerClient, plus the CLI key check.

Derived from D1, D7, D12, D14, D16, D18, D20, R3, R8, R9, R10, R11 and the runner/CLI Interfaces in PLAN.md.
No network, no real key, only temporary directories; collector.flags.FLAGS_PATH and
proposer.config.PROPOSALS_DIR are redirected so real data/ files are never touched.
"""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock

import collector.flags
from proposer import config
from proposer.llm import ProposerCallError
from proposer.response import Explanation, LegCondition, ProposedMatch, ProposerResponse, Relationship
from proposer.runner import run
from proposer.store import MatchStore

SHA = 'b' * 64
REVISION = 'a' * 40
SCORES = {'KE1': [0.9, 0.8, 0.7, 0.6, 0.5], 'KE2': [0.95, 0.4], 'KE3': [0.3]}
TOP3_PAIRS = {('KE1', 'KE1P1'), ('KE1', 'KE1P2'), ('KE1', 'KE1P3'),
              ('KE2', 'KE2P1'), ('KE2', 'KE2P2'), ('KE3', 'KE3P1')}


def raw_event(platform, event_id, market_suffixes):
    uid = f'{platform}:{event_id}'
    return {
        'platform': platform, 'event_uid': uid, 'title': f'Title {event_id}', 'category': 'Politics',
        'subcategory': None, 'tags': [], 'option_count': len(market_suffixes),
        'contracts': [{
            'market_uid': f'{uid.split(":")[0]}:{event_id}-{s}', 'platform': platform, 'event_uid': uid,
            'title': f'Title {event_id}', 'category': 'Politics', 'subcategory': None, 'tags': [],
            'market_title': f'market {event_id}-{s}', 'yes_means': f'yes {event_id}-{s}', 'summary': 'SUMMARY',
            'rules': f'rules {event_id}-{s}', 'close_time_ms': 1767225600000.0,
            'rules_hash': f'hash-{platform}:{event_id}-{s}', 'status': 'open',
        } for s in market_suffixes],
    }


def good_results():
    results = []
    for kalshi_id, scores in SCORES.items():
        candidates = [{'event': raw_event('POLYUS', f'{kalshi_id}P{i + 1}', ['A', 'B']), 'score': score}
                      for i, score in enumerate(scores)]
        results.append({'query': raw_event('KALSHI', kalshi_id, ['A', 'B']), 'candidates': candidates,
                        'ranked_candidate_ids': [c['event']['event_uid'] for c in candidates],
                        'excluded_candidate_ids': []})
    return results


def write_candidates(path, results):
    metadata = {'model': 'google/embeddinggemma-300m', 'revision': 'main', 'resolved_revision': REVISION,
                'representation': 'title', 'mapping_sha256': SHA, 'candidate_ids': [], 'profile_issues': {},
                'top_only': False}
    text = '{"metadata":' + json.dumps(metadata) + ',"results":[' + ','.join(json.dumps(r) for r in results) + ']}'
    Path(path).write_text(text, encoding='utf-8')


def explanation(uids):
    return Explanation(yes_conditions=[LegCondition(market_uid=u, yes_condition=f'{u} yes') for u in uids],
                       assumptions=[], rule_differences=[], summary='why')


def identical(kalshi_id, polyus_id):
    k, p = f'KALSHI:{kalshi_id}-A', f'POLYUS:{polyus_id}-A'
    return ProposedMatch(relationship=Relationship('identical'), kalshi_legs=[k], polyus_legs=[p],
                         explanation=explanation([k, p]))


def basket(kalshi_id, polyus_id):
    k, pa, pb = f'KALSHI:{kalshi_id}-A', f'POLYUS:{polyus_id}-A', f'POLYUS:{polyus_id}-B'
    return ProposedMatch(relationship=Relationship('basket'), kalshi_legs=[k], polyus_legs=[pa, pb],
                         explanation=explanation([k, pa, pb]))


def invalid(kalshi_id, polyus_id):
    return ProposerResponse(matches=[ProposedMatch(
        relationship=Relationship('identical'), kalshi_legs=[f'KALSHI:{kalshi_id}-A'],
        polyus_legs=['POLYUS:NOT-IN-EVENT'],
        explanation=explanation([f'KALSHI:{kalshi_id}-A', 'POLYUS:NOT-IN-EVENT']))])


def default_behaviour(kalshi_id, polyus_id):
    matches = [identical(kalshi_id, polyus_id)]
    if polyus_id == 'KE1P1':
        matches.append(basket(kalshi_id, polyus_id))
    return ProposerResponse(matches=matches)


class Fatal(Exception):
    """Stands in for a non-retryable API failure such as an authentication error."""


class FakeProposerClient:
    """ProposerClient fake that identifies the pair from the event data in the messages."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.pairs = []
        self.texts = []
        self.in_flight = 0
        self.max_in_flight = 0

    def identify(self, text):
        kalshi_id = next(k for k in SCORES if f'KALSHI:{k}-A' in text)
        polyus_id = next(f'{k}P{i}' for k, scores in SCORES.items() for i in range(1, len(scores) + 1)
                         if f'POLYUS:{k}P{i}-A' in text)
        return kalshi_id, polyus_id

    async def propose(self, messages):
        text = '\n'.join(m['content'] for m in messages)
        pair = self.identify(text)
        self.pairs.append(pair)
        self.texts.append(text)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            for _ in range(3):
                await asyncio.sleep(0)
        finally:
            self.in_flight -= 1
        outcome = self.behaviour(*pair)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.candidates = root / 'candidates.json'
        self.proposals = root / 'proposals'
        self.proposals.mkdir()
        write_candidates(self.candidates, good_results())
        patcher = mock.patch.object(collector.flags, 'FLAGS_PATH', root / 'flags' / 'match_flags.parquet')
        patcher.start()
        self.addCleanup(patcher.stop)

    def go(self, client, run_id, concurrency):
        return asyncio.run(run(client, self.candidates, self.proposals, run_id, concurrency))

    def jsonl(self, run_id, name):
        path = self.proposals / run_id / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def stored(self, run_id):
        with MatchStore(self.proposals / 'matches.sqlite3') as store:
            return list(store.matches_for_run(run_id))

    def t2_pairs(self):
        return {r['pair_id'] for r in collector.flags.load_flags()}


class RunnerNormalTests(RunnerTestCase):
    def test_only_top_three_candidates_are_sent_and_summary_counts(self):
        client = FakeProposerClient(default_behaviour)
        summary = self.go(client, 'run-1', 4)
        self.assertEqual(set(client.pairs), TOP3_PAIRS)
        self.assertEqual(len(client.pairs), 6)
        for text in client.texts:
            for unsent in ('KE1P4', 'KE1P5'):
                self.assertNotIn(unsent, text)
        self.assertEqual(summary.kalshi_events, 3)
        self.assertEqual(summary.pairs_sent, 6)
        self.assertEqual(summary.matches, 7)
        self.assertEqual(summary.rejected, 0)

    def test_event_data_sent_to_the_llm_excludes_scores_hashes_and_summary(self):
        client = FakeProposerClient(default_behaviour)
        self.go(client, 'run-1', 2)
        for text in client.texts:
            for forbidden in ('hash-KALSHI', 'hash-POLYUS', 'SUMMARY'):
                self.assertNotIn(forbidden, text)

    def test_one_audit_line_per_kalshi_event_with_full_top_k(self):
        self.go(FakeProposerClient(default_behaviour), 'run-1', 4)
        lines = {line['kalshi_event_uid']: line for line in self.jsonl('run-1', 'audit.jsonl')}
        self.assertEqual(len(self.jsonl('run-1', 'audit.jsonl')), 3)
        self.assertEqual(set(lines), {'KALSHI:KE1', 'KALSHI:KE2', 'KALSHI:KE3'})
        ke1 = lines['KALSHI:KE1']
        self.assertEqual(ke1['run_id'], 'run-1')
        self.assertEqual(ke1['model'], 'google/embeddinggemma-300m')
        self.assertEqual(ke1['resolved_revision'], REVISION)
        self.assertEqual(ke1['mapping_sha256'], SHA)
        self.assertEqual([(t['event_uid'], t['rank'], t['score']) for t in ke1['top_k']],
                         [(f'POLYUS:KE1P{i}', i, SCORES['KE1'][i - 1]) for i in range(1, 6)])
        self.assertEqual(ke1['sent'], ['POLYUS:KE1P1', 'POLYUS:KE1P2', 'POLYUS:KE1P3'])
        self.assertEqual(lines['KALSHI:KE2']['sent'], ['POLYUS:KE2P1', 'POLYUS:KE2P2'])
        self.assertEqual(len(lines['KALSHI:KE2']['top_k']), 2)
        self.assertEqual(lines['KALSHI:KE3']['sent'], ['POLYUS:KE3P1'])

    def test_matches_reference_run_rank_and_score(self):
        self.go(FakeProposerClient(default_behaviour), 'run-1', 4)
        records = self.stored('run-1')
        self.assertEqual(len(records), 7)
        by_pair = {(r.kalshi_event_uid, r.polyus_event_uid, r.relationship.value): r for r in records}
        second = by_pair[('KALSHI:KE1', 'POLYUS:KE1P2', 'identical')]
        self.assertEqual(second.candidate_rank, 2)
        self.assertEqual(second.candidate_score, 0.8)
        self.assertEqual(second.run_id, 'run-1')
        self.assertEqual(second.kalshi_legs, ('KALSHI:KE1-A',))
        self.assertEqual(second.polyus_legs, ('POLYUS:KE1P2-A',))
        self.assertEqual(by_pair[('KALSHI:KE3', 'POLYUS:KE3P1', 'identical')].candidate_rank, 1)

    def test_baskets_only_in_match_store_not_in_t2(self):
        self.go(FakeProposerClient(default_behaviour), 'run-1', 4)
        records = self.stored('run-1')
        self.assertEqual(sum(r.relationship.value == 'basket' for r in records), 1)
        expected = {f'KALSHI:{k}-A__POLYUS:{p}-A' for k, p in TOP3_PAIRS}
        self.assertEqual(self.t2_pairs(), expected)
        rows = {r['pair_id']: r for r in collector.flags.load_flags()}
        row = rows['KALSHI:KE1-A__POLYUS:KE1P1-A']
        self.assertIs(row['is_active'], False)
        self.assertEqual(row['source'], 'proposer')
        self.assertEqual(row['kalshi_rules_hash'], 'hash-KALSHI:KE1-A')
        self.assertEqual(row['polyus_rules_hash'], 'hash-POLYUS:KE1P1-A')

    def test_concurrency_is_bounded(self):
        client = FakeProposerClient(default_behaviour)
        self.go(client, 'run-1', 2)
        self.assertLessEqual(client.max_in_flight, 2)
        self.assertEqual(len(client.pairs), 6)

    def test_no_rejections_file_content_when_everything_is_valid(self):
        self.go(FakeProposerClient(default_behaviour), 'run-1', 4)
        self.assertEqual(self.jsonl('run-1', 'rejected.jsonl'), [])


class RunnerFailureTests(RunnerTestCase):
    def test_rejected_pair_does_not_stop_the_batch(self):
        def behaviour(kalshi_id, polyus_id):
            return invalid(kalshi_id, polyus_id) if polyus_id == 'KE1P2' else default_behaviour(kalshi_id, polyus_id)
        client = FakeProposerClient(behaviour)
        summary = self.go(client, 'run-1', 4)
        self.assertEqual(client.pairs.count(('KE1', 'KE1P2')), 2)
        self.assertEqual(len(client.pairs), 7)
        rejected = self.jsonl('run-1', 'rejected.jsonl')
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]['kind'], 'invalid_response')
        self.assertEqual(rejected[0]['kalshi_event_uid'], 'KALSHI:KE1')
        self.assertEqual(rejected[0]['polyus_event_uid'], 'POLYUS:KE1P2')
        self.assertEqual(rejected[0]['rank'], 2)
        self.assertEqual(rejected[0]['run_id'], 'run-1')
        self.assertTrue(rejected[0]['reason'])
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.pairs_sent, 6)
        self.assertEqual(len(self.stored('run-1')), 6)
        self.assertEqual(len(self.jsonl('run-1', 'audit.jsonl')), 3)

    def test_api_error_is_rejected_without_retry_and_batch_continues(self):
        def behaviour(kalshi_id, polyus_id):
            if polyus_id == 'KE2P1':
                return ProposerCallError('503 after retries')
            return default_behaviour(kalshi_id, polyus_id)
        client = FakeProposerClient(behaviour)
        self.go(client, 'run-1', 4)
        self.assertEqual(client.pairs.count(('KE2', 'KE2P1')), 1)
        rejected = self.jsonl('run-1', 'rejected.jsonl')
        self.assertEqual([r['kind'] for r in rejected], ['api_error'])
        self.assertEqual(rejected[0]['polyus_event_uid'], 'POLYUS:KE2P1')
        self.assertEqual(rejected[0]['rank'], 1)
        self.assertEqual(len(self.stored('run-1')), 6)

    def test_malformed_retrieval_record_goes_to_rejected_and_batch_continues(self):
        results = good_results()
        results.insert(1, {'candidates': []})
        write_candidates(self.candidates, results)
        client = FakeProposerClient(default_behaviour)
        self.go(client, 'run-1', 4)
        rejected = self.jsonl('run-1', 'rejected.jsonl')
        self.assertEqual([r['kind'] for r in rejected], ['invalid_input'])
        self.assertIsNone(rejected[0]['kalshi_event_uid'])
        self.assertEqual(set(client.pairs), TOP3_PAIRS)
        audited = {line['kalshi_event_uid'] for line in self.jsonl('run-1', 'audit.jsonl')}
        self.assertTrue({'KALSHI:KE1', 'KALSHI:KE2', 'KALSHI:KE3'} <= audited)

    def test_fatal_error_aborts_but_t2_receives_matches_already_stored(self):
        def behaviour(kalshi_id, polyus_id):
            if kalshi_id == 'KE2':
                return Fatal('authentication failed')
            return default_behaviour(kalshi_id, polyus_id)
        client = FakeProposerClient(behaviour)
        with self.assertRaises(Fatal):
            self.go(client, 'run-1', 1)
        self.assertNotIn(('KE3', 'KE3P1'), client.pairs)
        stored = self.stored('run-1')
        self.assertGreaterEqual(len(stored), 1)
        expected = {f'{r.kalshi_legs[0]}__{r.polyus_legs[0]}' for r in stored if r.relationship.value != 'basket'}
        self.assertTrue(expected)
        self.assertEqual(self.t2_pairs(), expected)

    def test_existing_run_directory_raises_and_nothing_is_sent(self):
        (self.proposals / 'run-1').mkdir()
        client = FakeProposerClient(default_behaviour)
        with self.assertRaises(FileExistsError):
            self.go(client, 'run-1', 4)
        self.assertEqual(client.pairs, [])


class RunnerRerunTests(RunnerTestCase):
    def test_rerun_with_new_run_id_calls_the_llm_again_and_does_not_duplicate(self):
        client = FakeProposerClient(default_behaviour)
        self.go(client, 'run-1', 4)
        t2_after_first = collector.flags.load_flags()
        self.go(client, 'run-2', 4)
        self.assertEqual(len(client.pairs), 12)
        self.assertEqual(self.stored('run-1'), [])
        second = self.stored('run-2')
        self.assertEqual(len(second), 7)
        self.assertEqual(len({r.match_id for r in second}), 7)
        rows = collector.flags.load_flags()
        self.assertEqual(len(rows), len(t2_after_first))
        self.assertEqual(len({r['pair_id'] for r in rows}), len(rows))
        self.assertEqual(len(self.jsonl('run-2', 'audit.jsonl')), 3)

    def test_rerun_with_changed_results_keeps_one_record_per_match_id(self):
        self.go(FakeProposerClient(default_behaviour), 'run-1', 4)
        self.go(FakeProposerClient(lambda k, p: ProposerResponse(matches=[identical(k, p)])), 'run-2', 4)
        self.assertEqual(len(self.stored('run-2')), 6)
        self.assertEqual(len(self.stored('run-1')), 1)


class CliTests(unittest.TestCase):
    def test_missing_api_key_gives_a_clear_error_before_any_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / 'candidates.json'
            write_candidates(candidates, good_results())
            proposals = root / 'proposals'
            stderr = io.StringIO()
            with mock.patch.dict(os.environ):
                os.environ.pop('OPENAI_API_KEY', None)
                with mock.patch.object(config, 'PROPOSALS_DIR', proposals), \
                        mock.patch.object(collector.flags, 'FLAGS_PATH', root / 'flags.parquet'), \
                        mock.patch.object(sys, 'argv', ['proposer', '--candidates', str(candidates)]), \
                        contextlib.redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_module('proposer', run_name='__main__')
            code = caught.exception.code
            self.assertNotIn(code, (0, None))
            self.assertIn('OPENAI_API_KEY', f'{code}\n{stderr.getvalue()}')
            self.assertFalse(proposals.exists() and any(proposals.iterdir()))
            self.assertFalse((root / 'flags.parquet').exists())


if __name__ == '__main__':
    unittest.main()
