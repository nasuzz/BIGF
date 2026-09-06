import unittest
from unittest.mock import patch

from agent import b_adapter
from agent.ext_retrievers import (
    PostgresHoldingRetriever,
    register_ext_retrievers,
)
from agent.prospectus_retriever import (
    PostgresProspectusRetriever,
    register_prospectus_retriever,
)
from b_agent.gateway import CapabilitySnapshot
from b_agent.models import Capability, Evidence, ProductType, RetrievalBatch
from b_agent.retrievers import RetrieverRegistry, StaticRetriever


class RecordingFetcher:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return list(self.rows)


class IdentityGateway:
    def capability_snapshot(self):
        return CapabilitySnapshot(
            snapshot_id="registry-integration",
            available=frozenset({Capability.IDENTITY_SEARCH}),
            registry_version="test",
        )

    def retrieve(self, capability, query, upstream_evidence, top_k):
        del query, upstream_evidence, top_k
        if capability != Capability.IDENTITY_SEARCH:
            raise AssertionError(f"unexpected gateway capability: {capability.value}")
        return RetrievalBatch(
            evidence=[
                Evidence(
                    evidence_id="identity:domestic_etf:101",
                    capability=Capability.IDENTITY_SEARCH,
                    source_id="test_identity",
                    content="키움 미국S&P500",
                    product_id="101",
                    product_type=ProductType.DOMESTIC_ETF,
                    source_ref="test#identity",
                    structured={
                        "name": "키움 미국S&P500",
                        "matched_terms": ["키움 미국S&P500"],
                    },
                )
            ],
            coverage_complete=True,
            total_hits=1,
        )


class RetrieverRegistryTest(unittest.TestCase):
    def test_duplicate_registration_is_rejected_without_replace(self):
        registry = RetrieverRegistry()
        original = StaticRetriever({})
        registry.register(Capability.HOLDING_SEARCH, original)

        with self.assertRaisesRegex(ValueError, "holding_search"):
            registry.register(Capability.HOLDING_SEARCH, StaticRetriever({}))

        self.assertIs(registry.get(Capability.HOLDING_SEARCH), original)

    def test_duplicate_registration_can_be_explicitly_replaced(self):
        registry = RetrieverRegistry()
        original = StaticRetriever({})
        replacement = StaticRetriever({})
        registry.register(Capability.DOCUMENT_SEARCH, original)

        registry.register(
            Capability.DOCUMENT_SEARCH,
            replacement,
            replace=True,
        )

        self.assertIs(registry.get(Capability.DOCUMENT_SEARCH), replacement)


class PipelineRetrieverWiringTest(unittest.TestCase):
    def setUp(self):
        self.holding_fetcher = RecordingFetcher(
            [
                {
                    "source_record_key": "holding-1",
                    "product_id": 101,
                    "product_type": "DOMESTIC_ETP",
                    "product_name": "키움 미국S&P500",
                    "holding_key": "AAPL",
                    "holding_name": "Apple",
                    "weight_pct": 8.5,
                    "as_of_date": "2026-08-31",
                    "source_name": "test",
                }
            ]
        )
        self.document_fetcher = RecordingFetcher(
            [
                {
                    "source_record_key": "document-1",
                    "product_id": 101,
                    "product_name": "키움 미국S&P500",
                    "source_product_key": "449770",
                    "section_type": "strategy",
                    "heading": "투자전략",
                    "chunk_text": "미국 S&P 500 지수를 추종합니다.",
                    "rcept_dt": "2026-08-24",
                    "source_url": "https://example.test/prospectus",
                    "relevance": 1.0,
                }
            ]
        )

    def _pipeline(self):
        def register_ext(registry):
            return register_ext_retrievers(
                registry,
                fetcher=self.holding_fetcher,
            )

        def register_document(registry):
            return register_prospectus_retriever(
                registry,
                fetcher=self.document_fetcher,
            )

        with patch.object(
            b_adapter,
            "register_ext_retrievers",
            side_effect=register_ext,
        ), patch.object(
            b_adapter,
            "register_prospectus_retriever",
            side_effect=register_document,
        ):
            return b_adapter._build_pipeline(
                IdentityGateway(),
                llm_client=object(),
            )

    def test_build_pipeline_selects_postgres_retrievers(self):
        pipeline = self._pipeline()

        self.assertIsInstance(
            pipeline.registry.get(Capability.HOLDING_SEARCH),
            PostgresHoldingRetriever,
        )
        self.assertIsInstance(
            pipeline.registry.get(Capability.DOCUMENT_SEARCH),
            PostgresProspectusRetriever,
        )

    def test_answer_path_executes_holding_and_prospectus_retrievers(self):
        pipeline = self._pipeline()

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            holding_result = b_adapter.answer_question(
                "registry-holding",
                "키움 미국S&P500 보유종목 알려줘",
            )
            document_result = b_adapter.answer_question(
                "registry-document",
                "키움 미국S&P500 투자전략 알려줘",
            )

        self.assertIn(
            "holding_search:success(1)",
            holding_result.think_trace[1].summary,
        )
        self.assertIn(
            "document_search:success(1)",
            document_result.think_trace[1].summary,
        )
        self.assertEqual(len(self.holding_fetcher.calls), 1)
        self.assertEqual(len(self.document_fetcher.calls), 1)


if __name__ == "__main__":
    unittest.main()
