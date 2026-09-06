"""Issue #42: resolve exact aliases before ambiguity and downstream scoping."""

import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from agent.ext_retrievers import register_ext_retrievers
from api.main import app
from b_agent.models import Capability
from b_agent.postgres_gateway import PostgresDataGateway
from tests.test_postgres_gateway import FakeConnection, _identity_row, _provider


def identity_row(product_id, term="449770", match_type="EXACT", **overrides):
    row = _identity_row()
    row.update(
        product_id=product_id,
        canonical_name=f"검증상품{product_id} ETF",
        ticker=term if match_type == "EXACT" else f"유사{product_id}",
        matched_term=term,
        matched_alias_value=term if match_type == "EXACT" else f"유사{term}",
        matched_alias_type="TICKER",
        match_type=match_type,
        match_score=Decimal("1") if match_type == "EXACT" else Decimal("0.9"),
    )
    row.update(overrides)
    return row


class ExactIdentityTest(unittest.TestCase):
    def retrieve(self, rows, *, terms=None, mentions=None, top_k=20):
        connection = FakeConnection(identity_rows=rows)
        batch = PostgresDataGateway(_provider(connection)).retrieve(
            Capability.IDENTITY_SEARCH,
            {
                "identifiers": ["449770"] if terms is None else terms,
                "product_mentions": mentions or [],
                "product_types": ["domestic_etf", "foreign_etf"],
            },
            {},
            top_k,
        )
        self.assertEqual(len(connection.calls), 1)
        self.assertEqual(connection.calls[0][1][1], ["DOMESTIC_ETP", "OVERSEAS_ETP"])
        return batch

    def test_exact_ticker_or_isin_excludes_fuzzy_products_and_keeps_sources(self):
        for term, alias_type in (("449770", "TICKER"), ("KRG520000529", "ISIN")):
            with self.subTest(term=term):
                exact = identity_row(24122, term, matched_alias_type=alias_type)
                rows = [
                    identity_row(300 + i, term, kind)
                    for i, kind in enumerate(["PREFIX", "PARTIAL", "SIMILAR"])
                ] + [exact]
                batch = self.retrieve(rows, terms=[term])
                self.assertEqual([ev.product_id for ev in batch.evidence], ["24122"])
                self.assertEqual(batch.total_hits, 1)
                self.assertFalse(batch.ambiguous)
                self.assertTrue(batch.coverage_complete)
                self.assertFalse(batch.truncated)
                evidence = batch.evidence[0]
                self.assertEqual(evidence.structured["matched_terms"], [term])
                self.assertEqual(evidence.source_ref, "domestic_etf.xlsx#sheet=상품&row=12")
                self.assertEqual(evidence.as_of_date, "2026-08-31")
                self.assertEqual(evidence.provenance["snapshot_id"], 7)
                self.assertTrue(evidence.value_provenance)

    def test_shared_exact_alias_stays_ambiguous_even_when_top_k_is_one(self):
        batch = self.retrieve([
            identity_row(101),
            identity_row(202, product_type="OVERSEAS_ETP"),
            identity_row(303, match_type="PARTIAL"),
        ], top_k=1)
        self.assertTrue(batch.ambiguous)
        self.assertEqual(batch.total_hits, 2)
        self.assertTrue(batch.truncated)
        self.assertEqual(len(batch.evidence), 1)

    def test_no_exact_match_keeps_fuzzy_candidates_and_ambiguity(self):
        batch = self.retrieve([
            identity_row(101, match_type="PREFIX"),
            identity_row(202, match_type="SIMILAR"),
        ])
        self.assertTrue(batch.ambiguous)
        self.assertEqual(batch.total_hits, 2)

    def test_resolution_is_per_term_not_global(self):
        batch = self.retrieve([
            identity_row(101),
            identity_row(202, match_type="SIMILAR"),
            identity_row(202, "다른 상품", "PREFIX"),
            identity_row(303, "다른 상품", "PARTIAL"),
        ], mentions=["다른 상품"])
        self.assertTrue(batch.ambiguous)
        self.assertEqual(batch.total_hits, 3)
        evidence = {ev.product_id: ev for ev in batch.evidence}
        self.assertEqual(evidence["101"].structured["matched_terms"], ["449770"])
        self.assertEqual(evidence["202"].structured["matched_terms"], ["다른 상품"])

    def test_multiple_identifiers_resolve_independently_and_deduplicate(self):
        batch = self.retrieve([
            identity_row(101), identity_row(202, match_type="SIMILAR"),
            identity_row(101, "KRG520000529", matched_alias_type="ISIN"),
            identity_row(202, "ABC", product_type="OVERSEAS_ETP"),
        ], terms=["449770", "KRG520000529", "ABC"])
        self.assertFalse(batch.ambiguous)
        self.assertEqual(batch.total_hits, 2)
        evidence = {ev.product_id: ev for ev in batch.evidence}
        self.assertEqual(
            evidence["101"].structured["matched_terms"], ["449770", "KRG520000529"]
        )
        self.assertEqual(evidence["202"].structured["matched_terms"], ["ABC"])

    def test_exact_name_also_wins_over_partial_alias(self):
        batch = self.retrieve([
            identity_row(101, "검증상품 ETF", matched_alias_type="CANONICAL_NAME"),
            identity_row(202, "검증상품 ETF", "PREFIX"),
        ], terms=[], mentions=["검증상품 ETF"])
        self.assertFalse(batch.ambiguous)
        self.assertEqual(batch.total_hits, 1)

    def test_unresolved_terms_and_function_limit_are_not_hidden(self):
        batch = self.retrieve([
            identity_row(101),
            identity_row(202, match_type="SIMILAR", _partition_hits=100),
        ], terms=["449770", "UNKNOWN"])
        self.assertEqual([ev.product_id for ev in batch.evidence], ["101"])
        self.assertFalse(batch.coverage_complete)
        self.assertTrue(batch.truncated)
        self.assertIsNone(batch.total_hits)
        self.assertIn("UNKNOWN", batch.coverage_note)
        self.assertIn("최대 100건", batch.coverage_note)

    def test_equal_score_is_not_treated_as_exact_without_match_type(self):
        batch = self.retrieve([
            identity_row(101),
            identity_row(202, match_type="SIMILAR", match_score=Decimal("1.0")),
        ])
        self.assertEqual([ev.product_id for ev in batch.evidence], ["101"])


