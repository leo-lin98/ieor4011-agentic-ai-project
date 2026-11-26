"""Developer tests, task 1: behaviours not pinned down by the acceptance tests."""
import builtins
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from proposer.adapter import InvalidRecord, iter_retrieval_results, read_metadata
from proposer.contract import Event, Market, RetrievalResult
from proposer.response import (
    Explanation,
    LegCondition,
    ProposedMatch,
    ProposerResponse,
    Relationship,
)
from proposer.validate import find_failures

META = {'model': 'm', 'resolved_revision': 'r', 'mapping_sha256': 's'}


def raw_event(platform, native):
    uid = f'{platform}:{native}'
    market = {'market_uid': f'{uid}-M', 'market_title': 't'}
    return {'event_uid': uid, 'title': 'T', 'category': 'C', 'contracts': [market]}


def make_market(uid, close_time_ms):
    return Market(market_uid=uid, market_title=None, yes_means=None, rules=None,
                  close_time_ms=close_time_ms, rules_hash=None)


class ContractDevTests(unittest.TestCase):
    def test_close_time_accepts_integral_float_and_rejects_fractional(self):
        self.assertEqual(make_market('KALSHI:M', 1793920247000.0).close_time_ms, 1793920247000)
        with self.assertRaises(ValueError):
            make_market('KALSHI:M', 1793920247000.5)

    def test_event_uid_needs_known_platform_prefix(self):
        with self.assertRaises(ValueError):
            Event(event_uid='OTHER:E', title=None, category=None, markets=(make_market('OTHER:M', None),))


class AdapterDevTests(unittest.TestCase):
    def test_non_dict_record_is_invalid_and_stream_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            meta = {'model': 'm', 'resolved_revision': 'r', 'mapping_sha256': 's'}
            path.write_text('{"metadata":' + json.dumps(meta) + ',"results":[5]}', encoding='utf-8')
            items = list(iter_retrieval_results(path))
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], InvalidRecord)
        self.assertIsNone(items[0].kalshi_event_uid)

    def write(self, tmp, metadata, results):
        path = Path(tmp) / 'c.json'
        path.write_text('{"metadata":' + json.dumps(metadata) + ',"results":[' +
                        ','.join(json.dumps(r) for r in results) + ']}', encoding='utf-8')
        return path

    def test_duplicate_candidate_event_uid_in_metadata_raises(self):
        meta = dict(META, top_only=True, candidate_events=[raw_event('POLYUS', 'P1'), raw_event('POLYUS', 'P1')])
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, meta, [])
            with self.assertRaisesRegex(ValueError, 'POLYUS:P1'):
                list(iter_retrieval_results(path))

    def test_read_metadata_closes_the_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, META, [])
            opened = []
            real_open = builtins.open

            def tracking_open(*args, **kwargs):
                handle = real_open(*args, **kwargs)
                opened.append(handle)
                return handle

            with mock.patch('builtins.open', tracking_open):
                read_metadata(path)
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].closed)

    def test_malformed_records_are_invalid_and_stream_continues(self):
        good = {'query': raw_event('KALSHI', 'GOOD'), 'candidates': []}
        kalshi_ok = raw_event('KALSHI', 'K1')
        cases = {
            'query uid not a str': ({'query': dict(raw_event('KALSHI', 'K1'), event_uid=5), 'candidates': []}, None),
            'candidate without event or uid': (
                {'query': kalshi_ok, 'candidates': [{'score': 0.5}]}, 'KALSHI:K1'),
            'candidate not a dict': ({'query': kalshi_ok, 'candidates': [5]}, 'KALSHI:K1'),
            'candidate is a string': ({'query': kalshi_ok, 'candidates': ['event']}, 'KALSHI:K1'),
            'contracts not a list': (
                {'query': dict(kalshi_ok, contracts='abc'), 'candidates': []}, 'KALSHI:K1'),
            'contracts is a dict': (
                {'query': dict(kalshi_ok, contracts={'a': 1}), 'candidates': []}, 'KALSHI:K1'),
            'candidates None': ({'query': kalshi_ok, 'candidates': None}, 'KALSHI:K1'),
            'candidates a dict': ({'query': kalshi_ok, 'candidates': {'a': 1}}, 'KALSHI:K1'),
            'candidates an empty dict': ({'query': kalshi_ok, 'candidates': {}}, 'KALSHI:K1'),
        }
        for name, (record, uid) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                items = list(iter_retrieval_results(self.write(tmp, META, [record, good])))
                self.assertEqual(len(items), 2)
                self.assertIsInstance(items[0], InvalidRecord)
                self.assertEqual(items[0].kalshi_event_uid, uid)
                self.assertIsInstance(items[1], RetrievalResult)

    def test_non_dict_metadata_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            path.write_text('{"metadata":[1],"results":[]}', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_metadata(path)


def match_with(relationship, kalshi, polyus, conditions):
    return ProposedMatch(
        relationship=Relationship(relationship), kalshi_legs=kalshi, polyus_legs=polyus,
        explanation=Explanation(
            yes_conditions=[LegCondition(market_uid=u, yes_condition='c') for u in conditions],
            assumptions=[], rule_differences=[], summary='s'))


class ValidateDevTests(unittest.TestCase):
    def test_messages_name_match_index_zero_based(self):
        k = Event(event_uid='KALSHI:E', title=None, category=None, markets=(make_market('KALSHI:K1', None),))
        p = Event(event_uid='POLYUS:E', title=None, category=None, markets=(make_market('POLYUS:P1', None),))
        response = ProposerResponse(matches=[
            match_with('identical', ['KALSHI:K1'], ['POLYUS:P1'], ['KALSHI:K1', 'POLYUS:P1']),
            match_with('identical', [], [], []),
        ])
        messages = find_failures(response, k, p)
        self.assertTrue(messages)
        self.assertTrue(all(m.startswith('match 1:') for m in messages))


if __name__ == '__main__':
    unittest.main()
