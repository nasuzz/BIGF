import unittest
from unittest.mock import patch

from agent import b_adapter
from b_agent.models import Capability
from b_agent.models import Evidence as BEvidence
from b_agent.models import ProductType, ValueProvenance
from b_agent.pipeline import BAgentPipeline
from b_agent.retrievers import InMemoryStructuredRetriever, RetrieverRegistry
from contracts import AnswerStatus, FillType, ReasonCode, SearchType


def _successful_bond_pipeline():
    registry = RetrieverRegistry()
    registry.register(
        Capability.STRUCTURED_SEARCH,
        InMemoryStructuredRetriever(
            [
                {
                    "product_id": "B1",
                    "product_type": "bond",
                    "name": "테스트 우량채권 1",
                    "currency": "KRW",
                    "sale_available": True,
                    "credit_rating": "AAA",
                    "yield": 3.5,
                    "source_ref": "bond.xlsx#row=2",
                    "as_of_date": "2026-08-31",
                },
                {
                    "product_id": "B2",
                    "product_type": "bond",
                    "name": "테스트 우량채권 2",
                    "currency": "KRW",
                    "sale_available": True,
                    "credit_rating": "AA-",
                    "yield": 4.2,
                    "source_ref": "bond.xlsx#row=3",
                    "as_of_date": "2026-08-31",
                },
            ]
        ),
    )
    return BAgentPipeline(registry=registry)


