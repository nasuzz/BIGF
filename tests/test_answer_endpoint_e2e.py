"""HTTP-level end-to-end check for issue #7 item 4: B response -> adapter
mapping -> /answer response, verified through the actual FastAPI endpoint
rather than by calling agent.b_adapter.answer_question() directly.

tests/test_b_adapter.py already proves the mapping is correct at the
AgentResult level; this file proves api/main.py's serialization on top of
that AgentResult produces a real, non-empty retrieved_context string in the
actual HTTP response body.
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from api.main import app
from b_agent.answering import AnswerComposer
from b_agent.models import Capability
from b_agent.pipeline import BAgentPipeline
from b_agent.retrievers import InMemoryStructuredRetriever, RetrieverRegistry

client = TestClient(app)


def _successful_bond_pipeline(llm_client=None):
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
    return BAgentPipeline(
        registry=registry,
        composer=AnswerComposer(llm_client=llm_client),
    )


class FakeGroundedLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return (
            "테스트 우량채권 2의 수익률은 4.2%이며 기준일은 "
            "2026-08-31입니다. [structured:bond:B2:1]"
        )


class AnswerEndpointEndToEndTest(unittest.TestCase):
    def test_answer_endpoint_returns_real_evidence_in_retrieved_context(self):
        question = "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 1개 추천"

        with patch.object(b_adapter, "_PIPELINE", _successful_bond_pipeline()):
            response = client.get(
                "/answer",
                params={"question_id": "Q-E2E", "question": question},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["question_id"], "Q-E2E")
        self.assertIn("테스트 우량채권 2", body["answer"])

        context = body["retrieved_context"]
        self.assertTrue(context)
        self.assertIn("테스트 우량채권 2", context)
        self.assertIn("bond.xlsx#row=3", context)
        self.assertNotIn("테스트 우량채권 1", context)

        self.assertIn("질문 유형", body["think_trace"])
        self.assertIn("검색 경로", body["think_trace"])
        self.assertIn("판정", body["think_trace"])

    def test_grounded_llm_answer_preserves_the_five_string_http_contract(self):
        question = "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 1개 추천"
        llm = FakeGroundedLLM()

        with patch.object(b_adapter, "_PIPELINE", _successful_bond_pipeline(llm)):
            response = client.get(
                "/answer",
                params={"question_id": "Q-HCX-E2E", "question": question},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in body.values()))
        self.assertEqual(llm.calls, 1)
        self.assertIn("수익률은 4.2%", body["answer"])
        self.assertIn("테스트 우량채권 2", body["retrieved_context"])


if __name__ == "__main__":
    unittest.main()
