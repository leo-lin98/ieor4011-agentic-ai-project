"""Acceptance tests, task 3: SQLite match store, audit/rejected JSONL, T2 merge.

Derived from D7, D11, D12, D16, D18, D19, R1-R4, R9 and DECISIONS.md T2 (collector.schemas.MATCH_FLAGS).
Only temporary directories are used; collector.flags.FLAGS_PATH is redirected for write_to_t2.
"""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import pyarrow as pa

import collector.flags
from collector.schemas import MATCH_FLAGS
from proposer.audit import AuditRecord, RejectedRecord, append_record
from proposer.response import Explanation, LegCondition, Relationship, match_id
from proposer.store import MatchRecord, MatchStore
from proposer.t2 import merge_proposals, write_to_t2

K1, K2, K3 = 'KALSHI:K1', 'KALSHI:K2', 'KALSHI:K3'
P1, P2, P3 = 'POLYUS:P1', 'POLYUS:P2', 'POLYUS:P3'


def record(relationship, kalshi, polyus, run_id, rank, score, proposed_ms, hashes, summary):
    """Build a MatchRecord; legs are passed already sorted."""
    uids = list(kalshi) + list(polyus)
    return MatchRecord(
        match_id=match_id(Relationship(relationship), list(kalshi), list(polyus)),
        relationship=Relationship(relationship), kalshi_legs=tuple(kalshi), polyus_legs=tuple(polyus),
        explanation=Explanation(
            yes_conditions=[LegCondition(market_uid=u, yes_condition=f'{u} yes') for u in uids],
            assumptions=['assume'], rule_differences=['differ'], summary=summary),
        run_id=run_id, kalshi_event_uid='KALSHI:EV', polyus_event_uid='POLYUS:EV',
        candidate_rank=rank, candidate_score=score, rules_hashes=hashes, proposed_ms=proposed_ms)