class AnswerQuestionEmptyInputTest(unittest.TestCase):
    def test_empty_question_returns_unanswerable_without_building_plan(self):
        with patch.object(b_adapter._PIPELINE, "plan") as mock_plan:
            result = b_adapter.answer_question("Q-1", "   ")

        self.assertEqual(result.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(result.retrieved_context, [])
        self.assertEqual(len(result.think_trace), 1)
        self.assertTrue(result.answer)
        mock_plan.assert_not_called()


class AnswerQuestionNormalPathTest(unittest.TestCase):
    def test_real_question_runs_full_pipeline_without_crashing(self):
        result = b_adapter.answer_question("Q-1", "삼성전자 ETF 알려줘")

        self.assertIsInstance(result.status, AnswerStatus)
        self.assertEqual(result.retrieved_context, [])
        self.assertEqual([step.step for step in result.think_trace], ["route", "retrieve", "validate"])
        self.assertTrue(result.answer)


class AnswerQuestionExceptionPathTest(unittest.TestCase):
    def test_pipeline_exception_returns_safe_fallback(self):
        with patch.object(
            b_adapter._PIPELINE, "plan", side_effect=RuntimeError("boom")
        ):
            result = b_adapter.answer_question("Q-1", "삼성전자 ETF 알려줘")

        self.assertEqual(result.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(result.reason_code, ReasonCode.RETRIEVAL_ERROR)
        self.assertEqual(result.retrieved_context, [])
        self.assertEqual(len(result.think_trace), 1)
        self.assertIn("RETRIEVAL_ERROR", result.think_trace[0].summary)
        self.assertNotIn("RuntimeError", result.think_trace[0].summary)
        self.assertTrue(result.answer)
        self.assertNotIn("RuntimeError", result.answer)
        self.assertNotIn("boom", result.answer)


class AnswerQuestionSuccessfulEvidenceTest(unittest.TestCase):
    def test_real_evidence_is_preserved_in_agent_result(self):
        question = "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 1개 추천"

        with patch.object(b_adapter, "_PIPELINE", _successful_bond_pipeline()):
            result = b_adapter.answer_question("Q-SUCCESS", question)

        self.assertEqual(result.status, AnswerStatus.ANSWERED)
        self.assertIsNone(result.reason_code)
        self.assertEqual(len(result.retrieved_context), 1)
        evidence = result.retrieved_context[0]
        self.assertEqual(evidence.product_id, "B2")
        self.assertEqual(evidence.product_name, "테스트 우량채권 2")
        self.assertEqual(evidence.dataset, "bond")
        self.assertEqual(evidence.source_ref, "bond.xlsx#row=3")
        self.assertEqual(evidence.as_of_date, "2026-08-31")
        self.assertEqual(evidence.search_type, SearchType.SQL)
        self.assertIsNotNone(evidence.score)
        self.assertEqual(
            [step.step for step in result.think_trace],
            ["route", "retrieve", "validate"],
        )
        self.assertIn("테스트 우량채권 2", result.answer)
        self.assertNotIn("테스트 우량채권 1", result.answer)


class FieldProvenanceMappingTest(unittest.TestCase):
    @staticmethod
    def _evidence(value_provenance):
        return BEvidence(
            evidence_id="structured:domestic_etf:101",
            capability=Capability.STRUCTURED_SEARCH,
            source_id="fund_ontology",
            content="테스트 국내 ETF",
            product_id="101",
            product_type=ProductType.DOMESTIC_ETF,
            as_of_date="2026-08-31",
            source_ref="domestic_etf.xlsx#sheet=상품&row=12",
            structured={
                "canonical_name": "테스트 국내 ETF",
                "name": "테스트 국내 ETF",
                "expense_ratio_pct": 0.15,
                "fee_rate": 0.15,
            },
            value_provenance=value_provenance,
        )

    def test_original_and_official_values_become_field_level_contract_items(self):
        evidence = self._evidence(
            [
                ValueProvenance(
                    field_name="canonical_name",
                    fill_type="original",
                    evidence_eligible=True,
                ),
                ValueProvenance(
                    field_name="expense_ratio_pct",
                    fill_type="official_fill",
                    evidence_eligible=True,
                    was_missing=True,
                    source_reference="https://example.test/product/101",
                    source_as_of_date="2026-08-30",
                ),
            ]
        )

        items = b_adapter._evidence_to_items(evidence)

        self.assertEqual(
            [(item.field, item.value, item.fill_type) for item in items],
            [
                ("canonical_name", "테스트 국내 ETF", FillType.ORIGINAL),
                ("expense_ratio_pct", 0.15, FillType.OFFICIAL_FILL),
            ],
        )
        self.assertEqual(items[0].source_ref, "domestic_etf.xlsx#sheet=상품&row=12")
        self.assertEqual(items[1].source_ref, "https://example.test/product/101")
        self.assertEqual(items[1].as_of_date, "2026-08-30")

    def test_manual_verified_name_is_mapped_to_public_contract_name(self):
        evidence = self._evidence(
            [
                ValueProvenance(
                    field_name="canonical_name",
                    fill_type="manual_verified",
                    evidence_eligible=True,
                )
            ]
        )

        item = b_adapter._evidence_to_items(evidence)[0]

        self.assertEqual(item.fill_type, FillType.MANUAL_FILL)

    def test_estimated_value_is_marked_so_api_can_exclude_it(self):
        evidence = self._evidence(
            [
                ValueProvenance(
                    field_name="expense_ratio_pct",
                    fill_type="estimated",
                    evidence_eligible=False,
                    was_missing=True,
                )
            ]
        )

        item = b_adapter._evidence_to_items(evidence)[0]

        self.assertEqual(item.fill_type, FillType.ESTIMATED)
        self.assertEqual(item.value, 0.15)

    def test_unverified_or_value_less_provenance_is_not_invented(self):
        evidence = self._evidence(
            [
                ValueProvenance(
                    field_name="ticker",
                    fill_type="official_fill",
                    evidence_eligible=False,
                )
            ]
        )

        item = b_adapter._evidence_to_items(evidence)[0]

        self.assertIsNone(item.field)
        self.assertIsNone(item.value)
        self.assertIsNone(item.fill_type)


class LLMInjectionTest(unittest.TestCase):
    def test_build_pipeline_injects_supplied_llm_into_answer_composer(self):
        class FakeGateway:
            def capability_snapshot(self):
                from b_agent.gateway import CapabilitySnapshot

                return CapabilitySnapshot(
                    snapshot_id="test",
                    available=frozenset(),
                    registry_version="test",
                )

            def retrieve(self, capability, query, upstream_evidence, top_k):
                raise AssertionError("not called")

        llm = object()

        pipeline = b_adapter._build_pipeline(FakeGateway(), llm_client=llm)

        self.assertIs(pipeline.composer.llm_client, llm)


if __name__ == "__main__":
    unittest.main()
