"""Regression coverage for tier floors, exact excerpts, and human scoring."""
import tempfile
import unittest
from pathlib import Path

from judge import tier, tier_rules


def record(**fields):
    documents = {key: f"Written rule for {key}." for key in fields}
    raw = {
        key: {"value": fields.get(key), "quotes": (
            [{"document_id": key, "quote": documents[key]}] if key in fields else []
        )}
        for key in tier_rules.FIELD_SCHEMAS
    }
    return documents, raw


def extraction(**fields):
    documents, raw = record(**fields)
    return tier_rules.validate_extraction(raw, documents)


def cite_spans(raw, payload):
    by_document = {span["document_id"]: identifier for identifier, span in payload["evidence_spans"].items()}
    return {key: {"value": field["value"],
                  "span_ids": [by_document[quote["document_id"]] for quote in field["quotes"]]}
            for key, field in raw.items()}


class TierJudgeTests(unittest.TestCase):
    def test_selected_span_recovers_verbatim_punctuation(self):
        documents = {"rule": "First clause. If above $59,999.99, it resolves YES.\nNo rounding."}
        spans = tier_rules.evidence_spans(documents)
        quote = tier_rules._resolve_spans({"span_ids": ["s2"]}, spans)["quotes"]
        self.assertEqual(quote, [{"document_id": "rule", "quote": "If above $59,999.99, it resolves YES."}])
        with self.assertRaisesRegex(ValueError, "valid evidence span IDs"):
            tier_rules._resolve_spans({"span_ids": ["invented"]}, spans)

    def test_station_alias_and_explicit_time_comparison(self):
        left = extraction(source_instrument="CLINYC", observation={
            "kind": "point_in_time", "time_utc": "2026-11-27T17:00:00-05:00",
        })
        right = extraction(source_instrument="Central Park", observation={
            "kind": "point_in_time", "time_utc": "2026-11-27T22:00:00Z",
        })
        self.assertEqual(tier_rules.compare(left, right), [])
        right["source_instrument"]["value"] = "KLGA"
        right["observation"]["value"]["time_utc"] = "2026-11-27T17:00:00Z"
        findings = tier_rules.compare(left, right)
        self.assertEqual({item["check"]: item["tier"] for item in findings},
                         {"source_instrument": "Blocking", "observation": "Blocking"})

    def test_threshold_operator_and_complement_are_handled(self):
        left = extraction(threshold={"op": ">", "value": "3.5000", "unit": "$"})
        right = extraction(threshold={"op": ">=", "value": "3.50", "unit": "USD"})
        self.assertEqual(tier_rules.compare(left, right)[0]["tier"], "Blocking")
        right["threshold"]["value"]["op"] = "<="
        self.assertEqual(tier_rules.compare(left, right, "opposite"), [])

    def test_shared_half_tie_stays_equivalent_and_different_fallback_is_minor(self):
        half = [{"trigger": "tie", "payout": "half"}]
        left = extraction(fallbacks=half)
        right = extraction(fallbacks=half)
        self.assertEqual(tier_rules.compare(left, right), [])
        right = extraction(fallbacks=[{"trigger": "tie", "payout": "fair_price"}])
        self.assertEqual(tier_rules.compare(left, right)[0]["tier"], "Minor")

    def test_bucket_aggregation_and_snapshot_checks(self):
        left = extraction(
            observation={"kind": "snapshot", "time_utc": "2026-11-27T17:00:00Z"},
            aggregation="trimmed_mean",
            bucket={"low": "60", "high": "70", "unit": "USD", "low_inclusive": True, "high_inclusive": False},
        )
        right = extraction(
            observation={"kind": "snapshot", "time_utc": "2026-11-27T18:00:00Z"},
            aggregation="any_high",
            bucket={"low": "60", "high": "80", "unit": "USD", "low_inclusive": True, "high_inclusive": False},
        )
        self.assertEqual({item["check"]: item["tier"] for item in tier_rules.compare(left, right)},
                         {"aggregation": "Material", "observation": "Material", "bucket": "Blocking"})

    def test_independent_extractions_are_cached_and_semantics_cannot_downgrade(self):
        documents, raw = record(source_entity="CF Benchmarks")
        left = {"side": "Yes", "market_title": "First contract", "documents": documents}
        right = {"side": "Yes", "market_title": "Second contract", "documents": documents}
        other_documents, other_raw = record(source_entity="Binance")
        other_documents["source_entity"] = "Written rule for Binance."
        other_raw["source_entity"]["quotes"][0]["quote"] = other_documents["source_entity"]
        right["documents"] = other_documents
        extraction_calls = []

        def call(model, system, payload, schema):
            if system == tier_rules.EXTRACT_SYSTEM:
                self.assertEqual(set(payload), {"evidence_spans", "selected_side", "market_title"})
                extraction_calls.append(payload["market_title"])
                selected = raw if payload["market_title"] == left["market_title"] else other_raw
                return cite_spans(selected, payload)
            self.assertEqual(payload["code_tier"], "Blocking")
            return {"tier": "Minor", "outcome_equivalence": "equivalent", "source_equivalence": "unclear",
                    "explanation": "The two sources may cover correlated prices.", "scenarios": []}

        with tempfile.TemporaryDirectory() as temp:
            options = {"direction": "same", "model": "mock", "cache": Path(temp) / "cache.sqlite3", "call": call}
            first = tier_rules.judge_pair(left, right, **options)
            second = tier_rules.judge_pair(left, right, **options)
        self.assertEqual(extraction_calls, ["First contract", "Second contract"])
        self.assertEqual(first["tier"], "Blocking")
        self.assertEqual(second["tier"], "Blocking")
        self.assertTrue(first["needs_human"])

    def test_semantic_can_raise_tier_with_quoted_payouts(self):
        documents, raw = record(outcome_definition="Wins an award")
        left = {"side": "Yes", "documents": documents}
        right = {"side": "Yes", "documents": documents}

        def call(model, system, payload, schema):
            if system == tier_rules.EXTRACT_SYSTEM:
                return cite_spans(raw, payload)
            return {"tier": "Material", "outcome_equivalence": "different", "source_equivalence": "equivalent",
                    "explanation": "Different award triggers.", "scenarios": [
                        {"facts": "The award is withdrawn after an initial public announcement.",
                         "kalshi_payout": {"amount_per_dollar": "1", "reasoning": "The initial award is binding.", "span_ids": ["s1"]},
                         "polymarket_payout": {"amount_per_dollar": "0", "reasoning": "The final award controls.", "span_ids": ["s1"]}},
                    ]}

        with tempfile.TemporaryDirectory() as temp:
            result = tier_rules.judge_pair(
                left, right, direction="same", model="mock", cache=Path(temp) / "cache.sqlite3", call=call,
            )
        self.assertEqual(result["code_tier"], "Equivalent")
        self.assertEqual(result["tier"], "Material")
        self.assertEqual(result["scenarios"][0]["evidence_status"], "confirmed")

    def test_scores_human_tiers_and_counts_missing_positive(self):
        report = tier.score_rows(
            {"1": "Material", "2": "Blocking", "3": "Minor", "4": "Blocking"},
            [{"pair_id": "1", "tier": "Minor", "findings": [{"check": "tie_rule"}]},
             {"pair_id": "2", "tier": "Material"}, {"pair_id": "3", "tier": "Equivalent"}],
        )
        self.assertEqual(report["confusion_matrix"]["Material"]["Minor"], 1)
        self.assertEqual(report["material_or_blocking_recall"], 1 / 3)
        self.assertEqual(report["unscored_material_or_blocking"], 1)
        self.assertEqual(report["misses"][0]["checks_triggered"], ["tie_rule"])
        self.assertEqual(tier.score_rows({}, [])["status"], "no_human_labels")

    def test_sheet_selection_keeps_gamma_identity_and_named_side(self):
        gamma = {"markets": [
            {"slug": "match", "outcomes": '["Hornets", "Heat"]', "description": "Winner of the game."},
            {"slug": "spread", "outcomes": '["Hornets", "Heat"]', "description": "Spread of the game."},
        ]}
        market = tier.gamma_market(gamma, "match", "Heat", "https://gamma-api.polymarket.com/events/slug/match")
        self.assertEqual(market["venue"], "polymarket_international")
        self.assertEqual(market["side"], "Heat")
        self.assertEqual(market["documents"]["description"], "Winner of the game.")
