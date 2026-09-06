import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from api.main import app
from b_agent.models import Capability
from b_agent.pipeline import BAgentPipeline
from b_agent.retrievers import InMemoryStructuredRetriever, RetrieverRegistry


client = TestClient(app)


def _successful_bond_pipeline():
    registry = RetrieverRegistry()
    registry.register(
        Capability.STRUCTURED_SEARCH,
        InMemoryStructuredRetriever(
            [
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
                }
            ]
        ),
    )
    return BAgentPipeline(registry=registry)


class ApiErrorHandlingTest(unittest.TestCase):
    def _assert_five_string_fields(self, response):
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "question_id",
                "question",
                "retrieved_context",
                "think_trace",
                "answer",
            },
        )
        self.assertTrue(all(isinstance(value, str) for value in body.values()))
        return body

    def test_adapter_failure_is_returned_as_a_safe_retrieval_error(self):
        secret = "NCP_DB_PASSWORD=do-not-expose"

        with patch.object(b_adapter._PIPELINE, "plan", side_effect=RuntimeError(secret)):
            response = client.get(
                "/answer",
                params={"question_id": "Q-ADAPTER", "question": "국내 ETF 추천"},
            )

        body = self._assert_five_string_fields(response)
        self.assertEqual(body["retrieved_context"], "")
        self.assertIn("사유=RETRIEVAL_ERROR", body["think_trace"])
        self.assertIn("처리단계=adapter", body["think_trace"])
        self.assertIn("근거 없는 답변은 생성하지 않았습니다", body["answer"])
        self.assertNotIn(secret, response.text)

    def test_api_failure_is_returned_as_a_safe_retrieval_error(self):
        secret = "postgresql://db_user:password@internal-db/funds"

        with patch("api.main.answer_question", side_effect=RuntimeError(secret)):
            response = client.get(
                "/answer",
                params={"question_id": "Q-API", "question": "해외 ETF 전략 검색"},
            )

        body = self._assert_five_string_fields(response)
        self.assertEqual(body["retrieved_context"], "")
        self.assertEqual(
            body["think_trace"],
            "상태=unanswerable; 원본상태=error; "
            "사유=RETRIEVAL_ERROR; 처리단계=api",
        )
        self.assertIn("근거 없는 상품은 생성하지 않았습니다", body["answer"])
        self.assertNotIn(secret, response.text)


class ApiSuccessHandlingTest(unittest.TestCase):
    def test_successful_evidence_is_preserved_in_five_field_response(self):
        question = "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 1개 추천"

        with patch.object(b_adapter, "_PIPELINE", _successful_bond_pipeline()):
            response = client.get(
                "/answer",
                params={"question_id": "Q-SUCCESS", "question": question},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "question_id",
                "question",
                "retrieved_context",
                "think_trace",
                "answer",
            },
        )
        self.assertTrue(all(isinstance(value, str) for value in body.values()))
        self.assertIn("상품ID: B2", body["retrieved_context"])
        self.assertIn("상품명: 테스트 우량채권 2", body["retrieved_context"])
        self.assertIn("출처: bond.xlsx#row=3", body["retrieved_context"])
        self.assertIn("판정: answered", body["think_trace"])
        self.assertIn("테스트 우량채권 2", body["answer"])


if __name__ == "__main__":
    unittest.main()
