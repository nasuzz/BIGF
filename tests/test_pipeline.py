import json
import unittest

from b_agent.models import Capability, ProductType
from b_agent.pipeline import BAgentPipeline
from b_agent.retrievers import (
    BM25KeywordRetriever,
    InMemoryStructuredRetriever,
    RetrieverRegistry,
    SearchDocument,
)


BONDS = [
    {
        "product_id": "B1",
        "product_type": "bond",
        "name": "우량채권 1",
        "currency": "KRW",
        "sale_available": True,
        "credit_rating": "AAA",
        "yield": 3.5,
        "source_ref": "bond.xlsx#row=2",
    },
    {
        "product_id": "B2",
        "product_type": "bond",
        "name": "우량채권 2",
        "currency": "KRW",
        "sale_available": True,
        "credit_rating": "AA-",
        "yield": 4.2,
        "source_ref": "bond.xlsx#row=3",
    },
    {
        "product_id": "B3",
        "product_type": "bond",
        "name": "등급미달 채권",
        "currency": "KRW",
        "sale_available": True,
        "credit_rating": "A+",
        "yield": 5.0,
    },
    {
        "product_id": "B4",
        "product_type": "bond",
        "name": "달러 채권",
        "currency": "USD",
        "sale_available": True,
        "credit_rating": "AAA",
        "yield": 6.0,
    },
]


class PipelineTest(unittest.TestCase):
    def _structured_pipeline(self):
        registry = RetrieverRegistry()
        registry.register(Capability.STRUCTURED_SEARCH, InMemoryStructuredRetriever(BONDS))
        return BAgentPipeline(registry=registry)

    def test_complete_structured_answer_and_order(self):
        payload = self._structured_pipeline().run(
            "q1",
            "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 2개 추천",
        ).to_dict()
        context = json.loads(payload["retrieved_context"])
        self.assertEqual([item["product_id"] for item in context], ["B2", "B1"])
        self.assertIn("상태=complete", payload["think_trace"])
        self.assertEqual(
            set(payload),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in payload.values()))

    def test_available_search_with_zero_matches_is_complete(self):
        payload = self._structured_pipeline().run(
            "q2", "원화 표시이며 매수 가능한 AAA 이상 채권 중 수익률 99% 이상"
        ).to_dict()
        self.assertIn("상태=complete", payload["think_trace"])
        self.assertIn("no_match", payload["think_trace"])
        self.assertIn("일치하는 상품이 없습니다", payload["answer"])

    def test_missing_external_capabilities_are_unanswerable(self):
        payload = self._structured_pipeline().run(
            "q3",
            "에코프로 자회사를 편입한 ETF를 순자산 큰 순으로 정렬하고 위험요인을 설명해줘",
        ).to_dict()
        self.assertIn("상태=unanswerable", payload["think_trace"])
        self.assertIn("relation_data_missing", payload["think_trace"])
        self.assertIn("기업 관계", payload["answer"])
        self.assertIn("ETF 편입종목", payload["answer"])

    def test_bm25_prefers_semiconductor_document(self):
        documents = [
            SearchDocument(
                document_id="E1",
                text="중국 반도체 산업과 인공지능 칩 기업에 투자하는 ETF 전략",
                source_id="foreign_etf_master",
                product_id="E1",
                product_type=ProductType.FOREIGN_ETF,
                source_ref="foreign_etf.xlsx#row=2",
            ),
            SearchDocument(
                document_id="E2",
                text="미국 장기 국채 지수를 추종하는 채권 ETF 전략",
                source_id="foreign_etf_master",
                product_id="E2",
                product_type=ProductType.FOREIGN_ETF,
                source_ref="foreign_etf.xlsx#row=3",
            ),
        ]
        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            InMemoryStructuredRetriever(
                [
                    {
                        "record_id": "E1:1",
                        "product_id": "E1",
                        "product_type": "foreign_etf",
                        "name": "중국 반도체 ETF",
                        "investment_region": "중국",
                        "source_ref": "foreign_etf.xlsx#row=2",
                    }
                ]
            ),
        )
        registry.register(Capability.KEYWORD_SEARCH, BM25KeywordRetriever(documents))
        payload = BAgentPipeline(registry=registry).run(
            "q4", "중국 반도체 관련 해외 ETF 전략을 알려줘"
        ).to_dict()
        context = json.loads(payload["retrieved_context"])
        self.assertEqual(context[0]["product_id"], "E1")


if __name__ == "__main__":
    unittest.main()
