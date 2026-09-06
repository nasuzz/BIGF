import unittest

from b_agent.answerability import MISSING_REASON, AnswerabilityPolicy
from b_agent.answering import AnswerComposer
from b_agent.models import (
    AnswerDecision,
    AnswerStatus,
    Capability,
    Evidence,
    ExecutionReport,
    ProductType,
    QueryUnderstanding,
    ReasonCode,
    RetrievalPlan,
    RetrievalStep,
    StepOutcome,
    StepResult,
)


class VectorAnswerabilityTest(unittest.TestCase):
    def setUp(self):
        self.policy = AnswerabilityPolicy()

    @staticmethod
    def _plan(*capabilities):
        understanding = QueryUnderstanding(
            question="반도체 관련 해외 ETF 전략을 알려줘",
            product_types=[ProductType.FOREIGN_ETF],
        )
        steps = [
            RetrievalStep(
                step_id=capability.value,
                capability=capability,
                query={},
                required=True,
            )
            for capability in capabilities
        ]
        return RetrievalPlan(version="test", understanding=understanding, steps=steps)

    def test_every_capability_has_an_explicit_missing_reason(self):
        self.assertEqual(set(Capability), set(MISSING_REASON))

    def test_unavailable_vector_search_is_document_data_missing(self):
        plan = self._plan(Capability.VECTOR_SEARCH)
        report = ExecutionReport(
            plan=plan,
            step_results=[
                StepResult(
                    step_id=Capability.VECTOR_SEARCH.value,
                    capability=Capability.VECTOR_SEARCH,
                    outcome=StepOutcome.UNAVAILABLE,
                )
            ],
        )

        decision = self.policy.assess(report, [])

        self.assertEqual(decision.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(decision.reason_codes, [ReasonCode.DOCUMENT_DATA_MISSING])
        self.assertEqual(decision.missing_capabilities, [Capability.VECTOR_SEARCH])

    def test_incomplete_vector_coverage_is_document_data_missing(self):
        plan = self._plan(Capability.VECTOR_SEARCH)
        report = ExecutionReport(
            plan=plan,
            step_results=[
                StepResult(
                    step_id=Capability.VECTOR_SEARCH.value,
                    capability=Capability.VECTOR_SEARCH,
                    outcome=StepOutcome.EMPTY,
                    coverage_complete=False,
                    coverage_note="전략문 일부만 검색할 수 있습니다.",
                )
            ],
        )

        decision = self.policy.assess(report, [])

        self.assertEqual(decision.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(decision.reason_codes, [ReasonCode.DOCUMENT_DATA_MISSING])
        self.assertEqual(decision.missing_capabilities, [Capability.VECTOR_SEARCH])

    def test_empty_vector_result_after_structured_evidence_is_partial(self):
        plan = self._plan(Capability.STRUCTURED_SEARCH, Capability.VECTOR_SEARCH)
        structured_evidence = Evidence(
            evidence_id="structured:F1",
            capability=Capability.STRUCTURED_SEARCH,
            source_id="products",
            content="해외 ETF 후보",
            product_id="F1",
            product_type=ProductType.FOREIGN_ETF,
        )
        report = ExecutionReport(
            plan=plan,
            step_results=[
                StepResult(
                    step_id=Capability.STRUCTURED_SEARCH.value,
                    capability=Capability.STRUCTURED_SEARCH,
                    outcome=StepOutcome.SUCCESS,
                    evidence=[structured_evidence],
                    coverage_complete=True,
                ),
                StepResult(
                    step_id=Capability.VECTOR_SEARCH.value,
                    capability=Capability.VECTOR_SEARCH,
                    outcome=StepOutcome.EMPTY,
                    coverage_complete=True,
                ),
            ],
        )

        decision = self.policy.assess(report, [structured_evidence])

        self.assertEqual(decision.status, AnswerStatus.PARTIAL)
        self.assertEqual(decision.reason_codes, [ReasonCode.DOCUMENT_DATA_MISSING])

    def test_failed_vector_search_remains_a_retrieval_error(self):
        plan = self._plan(Capability.VECTOR_SEARCH)
        report = ExecutionReport(
            plan=plan,
            step_results=[
                StepResult(
                    step_id=Capability.VECTOR_SEARCH.value,
                    capability=Capability.VECTOR_SEARCH,
                    outcome=StepOutcome.FAILED,
                )
            ],
        )

        decision = self.policy.assess(report, [])

        self.assertEqual(decision.status, AnswerStatus.ERROR)
        self.assertEqual(decision.reason_codes, [ReasonCode.RETRIEVAL_ERROR])

    def test_vector_fallback_uses_a_user_facing_missing_data_name(self):
        plan = self._plan(Capability.VECTOR_SEARCH)
        report = ExecutionReport(plan=plan, step_results=[])
        decision = AnswerDecision(
            status=AnswerStatus.UNANSWERABLE,
            reason_codes=[ReasonCode.DOCUMENT_DATA_MISSING],
            message="전략문 의미 검색 근거가 없습니다.",
            missing_capabilities=[Capability.VECTOR_SEARCH],
        )

        payload = AnswerComposer().compose("Q-VECTOR", report, decision, [])

        self.assertIn("의미 검색 가능한 전략·설명 텍스트", payload.answer)
        self.assertNotIn("vector_search", payload.answer)


if __name__ == "__main__":
    unittest.main()