def simple(relationship, kalshi, polyus):
    hashes = {u: f'hash-{u}' for u in list(kalshi) + list(polyus)}
    return record(relationship, kalshi, polyus, 'run-1', 1, 0.9, 1000, hashes, 'explained')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'matches.sqlite3'

    def rows_by_match_id(self):
        """Read raw rows through sqlite3 from every table that has a match_id column."""
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            found = []
            names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                columns = [r[1] for r in con.execute(f'PRAGMA table_info("{name}")')]
                if 'match_id' in columns:
                    found.extend(dict(r) for r in con.execute(f'SELECT * FROM "{name}"'))
            return found
        finally:
            con.close()

    def test_round_trip_by_run(self):
        rec = record('basket', [K1], [P1, P2], 'run-1', 2, 0.75, 1000, {K1: 'hk', P1: 'hp', P2: None}, 'basket summary')
        with MatchStore(self.path) as store:
            store.upsert(rec)
            self.assertEqual(list(store.matches_for_run('run-1')), [rec])
            self.assertEqual(list(store.matches_for_run('other-run')), [])

    def test_upsert_keeps_one_row_per_match_id_with_latest_values(self):
        first = record('identical', [K1], [P1], 'run-1', 1, 0.9, 1000, {K1: 'a', P1: 'b'}, 'old summary')
        second = record('identical', [K1], [P1], 'run-2', 3, 0.4, 5000, {K1: 'a', P1: 'b'}, 'new summary')
        self.assertEqual(first.match_id, second.match_id)
        with MatchStore(self.path) as store:
            store.upsert(first)
            store.upsert(second)
            self.assertEqual(list(store.matches_for_run('run-1')), [])
            self.assertEqual(list(store.matches_for_run('run-2')), [second])
        rows = self.rows_by_match_id()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['first_proposed_ms'], 1000)
        self.assertEqual(rows[0]['updated_ms'], 5000)

    def test_same_run_repeated_upsert_does_not_duplicate(self):
        rec = simple('identical', [K1], [P1])
        with MatchStore(self.path) as store:
            store.upsert(rec)
            store.upsert(rec)
            self.assertEqual(len(list(store.matches_for_run('run-1'))), 1)
        self.assertEqual(len(self.rows_by_match_id()), 1)

    def test_overlapping_matches_are_all_stored(self):
        records = [
            simple('identical', [K1], [P1]),
            simple('complementary', [K1], [P1]),
            simple('identical', [K1], [P2]),
            simple('basket', [K2, K3], [P1]),
        ]
        self.assertEqual(len({r.match_id for r in records}), 4)
        with MatchStore(self.path) as store:
            for rec in records:
                store.upsert(rec)
            self.assertEqual({r.match_id for r in store.matches_for_run('run-1')}, {r.match_id for r in records})

    def test_each_upsert_is_committed_immediately(self):
        rec = simple('identical', [K1], [P1])
        with MatchStore(self.path) as store:
            store.upsert(rec)
            self.assertEqual(len(self.rows_by_match_id()), 1)

    def test_data_survives_reopening(self):
        rec = simple('identical', [K1], [P1])
        with MatchStore(self.path) as store:
            store.upsert(rec)
        with MatchStore(self.path) as store:
            self.assertEqual(list(store.matches_for_run('run-1')), [rec])


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def read_lines(self, path):
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]

    def test_audit_records_append_one_json_line_each(self):
        path = self.dir / 'audit.jsonl'
        for index in (1, 2):
            append_record(path, AuditRecord(
                run_id='run-1', kalshi_event_uid=f'KALSHI:E{index}', model='m', resolved_revision='r' * 40,
                mapping_sha256='s' * 64,
                top_k=[{'event_uid': 'POLYUS:A', 'rank': 1, 'score': 0.9}, {'event_uid': 'POLYUS:B', 'rank': 2, 'score': 0.5}],
                sent=['POLYUS:A', 'POLYUS:B']))
        lines = self.read_lines(path)
        self.assertEqual(len(lines), 2)
        self.assertEqual([line['kalshi_event_uid'] for line in lines], ['KALSHI:E1', 'KALSHI:E2'])
        first = lines[0]
        self.assertEqual(first['run_id'], 'run-1')
        self.assertEqual(first['model'], 'm')
        self.assertEqual(first['resolved_revision'], 'r' * 40)
        self.assertEqual(first['mapping_sha256'], 's' * 64)
        self.assertEqual(first['top_k'][1], {'event_uid': 'POLYUS:B', 'rank': 2, 'score': 0.5})
        self.assertEqual(first['sent'], ['POLYUS:A', 'POLYUS:B'])

    def test_rejected_records_append_and_keep_earlier_lines(self):
        path = self.dir / 'rejected.jsonl'
        append_record(path, RejectedRecord(run_id='run-1', kalshi_event_uid='KALSHI:E1', polyus_event_uid='POLYUS:A',
                                           rank=2, kind='invalid_response', reason='bad uid POLYUS:ZZZ'))
        append_record(path, RejectedRecord(run_id='run-1', kalshi_event_uid=None, polyus_event_uid=None,
                                           rank=None, kind='invalid_input', reason='no query'))
        lines = self.read_lines(path)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['kind'], 'invalid_response')
        self.assertEqual(lines[0]['rank'], 2)
        self.assertEqual(lines[0]['polyus_event_uid'], 'POLYUS:A')
        self.assertEqual(lines[0]['reason'], 'bad uid POLYUS:ZZZ')
        self.assertIsNone(lines[1]['kalshi_event_uid'])
        self.assertEqual(lines[1]['kind'], 'invalid_input')

    def test_api_error_kind_is_serialised(self):
        path = self.dir / 'rejected.jsonl'
        append_record(path, RejectedRecord(run_id='r', kalshi_event_uid='KALSHI:E', polyus_event_uid='POLYUS:A',
                                           rank=1, kind='api_error', reason='503'))
        self.assertEqual(self.read_lines(path)[0]['kind'], 'api_error')


