import unittest
from unittest.mock import MagicMock, patch

from agent import b_adapter
from contracts import AnswerStatus, ReasonCode


class AdapterErrorHandlingTest(unittest.TestCase):
    def _assert_safe_retrieval_error(self, result, secret):
        self.assertEqual(result.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(result.reason_code, ReasonCode.RETRIEVAL_ERROR)
        self.assertEqual(result.retrieved_context, [])
        self.assertEqual(
            result.think_trace[0].summary,
            "상태=unanswerable; 원본상태=error; "
            "사유=RETRIEVAL_ERROR; 처리단계=adapter",
        )
        self.assertIn("근거 없는 답변은 생성하지 않았습니다", result.answer)
        self.assertNotIn(secret, result.model_dump_json())

    def test_unexpected_adapter_error_is_classified_without_leaking_details(self):
        secret = "postgresql://db_user:password@internal-db/funds"

        with patch.object(b_adapter._PIPELINE, "plan", side_effect=RuntimeError(secret)):
            result = b_adapter.answer_question("Q-ADAPTER-ERROR", "국내 ETF 추천")

        self._assert_safe_retrieval_error(result, secret)

    def test_retrieval_execution_error_uses_the_same_safe_contract(self):
        secret = "NCP_DB_PASSWORD=do-not-expose"
        plan = MagicMock()

        with (
            patch.object(b_adapter._PIPELINE, "plan", return_value=plan),
            patch.object(
                b_adapter._PIPELINE.executor,
                "execute",
                side_effect=ConnectionError(secret),
            ),
        ):
            result = b_adapter.answer_question("Q-RETRIEVER-ERROR", "해외 ETF 전략 검색")

        self._assert_safe_retrieval_error(result, secret)


if __name__ == "__main__":
    unittest.main()
