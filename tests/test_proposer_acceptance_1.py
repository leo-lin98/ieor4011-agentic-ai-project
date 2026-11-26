"""Acceptance tests, task 1: contract, adapter, response models, match_id, validation.

Derived from match-proposer-design.md (D3-D8, D10, D15, D16, D18, D19) and PLAN.md
(R4-R7, R11, R13, Interfaces). Expectations come from the specification, never from
observed implementation behaviour.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from proposer.adapter import InvalidRecord, iter_retrieval_results, read_metadata
from proposer.contract import Candidate, Event, Market, RetrievalMetadata, RetrievalResult
from proposer.response import (
    Explanation,
    LegCondition,
    ProposedMatch,
    ProposerResponse,
    Relationship,
    match_id,
)
from proposer.validate import find_failures

HASH_K = 'a' * 40
SHA = 'b' * 64


# ---------------------------------------------------------------- raw upstream fixtures
def raw_market(platform, event_uid, native, close_time_ms):
    """One upstream profile row, shaped like embeddings.prepare.build_events output."""
    return {
        'market_uid': f'{platform}:{native}', 'platform': platform, 'event_uid': event_uid,
        'title': 'Event title', 'category': 'Politics', 'subcategory': None, 'tags': [],
        'market_title': f'title {native}', 'yes_means': f'yes means {native}',
        'summary': 'SUMMARY-CANARY', 'rules': f'rules {native}', 'close_time_ms': close_time_ms,
        'rules_hash': f'hash-{native}', 'status': 'open',
    }


def raw_event(platform, native_event, natives, close_time_ms):
    """One upstream event dict with its markets under the upstream key `contracts`."""
    uid = f'{platform}:{native_event}'
    return {
        'platform': platform, 'event_uid': uid, 'title': f'Title {native_event}',
        'category': 'Politics', 'subcategory': None, 'tags': [], 'option_count': len(natives),
        'contracts': [raw_market(platform, uid, n, close_time_ms) for n in natives],
    }


def metadata_dict(top_only):
    return {
        'model': 'google/embeddinggemma-300m', 'revision': 'main', 'resolved_revision': HASH_K,
        'representation': 'title', 'mapping_sha256': SHA, 'candidate_ids': [],
        'profile_issues': {}, 'top_only': top_only,
    }


def write_stream(path, metadata, results):
    """Write the exact streamed layout produced by embeddings/embed.py."""
    text = '{"metadata":' + json.dumps(metadata) + ',"results":[' + ','.join(json.dumps(r) for r in results) + ']}'
    Path(path).write_text(text, encoding='utf-8')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'candidates.json'

    def test_read_metadata_normal_and_top_only(self):
        for top_only in (False, True):
            with self.subTest(top_only=top_only):
                write_stream(self.path, metadata_dict(top_only), [])
                meta = read_metadata(self.path)
                self.assertIsInstance(meta, RetrievalMetadata)
                self.assertEqual(meta.model, 'google/embeddinggemma-300m')
                self.assertEqual(meta.resolved_revision, HASH_K)
                self.assertEqual(meta.mapping_sha256, SHA)

    def test_normal_shape_with_float_close_time(self):
        kalshi = raw_event('KALSHI', 'EV1', ['EV1-A', 'EV1-B'], 1767225600000.0)
        p1 = raw_event('POLYUS', 'P1', ['p1a'], 1767225600000.0)
        p2 = raw_event('POLYUS', 'P2', ['p2a', 'p2b'], None)
        result = {'query': kalshi, 'candidates': [{'event': p1, 'score': 0.9}, {'event': p2, 'score': 0.5}],
                  'ranked_candidate_ids': ['POLYUS:P1', 'POLYUS:P2'], 'excluded_candidate_ids': []}
        write_stream(self.path, metadata_dict(False), [result])
        items = list(iter_retrieval_results(self.path))
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIsInstance(item, RetrievalResult)
        self.assertEqual(item.kalshi_event.event_uid, 'KALSHI:EV1')
        self.assertEqual(item.kalshi_event.title, 'Title EV1')
        self.assertEqual(item.kalshi_event.category, 'Politics')
        self.assertEqual([m.market_uid for m in item.kalshi_event.markets], ['KALSHI:EV1-A', 'KALSHI:EV1-B'])
        close = item.kalshi_event.markets[0].close_time_ms
        self.assertIsInstance(close, int)
        self.assertEqual(close, 1767225600000)
        self.assertEqual([c.rank for c in item.candidates], [1, 2])
        self.assertEqual([c.score for c in item.candidates], [0.9, 0.5])
        self.assertIsInstance(item.candidates[0], Candidate)
        self.assertEqual(item.candidates[1].event.event_uid, 'POLYUS:P2')
        self.assertIsNone(item.candidates[1].event.markets[0].close_time_ms)
        first = item.kalshi_event.markets[0]
        self.assertEqual(first.market_title, 'title EV1-A')
        self.assertEqual(first.yes_means, 'yes means EV1-A')
        self.assertEqual(first.rules, 'rules EV1-A')
        self.assertEqual(first.rules_hash, 'hash-EV1-A')

    def test_upstream_contracts_key_is_renamed_and_extra_columns_dropped(self):
        kalshi = raw_event('KALSHI', 'EV1', ['EV1-A'], 1)
        write_stream(self.path, metadata_dict(False), [{'query': kalshi, 'candidates': []}])
        item = list(iter_retrieval_results(self.path))[0]
        self.assertFalse(hasattr(item.kalshi_event, 'contracts'))
        self.assertFalse(hasattr(item.kalshi_event.markets[0], 'summary'))
        self.assertEqual(item.candidates, ())

    def test_top_only_shape_resolves_events_from_metadata(self):
        kalshi = raw_event('KALSHI', 'EV1', ['EV1-A'], 5)
        events = [raw_event('POLYUS', f'P{i}', [f'p{i}a'], 5) for i in (1, 2, 3)]
        meta = metadata_dict(True)
        meta['candidate_events'] = events
        result = {'query': kalshi, 'candidates': [{'event_uid': 'POLYUS:P3', 'score': 0.8},
                                                  {'event_uid': 'POLYUS:P1', 'score': 0.4}]}
        write_stream(self.path, meta, [result])
        item = list(iter_retrieval_results(self.path))[0]
        self.assertIsInstance(item, RetrievalResult)
        self.assertEqual([(c.rank, c.event.event_uid, c.score) for c in item.candidates],
                         [(1, 'POLYUS:P3', 0.8), (2, 'POLYUS:P1', 0.4)])
        self.assertEqual(item.candidates[0].event.markets[0].market_uid, 'POLYUS:p3a')

    def test_top_only_unknown_candidate_is_invalid_record(self):
        kalshi = raw_event('KALSHI', 'EV1', ['EV1-A'], 5)
        meta = metadata_dict(True)
        meta['candidate_events'] = [raw_event('POLYUS', 'P1', ['p1a'], 5)]
        result = {'query': kalshi, 'candidates': [{'event_uid': 'POLYUS:NOPE', 'score': 0.8}]}
        write_stream(self.path, meta, [result])
        item = list(iter_retrieval_results(self.path))[0]
        self.assertIsInstance(item, InvalidRecord)
        self.assertEqual(item.kalshi_event_uid, 'KALSHI:EV1')

    def test_malformed_record_yields_invalid_record_and_stream_continues(self):
        good_a = {'query': raw_event('KALSHI', 'A', ['a1'], 1), 'candidates': []}
        no_query = {'candidates': []}
        empty_markets = {'query': raw_event('KALSHI', 'BAD', [], 1), 'candidates': []}
        dup_candidate = raw_event('POLYUS', 'P1', ['x', 'x'], 1)
        bad_candidate = {'query': raw_event('KALSHI', 'BADC', ['b1'], 1),
                         'candidates': [{'event': dup_candidate, 'score': 0.5}]}
        missing_uid = raw_event('KALSHI', 'BADU', ['u1'], 1)
        del missing_uid['contracts'][0]['market_uid']
        no_market_uid = {'query': missing_uid, 'candidates': []}
        good_b = {'query': raw_event('KALSHI', 'B', ['b1'], 1), 'candidates': []}
        write_stream(self.path, metadata_dict(False),
                     [good_a, no_query, empty_markets, bad_candidate, no_market_uid, good_b])
        items = list(iter_retrieval_results(self.path))
        self.assertEqual(len(items), 6)
        self.assertIsInstance(items[0], RetrievalResult)
        self.assertIsInstance(items[5], RetrievalResult)
        self.assertEqual(items[5].kalshi_event.event_uid, 'KALSHI:B')
        for item in items[1:5]:
            self.assertIsInstance(item, InvalidRecord)
            self.assertIsInstance(item.reason, str)
            self.assertTrue(item.reason)
        self.assertIsNone(items[1].kalshi_event_uid)
        self.assertEqual(items[2].kalshi_event_uid, 'KALSHI:BAD')
        self.assertEqual(items[3].kalshi_event_uid, 'KALSHI:BADC')

    def test_malformed_metadata_raises_value_error(self):
        broken = metadata_dict(False)
        del broken['mapping_sha256']
        write_stream(self.path, broken, [])
        with self.assertRaises(ValueError):
            read_metadata(self.path)
        with self.assertRaises(ValueError):
            list(iter_retrieval_results(self.path))
        wrong_type = metadata_dict(False)
        wrong_type['resolved_revision'] = None
        write_stream(self.path, wrong_type, [])
        with self.assertRaises(ValueError):
            read_metadata(self.path)
        self.path.write_text('{"not":"streamed"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            read_metadata(self.path)

    def test_results_are_streamed_lazily(self):
        write_stream(self.path, metadata_dict(False), [{'query': raw_event('KALSHI', 'A', ['a'], 1), 'candidates': []}])
        iterator = iter_retrieval_results(self.path)
        self.assertIs(iter(iterator), iterator)


# ---------------------------------------------------------------- contract models
def market(uid):
    return Market(market_uid=uid, market_title='t', yes_means='y', rules='r', close_time_ms=1, rules_hash='h')


def event(uid, market_uids):
    return Event(event_uid=uid, title='T', category='C', markets=tuple(market(u) for u in market_uids))


class ContractTests(unittest.TestCase):
    def test_valid_models_and_optional_fields(self):
        bare = Market(market_uid='KALSHI:M', market_title=None, yes_means=None, rules=None,
                      close_time_ms=None, rules_hash=None)
        ev = Event(event_uid='KALSHI:E', title=None, category=None, markets=(bare,))
        result = RetrievalResult(kalshi_event=ev, candidates=())
        self.assertEqual(result.candidates, ())

    def test_models_are_frozen_and_forbid_extras(self):
        m = market('KALSHI:M')
        with self.assertRaises(ValueError):
            m.rules = 'changed'
        with self.assertRaises(ValueError):
            Market(market_uid='KALSHI:M', market_title='t', yes_means='y', rules='r', close_time_ms=1,
                   rules_hash='h', summary='x')

    def test_event_invariants(self):
        with self.assertRaises(ValueError):
            Event(event_uid='KALSHI:E', title='T', category='C', markets=())
        with self.assertRaises(ValueError):
            event('KALSHI:E', ['KALSHI:M', 'KALSHI:M'])
        with self.assertRaises(ValueError):
            event('KALSHI:E', ['POLYUS:M'])
        with self.assertRaises(ValueError):
            event('POLYUS:E', ['KALSHI:M'])

    def test_candidate_invariants(self):
        ev = event('POLYUS:E', ['POLYUS:M'])
        Candidate(rank=1, score=0.5, event=ev)
        for rank in (0, -1):
            with self.assertRaises(ValueError):
                Candidate(rank=rank, score=0.5, event=ev)
        for score in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                Candidate(rank=1, score=score, event=ev)

    def test_retrieval_result_platform_and_rank_invariants(self):
        k = event('KALSHI:E', ['KALSHI:M'])
        p1 = Candidate(rank=1, score=0.9, event=event('POLYUS:A', ['POLYUS:A1']))
        p2 = Candidate(rank=2, score=0.8, event=event('POLYUS:B', ['POLYUS:B1']))
        p3 = Candidate(rank=3, score=0.7, event=event('POLYUS:C', ['POLYUS:C1']))
        RetrievalResult(kalshi_event=k, candidates=(p1, p2, p3))
        with self.assertRaises(ValueError):
            RetrievalResult(kalshi_event=k, candidates=(p2, p1))
        with self.assertRaises(ValueError):
            RetrievalResult(kalshi_event=k, candidates=(p1, p3))
        with self.assertRaises(ValueError):
            RetrievalResult(kalshi_event=event('POLYUS:A', ['POLYUS:A1']), candidates=())
        wrong_side = Candidate(rank=1, score=0.9, event=event('KALSHI:X', ['KALSHI:X1']))
        with self.assertRaises(ValueError):
            RetrievalResult(kalshi_event=k, candidates=(wrong_side,))


# ---------------------------------------------------------------- response models, match_id
def explanation(uids):
    return Explanation(
        yes_conditions=[LegCondition(market_uid=u, yes_condition=f'{u} pays YES') for u in uids],
        assumptions=[], rule_differences=[], summary='summary')


def proposed(relationship, kalshi, polyus, conditions=None):
    return ProposedMatch(
        relationship=Relationship(relationship), kalshi_legs=list(kalshi), polyus_legs=list(polyus),
        explanation=explanation(list(kalshi) + list(polyus) if conditions is None else conditions))


def walk_objects(schema, node):
    """Yield every JSON-schema node that describes an object."""
    if isinstance(node, dict):
        if 'properties' in node:
            yield node
        for value in node.values():
            yield from walk_objects(schema, value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_objects(schema, value)


class ResponseModelTests(unittest.TestCase):
    def test_relationship_values(self):
        self.assertEqual({r.value for r in Relationship}, {'identical', 'complementary', 'basket'})

    def test_schema_is_strict_compatible(self):
        schema = ProposerResponse.model_json_schema()
        objects = list(walk_objects(schema, schema))
        self.assertGreaterEqual(len(objects), 4)
        for obj in objects:
            self.assertIs(obj.get('additionalProperties'), False)
            self.assertEqual(sorted(obj.get('required', [])), sorted(obj['properties']))

    def test_none_is_not_a_relationship_and_extras_rejected(self):
        with self.assertRaises(ValueError):
            Relationship('none')
        payload = {'matches': [{'relationship': 'none', 'kalshi_legs': [], 'polyus_legs': [],
                                'explanation': {'yes_conditions': [], 'assumptions': [],
                                                'rule_differences': [], 'summary': 's'}}]}
        with self.assertRaises(ValueError):
            ProposerResponse.model_validate(payload)
        with self.assertRaises(ValueError):
            ProposerResponse.model_validate({'matches': [], 'extra': 1})
        with self.assertRaises(ValueError):
            ProposerResponse.model_validate({})

    def test_empty_matches_is_a_valid_response(self):
        self.assertEqual(ProposerResponse.model_validate({'matches': []}).matches, [])

    def test_response_round_trips_through_json(self):
        original = ProposerResponse(matches=[proposed('identical', ['KALSHI:K1'], ['POLYUS:P1'])])
        self.assertEqual(ProposerResponse.model_validate_json(original.model_dump_json()), original)

    def test_match_id_formula(self):
        expected = hashlib.sha256('identical|KALSHI:A,KALSHI:B|POLYUS:X'.encode('utf-8')).hexdigest()
        self.assertEqual(match_id(Relationship('identical'), ['KALSHI:B', 'KALSHI:A'], ['POLYUS:X']), expected)
        expected_basket = hashlib.sha256('basket|KALSHI:A|POLYUS:X,POLYUS:Y'.encode('utf-8')).hexdigest()
        self.assertEqual(match_id(Relationship('basket'), ['KALSHI:A'], ['POLYUS:Y', 'POLYUS:X']), expected_basket)

    def test_match_id_identity_properties(self):
        base = match_id(Relationship('identical'), ['KALSHI:A'], ['POLYUS:X'])
        self.assertEqual(base, match_id(Relationship('identical'), ['KALSHI:A'], ['POLYUS:X']))
        self.assertNotEqual(base, match_id(Relationship('complementary'), ['KALSHI:A'], ['POLYUS:X']))
        self.assertNotEqual(base, match_id(Relationship('identical'), ['KALSHI:B'], ['POLYUS:X']))
        self.assertNotEqual(base, match_id(Relationship('identical'), ['KALSHI:A'], ['POLYUS:Y']))
        kalshi_basket = match_id(Relationship('basket'), ['KALSHI:A', 'KALSHI:B'], ['POLYUS:X'])
        polyus_basket = match_id(Relationship('basket'), ['KALSHI:A'], ['POLYUS:X', 'POLYUS:Y'])
        self.assertNotEqual(kalshi_basket, polyus_basket)
        self.assertEqual(len(base), 64)

    def test_match_id_does_not_mutate_inputs(self):
        legs = ['KALSHI:B', 'KALSHI:A']
        match_id(Relationship('basket'), legs, ['POLYUS:X'])
        self.assertEqual(legs, ['KALSHI:B', 'KALSHI:A'])


# ---------------------------------------------------------------- validation (D16, R6, R7, D18)
K = event('KALSHI:EV', ['KALSHI:K1', 'KALSHI:K2', 'KALSHI:K3'])
P = event('POLYUS:EV', ['POLYUS:P1', 'POLYUS:P2', 'POLYUS:P3'])


def failures(*matches):
    return find_failures(ProposerResponse(matches=list(matches)), K, P)


class ValidationTests(unittest.TestCase):
    def test_empty_response_is_valid(self):
        self.assertEqual(failures(), [])

    def test_identical_and_complementary_one_leg_each(self):
        for relationship in ('identical', 'complementary'):
            with self.subTest(relationship=relationship):
                self.assertEqual(failures(proposed(relationship, ['KALSHI:K1'], ['POLYUS:P2'])), [])

    def test_valid_baskets_in_both_directions(self):
        self.assertEqual(failures(proposed('basket', ['KALSHI:K1'], ['POLYUS:P1', 'POLYUS:P2'])), [])
        self.assertEqual(failures(proposed('basket', ['KALSHI:K1', 'KALSHI:K2'], ['POLYUS:P1'])), [])
        self.assertEqual(failures(proposed('basket', ['KALSHI:K1', 'KALSHI:K2', 'KALSHI:K3'], ['POLYUS:P3'])), [])

    def test_overlapping_matches_are_allowed(self):
        response = [
            proposed('identical', ['KALSHI:K1'], ['POLYUS:P1']),
            proposed('complementary', ['KALSHI:K1'], ['POLYUS:P1']),
            proposed('identical', ['KALSHI:K1'], ['POLYUS:P2']),
            proposed('basket', ['KALSHI:K2', 'KALSHI:K3'], ['POLYUS:P1']),
        ]
        self.assertEqual(failures(*response), [])

    def test_unknown_market_uid_is_named(self):
        for kalshi, polyus, bad in (
            (['KALSHI:GHOST'], ['POLYUS:P1'], 'KALSHI:GHOST'),
            (['KALSHI:K1'], ['POLYUS:GHOST'], 'POLYUS:GHOST'),
        ):
            with self.subTest(bad=bad):
                result = failures(proposed('identical', kalshi, polyus))
                self.assertTrue(result)
                self.assertTrue(any(bad in message for message in result))

    def test_market_on_the_wrong_side_is_invalid(self):
        swapped = failures(proposed('identical', ['POLYUS:P1'], ['KALSHI:K1']))
        self.assertTrue(swapped)
        self.assertTrue(any('POLYUS:P1' in m for m in swapped))
        self.assertTrue(failures(proposed('identical', ['KALSHI:K1'], ['KALSHI:K2'])))
        self.assertTrue(failures(proposed('identical', ['POLYUS:P1'], ['POLYUS:P2'])))

    def test_market_on_both_sides_is_invalid(self):
        result = failures(proposed('identical', ['KALSHI:K1'], ['KALSHI:K1']))
        self.assertTrue(result)
        self.assertTrue(any('KALSHI:K1' in m for m in result))
        self.assertTrue(failures(proposed('basket', ['KALSHI:K1'], ['KALSHI:K1', 'POLYUS:P1'])))

    def test_identical_and_complementary_need_exactly_one_leg_per_side(self):
        for relationship in ('identical', 'complementary'):
            for kalshi, polyus in (
                (['KALSHI:K1', 'KALSHI:K2'], ['POLYUS:P1']),
                (['KALSHI:K1'], ['POLYUS:P1', 'POLYUS:P2']),
                (['KALSHI:K1', 'KALSHI:K2'], ['POLYUS:P1', 'POLYUS:P2']),
                ([], ['POLYUS:P1']),
                (['KALSHI:K1'], []),
                ([], []),
            ):
                with self.subTest(relationship=relationship, kalshi=kalshi, polyus=polyus):
                    self.assertTrue(failures(proposed(relationship, kalshi, polyus)))

    def test_basket_needs_one_leg_on_one_side_and_two_or_more_on_the_other(self):
        for kalshi, polyus in (
            (['KALSHI:K1'], ['POLYUS:P1']),
            (['KALSHI:K1', 'KALSHI:K2'], ['POLYUS:P1', 'POLYUS:P2']),
            (['KALSHI:K1'], []),
            ([], ['POLYUS:P1', 'POLYUS:P2']),
            ([], []),
        ):
            with self.subTest(kalshi=kalshi, polyus=polyus):
                self.assertTrue(failures(proposed('basket', kalshi, polyus)))

    def test_duplicate_legs_within_one_side_are_invalid(self):
        self.assertTrue(failures(proposed('basket', ['KALSHI:K1'], ['POLYUS:P1', 'POLYUS:P1'],
                                          conditions=['KALSHI:K1', 'POLYUS:P1'])))
        self.assertTrue(failures(proposed('basket', ['KALSHI:K1', 'KALSHI:K1'], ['POLYUS:P1'],
                                          conditions=['KALSHI:K1', 'POLYUS:P1'])))

    def test_yes_conditions_must_equal_leg_set_exactly_once_each(self):
        legs = (['KALSHI:K1'], ['POLYUS:P1', 'POLYUS:P2'])
        missing = failures(proposed('basket', *legs, conditions=['KALSHI:K1', 'POLYUS:P1']))
        self.assertTrue(missing)
        self.assertTrue(any('POLYUS:P2' in m for m in missing))
        extra = failures(proposed('basket', *legs, conditions=['KALSHI:K1', 'POLYUS:P1', 'POLYUS:P2', 'POLYUS:P3']))
        self.assertTrue(extra)
        self.assertTrue(any('POLYUS:P3' in m for m in extra))
        duplicated = failures(proposed('basket', *legs, conditions=['KALSHI:K1', 'POLYUS:P1', 'POLYUS:P1', 'POLYUS:P2']))
        self.assertTrue(duplicated)
        self.assertTrue(any('POLYUS:P1' in m for m in duplicated))
        self.assertTrue(failures(proposed('basket', *legs, conditions=[])))
        self.assertTrue(failures(proposed('identical', ['KALSHI:K1'], ['POLYUS:P1'],
                                          conditions=['KALSHI:K2', 'POLYUS:P1'])))

    def test_one_invalid_match_invalidates_the_response_and_is_located(self):
        good = proposed('identical', ['KALSHI:K1'], ['POLYUS:P1'])
        bad = proposed('identical', ['KALSHI:K2'], ['POLYUS:ZZZ'])
        result = failures(good, bad)
        self.assertTrue(result)
        self.assertTrue(any('POLYUS:ZZZ' in m for m in result))

    def test_failure_without_a_single_uid_names_the_match_index(self):
        good = proposed('identical', ['KALSHI:K1'], ['POLYUS:P1'])
        bad = proposed('identical', [], [])
        result = failures(good, bad)
        self.assertTrue(result)
        self.assertTrue(any('1' in m for m in result))

    def test_every_invalid_match_is_reported(self):
        bad_a = proposed('identical', ['KALSHI:GHOST'], ['POLYUS:P1'])
        bad_b = proposed('identical', ['KALSHI:K1'], ['POLYUS:GHOST2'])
        result = failures(bad_a, bad_b)
        self.assertGreaterEqual(len(result), 2)
        self.assertTrue(any('KALSHI:GHOST' in m for m in result))
        self.assertTrue(any('GHOST2' in m for m in result))

    def test_find_failures_does_not_mutate_inputs(self):
        response = ProposerResponse(matches=[proposed('basket', ['KALSHI:K1'], ['POLYUS:P1', 'POLYUS:P2'])])
        before = response.model_dump_json()
        find_failures(response, K, P)
        self.assertEqual(response.model_dump_json(), before)


if __name__ == '__main__':
    unittest.main()
