import json
import unittest
from unittest.mock import patch

from agent import b_adapter
from b_agent.models import AnswerStatus as BAnswerStatus
from b_agent.models import Capability, ReasonCode as BReasonCode
from b_agent.pipeline import BAgentPipeline, PipelineExecution
from b_agent.retrievers import InMemoryStructuredRetriever, RetrieverRegistry
from contracts import AnswerStatus, ReasonCode


QUESTION = (
    "원화 표시이며 매수 가능한 AA- 이상 채권을 "
    "수익률 높은 순으로 1개 추천"
)


def _pipeline(records=None, retriever=None):
    registry = RetrieverRegistry()
    registry.register(
        Capability.STRUCTURED_SEARCH,
        retriever
        or InMemoryStructuredRetriever(
            records
            if records is not None
            else [
                {
                    "product_id": "B1",
                    "product_type": "bond",
                    "name": "테스트 채권",
                    "currency": "KRW",
                    "sale_available": True,
                    "credit_rating": "AAA",
                    "yield": 4.2,
                    "source_ref": "bond.xlsx#row=2",
                }
            ]
        ),
    )
    return BAgentPipeline(registry=registry)


class FailingRetriever:
    def retrieve(self, step, context):
        del step, context
        raise ConnectionError("NCP_DB_PASSWORD=do-not-expose")


class PipelineAdapterParityTest(unittest.TestCase):
    def _assert_shared_result(
        self,
        pipeline: BAgentPipeline,
        execution: PipelineExecution,
        question: str,
    ):
        with patch.object(pipeline, "execute", return_value=execution):
            payload = pipeline.run("Q-PARITY", question)
            with patch.object(b_adapter, "_PIPELINE", pipeline):
                result = b_adapter.answer_question("Q-PARITY", question)

        expected_status = b_adapter._STATUS_MAP[execution.assessment.status]
        expected_reason = (
            b_adapter._REASON_CODE_MAP[execution.assessment.reason_codes[0]]
            if execution.assessment.reason_codes
            else None
        )
        self.assertEqual(result.status, expected_status)
        self.assertEqual(result.reason_code, expected_reason)
        self.assertEqual(result.answer, execution.answer)
        self.assertEqual(payload.answer, execution.answer)
        self.assertEqual(payload, execution.to_answer_payload())
        self.assertEqual(
            [item.evidence_id for item in result.retrieved_context],
            [item.evidence_id for item in execution.fused_evidence],
        )
        self.assertEqual(
            [item["evidence_id"] for item in json.loads(payload.retrieved_context)],
            [item.evidence_id for item in execution.fused_evidence],
        )
        self.assertEqual(result.timings.routing_ms, execution.timings.routing_ms)
        self.assertEqual(result.timings.retrieval_ms, execution.timings.retrieval_ms)
        self.assertEqual(result.timings.validation_ms, execution.timings.validation_ms)
        self.assertEqual(result.timings.llm_ms, execution.timings.llm_ms)
        self.assertEqual(result.timings.total_ms, execution.timings.total_ms)

    def test_normal_result_has_pipeline_adapter_parity(self):
        pipeline = _pipeline()
        execution = pipeline.execute("Q-PARITY", QUESTION)

        self.assertEqual(execution.assessment.status, BAnswerStatus.COMPLETE)
        self.assertEqual(len(execution.fused_evidence), 1)
        self._assert_shared_result(pipeline, execution, QUESTION)

    def test_zero_match_has_pipeline_adapter_parity(self):
        pipeline = _pipeline()
        question = f"{QUESTION} 수익률 99% 이상"
        execution = pipeline.execute("Q-PARITY", question)

        self.assertEqual(execution.assessment.status, BAnswerStatus.COMPLETE)
        self.assertEqual(execution.assessment.reason_codes, [BReasonCode.NO_MATCH])
        self.assertEqual(execution.fused_evidence, [])
        self._assert_shared_result(pipeline, execution, question)

    def test_retrieval_failure_has_pipeline_adapter_parity(self):
        pipeline = _pipeline(retriever=FailingRetriever())
        execution = pipeline.execute("Q-PARITY", QUESTION)

        self.assertEqual(execution.assessment.status, BAnswerStatus.ERROR)
        self.assertEqual(
            execution.assessment.reason_codes, [BReasonCode.RETRIEVAL_ERROR]
        )
        self.assertNotIn("NCP_DB_PASSWORD", execution.think_trace)
        self.assertNotIn("NCP_DB_PASSWORD", execution.answer)
        self._assert_shared_result(pipeline, execution, QUESTION)

    def test_unexpected_error_is_sanitized_for_both_outputs(self):
        pipeline = _pipeline()
        secret = "postgresql://db_user:password@internal-db/funds"
        with patch.object(pipeline, "plan", side_effect=RuntimeError(secret)):
            execution = pipeline.execute("Q-PARITY", QUESTION)

        self.assertEqual(execution.error_stage, "route")
        self.assertEqual(execution.assessment.status, BAnswerStatus.ERROR)
        self.assertNotIn(secret, execution.to_answer_payload().to_dict().values())
        self._assert_shared_result(pipeline, execution, QUESTION)

    def test_blank_question_has_pipeline_adapter_parity(self):
        pipeline = _pipeline()
        with patch.object(pipeline, "plan") as mock_plan:
            execution = pipeline.execute("Q-PARITY", "   ")

        mock_plan.assert_not_called()
        self.assertEqual(execution.assessment.status, BAnswerStatus.UNANSWERABLE)
        self.assertEqual(
            execution.assessment.reason_codes, [BReasonCode.UNSUPPORTED_QUERY]
        )
        self._assert_shared_result(pipeline, execution, "   ")
        with patch.object(pipeline, "execute", return_value=execution):
            with patch.object(b_adapter, "_PIPELINE", pipeline):
                result = b_adapter.answer_question("Q-PARITY", "   ")
        self.assertEqual(result.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(result.reason_code, ReasonCode.OUT_OF_SCOPE)


if __name__ == "__main__":
    unittest.main()
