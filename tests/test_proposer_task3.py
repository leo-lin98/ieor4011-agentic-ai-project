"""Developer tests, task 3: behaviours beyond the QA acceptance tests."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from proposer.audit import AuditRecord, RejectedRecord, append_record
from proposer.propose import RejectionKind
from proposer.response import Explanation, LegCondition, Relationship, match_id
from proposer.store import MatchRecord, MatchStore
from proposer.t2 import merge_proposals


def make_record(kalshi, polyus, run_id, proposed_ms):
    """Build a MatchRecord from possibly unsorted legs."""
    return MatchRecord(
        match_id=match_id(Relationship.basket, kalshi, polyus),
        relationship=Relationship.basket, kalshi_legs=kalshi, polyus_legs=polyus,
        explanation=Explanation(
            yes_conditions=[LegCondition(market_uid=u, yes_condition='y') for u in kalshi + polyus],
            assumptions=[], rule_differences=[], summary='s'),
        run_id=run_id, kalshi_event_uid='KALSHI:EV', polyus_event_uid='POLYUS:EV',
        candidate_rank=1, candidate_score=0.5, rules_hashes={}, proposed_ms=proposed_ms)


class MatchRecordTests(unittest.TestCase):
    def test_legs_are_sorted_tuples(self):
        record = make_record(['KALSHI:B', 'KALSHI:A'], ['POLYUS:Z', 'POLYUS:Y'], 'run-1', 1)
        self.assertEqual(record.kalshi_legs, ('KALSHI:A', 'KALSHI:B'))
        self.assertEqual(record.polyus_legs, ('POLYUS:Y', 'POLYUS:Z'))


class AuditRecordTests(unittest.TestCase):
    def test_audit_record_coerces_top_k_dicts_and_is_frozen(self):
        record = AuditRecord(
            run_id='r', kalshi_event_uid='KALSHI:E', model='m', resolved_revision='rev', mapping_sha256='sha',
            top_k=[{'event_uid': 'POLYUS:A', 'rank': 1, 'score': 0.5}], sent=['POLYUS:A'])
        self.assertEqual(record.top_k[0].event_uid, 'POLYUS:A')
        with self.assertRaises(ValidationError):
            record.run_id = 'other'

    def test_rejected_record_coerces_kind_string(self):
        record = RejectedRecord(run_id='r', kalshi_event_uid=None, polyus_event_uid=None, rank=None,
                                kind='api_error', reason='x')
        self.assertIs(record.kind, RejectionKind.api_error)

    def test_rejected_record_rejects_unknown_kind(self):
        with self.assertRaises(ValidationError):
            RejectedRecord(run_id='r', kalshi_event_uid=None, polyus_event_uid=None, rank=None,
                           kind='bogus', reason='x')

    def test_append_record_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = RejectedRecord(run_id='r', kalshi_event_uid=None, polyus_event_uid=None, rank=None,
                                    kind='invalid_input', reason='x')
            with self.assertRaises(FileNotFoundError):
                append_record(Path(tmp) / 'missing' / 'rejected.jsonl', record)

    def test_append_record_writes_compact_line_with_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rejected.jsonl'
            append_record(path, RejectedRecord(run_id='r', kalshi_event_uid=None, polyus_event_uid=None,
                                               rank=None, kind='invalid_input', reason='x'))
            self.assertEqual(
                path.read_text(encoding='utf-8'),
                '{"run_id":"r","kalshi_event_uid":null,"polyus_event_uid":null,"rank":null,'
                '"kind":"invalid_input","reason":"x"}\n')


class MergeTests(unittest.TestCase):
    def test_conflict_warning_carries_structured_fields(self):
        identical = make_record(['KALSHI:A'], ['POLYUS:Y'], 'run-1', 1)
        complementary = identical.model_copy(update={'relationship': Relationship.complementary})
        identical = identical.model_copy(update={'relationship': Relationship.identical})
        with self.assertLogs(level='WARNING') as logs:
            merge_proposals([], [identical, complementary], 5)
        self.assertEqual(logs.records[0].pair_id, 'KALSHI:A__POLYUS:Y')
        self.assertNotIn('KALSHI:A__POLYUS:Y', logs.records[0].getMessage())
        self.assertEqual(logs.records[0].run_ids, ['run-1'])

    def test_polyus_hash_change_resets_while_null_kalshi_hash_keeps_old(self):
        row = {
            'pair_id': 'KALSHI:A__POLYUS:Y', 'kalshi_market_uid': 'KALSHI:A', 'polyus_market_uid': 'POLYUS:Y',
            'market_identical': True, 'relationship': 'identical', 'proposer_explanation': 'old',
            'judge_verdict': 'pass', 'numeric_checks_passed': True, 'rules_review_status': 'ok',
            'human_review_status': 'approved', 'kalshi_rules_hash': 'old-k', 'polyus_rules_hash': 'old-p',
            'flagged_ts': 1, 'updated_ts': 1, 'is_active': True, 'source': 'proposer', 'note': None,
        }
        match = make_record(['KALSHI:A'], ['POLYUS:Y'], 'run-1', 1).model_copy(
            update={'relationship': Relationship.identical, 'rules_hashes': {'POLYUS:Y': 'new-p'}})
        merged = merge_proposals([row], [match], 5)[0]
        self.assertEqual(merged['kalshi_rules_hash'], 'old-k')
        self.assertEqual(merged['polyus_rules_hash'], 'new-p')
        self.assertIs(merged['is_active'], False)
        for field in ('judge_verdict', 'numeric_checks_passed', 'rules_review_status', 'human_review_status'):
            self.assertIsNone(merged[field])

    def test_proposer_explanation_is_explanation_json(self):
        match = make_record(['KALSHI:A'], ['POLYUS:Y'], 'run-1', 1)
        match = match.model_copy(update={'relationship': Relationship.identical})
        row = merge_proposals([], [match], 5)[0]
        self.assertEqual(Explanation.model_validate_json(row['proposer_explanation']), match.explanation)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_failed_table_creation_closes_connection_and_reraises(self):
        connection = mock.MagicMock()
        connection.execute.side_effect = sqlite3.OperationalError('boom')
        with (
            mock.patch('proposer.store.sqlite3.connect', return_value=connection),
            self.assertRaises(sqlite3.OperationalError),
        ):
            MatchStore(Path(self.tmp.name) / 'matches.sqlite3')
        connection.close.assert_called_once_with()

    def test_creates_missing_parent_directory(self):
        path = Path(self.tmp.name) / 'nested' / 'deeper' / 'matches.sqlite3'
        with MatchStore(path) as store:
            store.upsert(make_record(['KALSHI:A'], ['POLYUS:Y'], 'run-1', 1))
        self.assertTrue(path.exists())

    def test_matches_for_run_is_lazy(self):
        path = Path(self.tmp.name) / 'matches.sqlite3'
        with MatchStore(path) as store:
            store.upsert(make_record(['KALSHI:A'], ['POLYUS:Y'], 'run-1', 1))
            self.assertNotIsInstance(store.matches_for_run('run-1'), list)
