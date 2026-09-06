"""Issue #44: ineligible values must not escape through public evidence text."""

import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from api.main import app
from b_agent.models import Capability, Evidence, ProductType, ValueProvenance
from b_agent.postgres_gateway import PostgresDataGateway
from tests.test_postgres_gateway import (
    FakeConnection, StaticQueryEmbedder, _provider, _structured_row,
)


class PublicEvidenceProvenanceTest(unittest.TestCase):
    def _answer(self, row):
        gateway = PostgresDataGateway(
            _provider(FakeConnection(structured_rows=[row])),
            query_embedder=StaticQueryEmbedder(),
        )
        pipeline = b_adapter._build_pipeline(gateway=gateway)
        with patch.object(b_adapter, "_PIPELINE", pipeline):
            response = TestClient(app).get(
                "/answer",
                params={"question_id": "issue-44", "question": "순자산 높은 국내 ETF 1개"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in body.values()))
        self.assertIn("B상태: complete", body["think_trace"])
        return body

    def test_ineligible_fee_is_absent_from_http_context_but_verified_fields_survive(self):
        for fill_type, eligible in (
            ("estimated", False), ("official_fill", False),
            ("manual_verified", False), ("estimated", True),
        ):
            with self.subTest(fill_type=fill_type, eligible=eligible):
                row = _structured_row()
                row["expense_ratio_pct"] = 9.87654321
                row["_value_provenance"][1].update(
                    fill_type=fill_type, evidence_eligible=eligible,
                )
                for field, kind in (("asset_amount", "official_fill"), ("return_1y_pct", "manual_verified")):
                    row["_value_provenance"].append({
                        "field_name": field, "fill_type": kind,
                        "evidence_eligible": True, "was_missing": True,
                        "source_reference": f"https://example.test/verified/{field}",
                        "source_as_of_date": "2026-08-30",
                    })
                body = self._answer(row)
                context = body["retrieved_context"]
                self.assertNotIn("9.87654321", str(body))
                self.assertNotIn("expense_ratio_pct", context)
                self.assertNotIn('"fee_rate"', context)
                self.assertIn("테스트 국내 ETF", context)
                self.assertIn("domestic_etf.xlsx#sheet=상품&row=12", context)
                self.assertIn("필드: asset_amount / 값: 120000000000", context)
                self.assertIn("필드: return_1y_pct / 값: 12.3", context)
                self.assertIn("기준일: 2026-08-30", context)
                self.assertIn("https://example.test/verified/asset_amount", context)
                self.assertIn("https://example.test/verified/return_1y_pct", context)

    def test_unverified_name_and_numeric_value_do_not_leak_through_fallback_item(self):
        row = _structured_row()
        row["canonical_name"] = "UNVERIFIED_PRODUCT_NAME_44"
        row["expense_ratio_pct"] = 9.87654321
        for source in row["_value_provenance"]:
            source.update(fill_type="official_fill", evidence_eligible=False)
        body = self._answer(row)
        self.assertNotIn("UNVERIFIED_PRODUCT_NAME_44", str(body))
        self.assertNotIn("9.87654321", str(body))
        self.assertIn("상품ID: 101", body["retrieved_context"])
        self.assertIn("domestic_etf.xlsx#sheet=상품&row=12", body["retrieved_context"])

    def test_rejected_names_are_removed_from_raw_text_for_all_product_searches(self):
        for capability in (
            Capability.STRUCTURED_SEARCH, Capability.IDENTITY_SEARCH,
            Capability.KEYWORD_SEARCH, Capability.VECTOR_SEARCH,
        ):
            with self.subTest(capability=capability):
                ev = Evidence(
                    evidence_id="E44", capability=capability, source_id="test",
                    product_id="101", product_type=ProductType.DOMESTIC_ETF,
                    content="UNVERIFIED_NAME_44의 원본 설명",
                    structured={"canonical_name": "UNVERIFIED_NAME_44", "name": "UNVERIFIED_NAME_44", "currency": "KRW"},
                    source_ref="source.csv#row=1",
                    value_provenance=[ValueProvenance("canonical_name", "official_fill", False)],
                )
                original = copy.deepcopy(ev)
                items = b_adapter._evidence_to_items(ev)
                self.assertNotIn("UNVERIFIED_NAME_44", str([item.model_dump() for item in items]))
                self.assertIn("KRW", items[0].text)
                self.assertEqual(ev, original)

    def test_ineligible_alias_overrides_an_eligible_alias_of_the_same_value(self):
        ev = Evidence(
            evidence_id="E44", capability=Capability.STRUCTURED_SEARCH, source_id="test",
            content="9.87654321", structured={"name": "확인 상품", "fee_rate": 9.87654321, "expense_ratio_pct": 9.87654321},
            value_provenance=[
                ValueProvenance("fee_rate", "official_fill", False),
                ValueProvenance("expense_ratio_pct", "original", True),
            ],
        )
        items = b_adapter._evidence_to_items(ev)
        self.assertNotIn("9.87654321", str([item.model_dump() for item in items]))
        self.assertEqual(items[0].product_name, "확인 상품")

    def test_rejected_strategy_removes_excerpt_and_normalized_text_aliases(self):
        ev = Evidence(
            evidence_id="E44", capability=Capability.KEYWORD_SEARCH, source_id="test",
            content="UNVERIFIED_STRATEGY_44", structured={
                "name": "확인 ETF", "strategy_text": "UNVERIFIED_STRATEGY_44",
                "strategy": "UNVERIFIED_STRATEGY_44", "raw_text": "UNVERIFIED_STRATEGY_44",
                "normalized_text": "UNVERIFIED_STRATEGY_44", "evidence_excerpt": "UNVERIFIED_STRATEGY_44",
            },
            value_provenance=[ValueProvenance("strategy_text", "official_fill", False)],
        )
        items = b_adapter._evidence_to_items(ev)
        self.assertNotIn("UNVERIFIED_STRATEGY_44", str([item.model_dump() for item in items]))
        self.assertEqual(items[0].product_name, "확인 ETF")

    def test_evidence_without_field_provenance_preserves_original_content(self):
        ev = Evidence(
            evidence_id="E44", capability=Capability.DOCUMENT_SEARCH, source_id="test",
            content="검증된 문서 원문", structured={"name": "확인 ETF"},
            source_ref="document.pdf#page=1",
        )
        item = b_adapter._evidence_to_items(ev)[0]
        self.assertEqual(item.text, ev.content)
        self.assertEqual(item.source_ref, ev.source_ref)


if __name__ == "__main__":
    unittest.main()