class RecordingHoldingFetcher:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return [{
            "product_id": product_id,
            "product_type": "DOMESTIC_ETP",
            "product_name": f"검증상품{product_id} ETF",
            "holding_key": "TEST-HOLDING",
            "holding_name": "검증편입종목",
            "weight_pct": Decimal("3.5"),
            "source_record_key": f"krx-holding-{product_id}",
            "as_of_date": "2026-08-31",
        } for product_id in params[1] or []]


class HoldingLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return (
            "검증상품24122 ETF의 보유종목은 검증편입종목입니다. "
            "[holding:24122:krx-holding-24122]"
        )


class ExactIdentityHTTPTest(unittest.TestCase):
    def answer(self, rows):
        connection = FakeConnection(identity_rows=rows, structured_rows=[rows[0]])
        fetcher = RecordingHoldingFetcher()
        llm = HoldingLLM()
        # Build the actual API pipeline and official ext retriever, replacing
        # only DB I/O and HCX. No network or production credentials are needed.
        with patch.object(
            b_adapter, "register_ext_retrievers",
            side_effect=lambda registry: register_ext_retrievers(registry, fetcher=fetcher),
        ), patch.object(b_adapter, "register_prospectus_retriever"):
            pipeline = b_adapter._build_pipeline(
                gateway=PostgresDataGateway(_provider(connection)), llm_client=llm,
            )
        with patch.object(b_adapter, "_PIPELINE", pipeline):
            response = TestClient(app).get("/answer", params={
                "question_id": "issue-42",
                "question": "449770 보유종목 알려줘",
            })
        self.assertEqual(response.status_code, 200)
        return response.json(), connection, fetcher, llm

    def test_exact_ticker_flows_to_official_holding_retriever_and_answer(self):
        rows = [identity_row(24122)] + [
            identity_row(300 + i, match_type="SIMILAR") for i in range(5)
        ]
        body, connection, fetcher, llm = self.answer(rows)
        self.assertEqual(llm.calls, 1)
        self.assertIn("B상태: complete", body["think_trace"])
        self.assertIn("검증편입종목", body["answer"])
        self.assertIn("[holding:24122:krx-holding-24122]", body["answer"])
        self.assertEqual(len(fetcher.calls), 1)
        self.assertIn("ext.search_etf_holdings", fetcher.calls[0][0])
        self.assertEqual(fetcher.calls[0][1][1], [24122])
        for row in rows[1:]:
            self.assertNotIn(row["canonical_name"], body["retrieved_context"])
        structured_calls = [(sql, params) for sql, params in connection.calls
                            if "search.product_metrics" in sql]
        self.assertEqual(len(structured_calls), 1)
        self.assertIn(["24122"], structured_calls[0][1])

    def test_shared_exact_ticker_still_refuses_to_choose_a_product(self):
        body, _, _, llm = self.answer([identity_row(24122), identity_row(24123)])
        self.assertEqual(llm.calls, 0)
        self.assertIn("B상태: unanswerable", body["think_trace"])
        self.assertIn("후보가 여러 개", body["answer"])


if __name__ == "__main__":
    unittest.main()