# ---------------------------------------------------------------- T2 merge
def existing_row(pair_id, k_uid, p_uid, **overrides):
    row = {
        'pair_id': pair_id, 'kalshi_market_uid': k_uid, 'polyus_market_uid': p_uid,
        'market_identical': True, 'relationship': 'identical', 'proposer_explanation': 'old text',
        'judge_verdict': 'pass', 'numeric_checks_passed': True, 'rules_review_status': 'ok',
        'human_review_status': 'approved', 'kalshi_rules_hash': 'hk', 'polyus_rules_hash': 'hp',
        'flagged_ts': 100, 'updated_ts': 100, 'is_active': True, 'source': 'proposer', 'note': 'keep me',
    }
    row.update(overrides)
    return row


PAIR = f'{K1}__{P1}'
NOW = 9000
REVIEW_FIELDS = ('judge_verdict', 'numeric_checks_passed', 'rules_review_status', 'human_review_status')


def match_for_pair(relationship, hk, hp, summary):
    return record(relationship, [K1], [P1], 'run-9', 1, 0.8, 8000, {K1: hk, P1: hp}, summary)


def only(rows, pair_id):
    found = [r for r in rows if r['pair_id'] == pair_id]
    assert len(found) == 1, f'expected exactly one row for {pair_id}, got {len(found)}'
    return found[0]


class MergeNewRowTests(unittest.TestCase):
    def test_new_identical_row(self):
        rows = merge_proposals([], [match_for_pair('identical', 'hk', 'hp', 'why identical')], NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['pair_id'], PAIR)
        self.assertEqual(row['kalshi_market_uid'], K1)
        self.assertEqual(row['polyus_market_uid'], P1)
        self.assertEqual(row['relationship'], 'identical')
        self.assertIs(row['market_identical'], True)
        self.assertIs(row['is_active'], False)
        self.assertEqual(row['source'], 'proposer')
        self.assertIsNone(row['note'])
        for field in REVIEW_FIELDS:
            self.assertIsNone(row[field])
        self.assertEqual(row['kalshi_rules_hash'], 'hk')
        self.assertEqual(row['polyus_rules_hash'], 'hp')
        self.assertEqual(row['flagged_ts'], NOW)
        self.assertEqual(row['updated_ts'], NOW)
        self.assertIsInstance(row['proposer_explanation'], str)
        self.assertIn('why identical', row['proposer_explanation'])

    def test_new_complementary_row_is_not_market_identical(self):
        row = merge_proposals([], [match_for_pair('complementary', 'hk', 'hp', 's')], NOW)[0]
        self.assertEqual(row['relationship'], 'complementary')
        self.assertIs(row['market_identical'], False)
        self.assertIs(row['is_active'], False)

    def test_rows_conform_to_match_flags_schema(self):
        rows = merge_proposals([], [match_for_pair('identical', None, 'hp', 's')], NOW)
        for row in rows:
            self.assertEqual(set(row), set(MATCH_FLAGS.names))
        table = pa.Table.from_pylist(rows, schema=MATCH_FLAGS)
        self.assertEqual(table.num_rows, 1)

    def test_baskets_are_excluded(self):
        basket = record('basket', [K1], [P1, P2], 'run-9', 1, 0.8, 8000, {K1: 'a', P1: 'b', P2: 'c'}, 's')
        identical = simple('identical', [K2], [P3])
        rows = merge_proposals([], [basket, identical], NOW)
        self.assertEqual([r['pair_id'] for r in rows], [f'{K2}__{P3}'])
        self.assertEqual(merge_proposals([], [basket], NOW), [])

    def test_accepts_any_iterable_of_matches(self):
        generator = (m for m in [simple('identical', [K1], [P1]), simple('identical', [K2], [P2])])
        self.assertEqual(len(merge_proposals([], generator, NOW)), 2)

    def test_inputs_are_not_mutated(self):
        existing = [existing_row(PAIR, K1, P1), existing_row('x__y', 'KALSHI:X', 'POLYUS:Y', source='manual')]
        before = copy.deepcopy(existing)
        matches = [match_for_pair('complementary', 'new', 'new2', 's')]
        merge_proposals(existing, matches, NOW)
        self.assertEqual(existing, before)

    def test_unrelated_existing_rows_are_preserved(self):
        other = existing_row('KALSHI:X__POLYUS:Y', 'KALSHI:X', 'POLYUS:Y', source='manual')
        rows = merge_proposals([other], [simple('identical', [K1], [P1])], NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(only(rows, other['pair_id']), other)


class MergeExistingRowTests(unittest.TestCase):
    def merge_one(self, existing, match):
        rows = merge_proposals([existing], [match], NOW)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def assert_reviews_kept(self, row, base):
        for field in REVIEW_FIELDS:
            self.assertEqual(row[field], base[field])
        self.assertIs(row['is_active'], True)

    def assert_reset(self, row):
        for field in REVIEW_FIELDS:
            self.assertIsNone(row[field])
        self.assertIs(row['is_active'], False)

    def test_non_proposer_rows_are_untouched(self):
        for source in ('manual', 'other'):
            with self.subTest(source=source):
                base = existing_row(PAIR, K1, P1, source=source, relationship='complementary',
                                    market_identical=False, kalshi_rules_hash='old')
                row = self.merge_one(base, match_for_pair('identical', 'new', 'new', 'changed'))
                self.assertEqual(row, base)

    def test_unchanged_relationship_and_hashes_update_content_but_keep_reviews(self):
        base = existing_row(PAIR, K1, P1)
        row = self.merge_one(base, match_for_pair('identical', 'hk', 'hp', 'fresh explanation'))
        self.assertIn('fresh explanation', row['proposer_explanation'])
        self.assertEqual(row['updated_ts'], NOW)
        self.assertEqual(row['flagged_ts'], 100)
        self.assertEqual(row['note'], 'keep me')
        self.assertEqual(row['source'], 'proposer')
        self.assertIs(row['market_identical'], True)
        self.assert_reviews_kept(row, base)

    def test_relationship_change_resets_reviews_and_deactivates(self):
        base = existing_row(PAIR, K1, P1)
        row = self.merge_one(base, match_for_pair('complementary', 'hk', 'hp', 'now complementary'))
        self.assertEqual(row['relationship'], 'complementary')
        self.assertIs(row['market_identical'], False)
        self.assert_reset(row)
        self.assertEqual(row['updated_ts'], NOW)
        self.assertEqual(row['flagged_ts'], 100)
        self.assertEqual(row['note'], 'keep me')
        self.assertIn('now complementary', row['proposer_explanation'])

    def test_relationship_change_back_to_identical_sets_market_identical(self):
        base = existing_row(PAIR, K1, P1, relationship='complementary', market_identical=False)
        row = self.merge_one(base, match_for_pair('identical', 'hk', 'hp', 's'))
        self.assertIs(row['market_identical'], True)
        self.assert_reset(row)

    def test_changed_hash_resets_reviews_and_updates_hash(self):
        for side, key in (('kalshi', 'kalshi_rules_hash'), ('polyus', 'polyus_rules_hash')):
            with self.subTest(side=side):
                base = existing_row(PAIR, K1, P1)
                hk, hp = ('new', 'hp') if side == 'kalshi' else ('hk', 'new')
                row = self.merge_one(base, match_for_pair('identical', hk, hp, 's'))
                self.assertEqual(row[key], 'new')
                self.assert_reset(row)
                self.assertEqual(row['relationship'], 'identical')
                self.assertEqual(row['flagged_ts'], 100)
                self.assertEqual(row['note'], 'keep me')

    def test_null_new_hash_never_overwrites_and_is_not_a_change(self):
        base = existing_row(PAIR, K1, P1)
        row = self.merge_one(base, match_for_pair('identical', None, None, 's'))
        self.assertEqual(row['kalshi_rules_hash'], 'hk')
        self.assertEqual(row['polyus_rules_hash'], 'hp')
        self.assert_reviews_kept(row, base)

    def test_null_old_hash_is_filled_without_counting_as_change(self):
        base = existing_row(PAIR, K1, P1, kalshi_rules_hash=None, polyus_rules_hash=None)
        row = self.merge_one(base, match_for_pair('identical', 'new-k', 'new-p', 's'))
        self.assertEqual(row['kalshi_rules_hash'], 'new-k')
        self.assertEqual(row['polyus_rules_hash'], 'new-p')
        self.assert_reviews_kept(row, base)

    def test_both_null_hashes_stay_null_without_reset(self):
        base = existing_row(PAIR, K1, P1, kalshi_rules_hash=None, polyus_rules_hash=None)
        row = self.merge_one(base, match_for_pair('identical', None, None, 's'))
        self.assertIsNone(row['kalshi_rules_hash'])
        self.assertIsNone(row['polyus_rules_hash'])
        self.assert_reviews_kept(row, base)

    def test_inactive_row_stays_inactive_when_unchanged(self):
        base = existing_row(PAIR, K1, P1, is_active=False)
        row = self.merge_one(base, match_for_pair('identical', 'hk', 'hp', 's'))
        self.assertIs(row['is_active'], False)

    def test_match_for_a_different_pair_adds_a_row(self):
        base = existing_row(PAIR, K1, P1)
        other = simple('identical', [K2], [P2])
        rows = merge_proposals([base], [other], NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(only(rows, PAIR), base)
        self.assertTrue(only(rows, f'{K2}__{P2}'))


class MergeConflictTests(unittest.TestCase):
    def both(self):
        return [match_for_pair('identical', 'hk', 'hp', 'ident'), match_for_pair('complementary', 'hk', 'hp', 'compl')]

    def test_identical_and_complementary_for_one_pair_are_skipped_with_warning(self):
        with self.assertLogs(level='WARNING'):
            rows = merge_proposals([], self.both(), NOW)
        self.assertEqual(rows, [])

    def test_conflicting_pair_leaves_existing_row_untouched(self):
        base = existing_row(PAIR, K1, P1)
        with self.assertLogs(level='WARNING'):
            rows = merge_proposals([base], self.both(), NOW)
        self.assertEqual(rows, [base])

    def test_conflict_does_not_block_other_pairs(self):
        other = simple('identical', [K2], [P2])
        with self.assertLogs(level='WARNING'):
            rows = merge_proposals([], self.both() + [other], NOW)
        self.assertEqual([r['pair_id'] for r in rows], [f'{K2}__{P2}'])

    def test_conflict_order_does_not_matter(self):
        with self.assertLogs(level='WARNING'):
            rows = merge_proposals([], list(reversed(self.both())), NOW)
        self.assertEqual(rows, [])


class WriteToT2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(collector.flags, 'FLAGS_PATH', Path(self.tmp.name) / 'match_flags' / 'match_flags.parquet')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_file_and_rows_are_readable(self):
        write_to_t2([simple('identical', [K1], [P1]), simple('basket', [K2], [P1, P2])], NOW)
        rows = collector.flags.load_flags()
        self.assertEqual([r['pair_id'] for r in rows], [PAIR])
        self.assertIs(rows[0]['is_active'], False)
        self.assertEqual(rows[0]['source'], 'proposer')
        self.assertEqual(rows[0]['flagged_ts'], NOW)

    def test_second_write_updates_without_duplicates_and_preserves_other_rows(self):
        manual = existing_row('KALSHI:M__POLYUS:M', 'KALSHI:M', 'POLYUS:M', source='manual')
        collector.flags.save_flags([manual])
        write_to_t2([simple('identical', [K1], [P1])], NOW)
        write_to_t2([simple('identical', [K1], [P1])], NOW + 1)
        rows = collector.flags.load_flags()
        self.assertEqual(len(rows), 2)
        self.assertEqual(only(rows, manual['pair_id']), manual)
        self.assertEqual(only(rows, PAIR)['updated_ts'], NOW + 1)


if __name__ == '__main__':
    unittest.main()
