import json
import unittest

from b_agent.fusion import EvidenceFusion
from b_agent.models import (
    Capability,
    Evidence,
    Intent,
    Operator,
    ProductType,
    SortDirection,
)
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.ontology import canonical_region, database_region_values
from b_agent.pipeline import BAgentPipeline
from b_agent.planner import RetrievalPlanner
from b_agent.retrievers import (
    BM25KeywordRetriever,
    InMemoryStructuredRetriever,
    RetrievalContext,
    RetrieverRegistry,
    SearchDocument,
    StaticRetriever,
)


class RegressionTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()
        self.planner = RetrievalPlanner()

    def test_domestic_listed_us_etf_separates_listing_market_from_region(self):
        parsed = self.analyzer.analyze("국내 상장 미국 ETF를 알려줘")

        self.assertEqual(parsed.product_types, [ProductType.DOMESTIC_ETF])
        self.assertIn(
            ("investment_region", Operator.CONTAINS, "미국"),
            {(item.field, item.operator, item.value) for item in parsed.filters},
        )

    def test_us_listed_global_etf_separates_listing_market_from_region(self):
        parsed = self.analyzer.analyze("미국 상장 글로벌 ETF를 알려줘")

        self.assertEqual(parsed.product_types, [ProductType.FOREIGN_ETF])
        self.assertIn(
            ("investment_region", Operator.CONTAINS, "글로벌"),
            {(item.field, item.operator, item.value) for item in parsed.filters},
        )

    def test_region_vocabulary_maps_question_and_database_labels_consistently(self):
        self.assertEqual(canonical_region("미국"), "미국")
        self.assertEqual(canonical_region("U.S."), "미국")
        self.assertEqual(canonical_region("United States of America"), "미국")
        self.assertEqual(canonical_region("Global Ex US"), "글로벌")
        self.assertIn("United States of America", database_region_values("미국"))
        self.assertNotIn("Global Ex US", database_region_values("미국"))

    def test_foreign_etf_region_strategy_uses_vector_candidates(self):
        parsed = self.analyzer.analyze(
            "미국 성장주에 집중 투자하는 해외 ETF 전략 알려줘"
        )
        plan = self.planner.build(parsed)
        by_capability = {step.capability: step for step in plan.steps}

        self.assertFalse(by_capability[Capability.KEYWORD_SEARCH].required)
        self.assertTrue(by_capability[Capability.VECTOR_SEARCH].required)
        self.assertEqual(
            by_capability[Capability.STRUCTURED_SEARCH].depends_on,
            ["vector_search"],
        )

    def test_region_matching_uses_semantic_category_not_substring(self):
        records = [
            {
                "product_id": "US",
                "product_type": "foreign_etf",
                "name": "미국 ETF",
                "investment_region": "United States of America",
                "source_ref": "test.xlsx#row=1",
            },
            {
                "product_id": "GLOBAL-EX-US",
                "product_type": "foreign_etf",
                "name": "미국 제외 글로벌 ETF",
                "investment_region": "Global Ex US",
                "source_ref": "test.xlsx#row=2",
            },
        ]
        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            InMemoryStructuredRetriever(records),
        )
        context = json.loads(
            BAgentPipeline(registry=registry)
            .run("region", "미국 해외 ETF 알려줘")
            .retrieved_context
        )

        self.assertEqual([item["product_id"] for item in context], ["US"])

    def test_recent_one_year_return_is_metric_not_recent_event(self):
        parsed = self.analyzer.analyze("최근 1년 수익률 높은 국내 ETF 5개")
        plan = self.planner.build(parsed)

        self.assertNotIn(Intent.RECENT_EVENT, parsed.intents)
        self.assertIsNone(parsed.temporal)
        self.assertEqual(parsed.limit, 5)
        self.assertEqual(len(parsed.sorts), 1)
        self.assertEqual(parsed.sorts[0].field, "return_1y")
        self.assertEqual(parsed.sorts[0].direction, SortDirection.DESC)
        self.assertNotIn(Capability.EVENT_SEARCH, plan.required_capabilities)
        self.assertEqual(
            [step.capability for step in plan.steps],
            [Capability.STRUCTURED_SEARCH],
        )

    def test_ot_ecopro_question_builds_full_federated_plan(self):
        question = (
            "에코프로의 상장 자회사를 편입한 ETF 중, 순자산 1,000억 원 이상이고 "
            "리서치 의견이 긍정적인 상품 추천"
        )
        parsed = self.analyzer.analyze(question)
        plan = self.planner.build(parsed)
        by_capability = {step.capability: step for step in plan.steps}
        aum_filter = next(
            (item for item in parsed.filters if item.field == "net_assets"),
            None,
        )
        research_steps = [
            step
            for step in plan.steps
            if step.capability
            in {Capability.DOCUMENT_SEARCH, Capability.VECTOR_SEARCH}
            and step.required
        ]

        actual = {
            "entities": parsed.entities,
            "has_relation": Capability.RELATION_SEARCH in by_capability,
            "relation_predicates": by_capability[
                Capability.RELATION_SEARCH
            ].query.get("relation_predicates"),
            "listed_only": by_capability[Capability.RELATION_SEARCH].query.get(
                "listed_only"
            ),
            "has_holdings": Capability.HOLDING_SEARCH in by_capability,
            "holding_depends_on": by_capability[
                Capability.HOLDING_SEARCH
            ].depends_on,
            "structured_depends_on": by_capability[
                Capability.STRUCTURED_SEARCH
            ].depends_on,
            "aum_operator": aum_filter.operator if aum_filter else None,
            "aum_value": aum_filter.value if aum_filter else None,
            "has_required_research_step": bool(research_steps),
        }
        self.assertEqual(
            actual,
            {
                "entities": ["에코프로"],
                "has_relation": True,
                "relation_predicates": ["subsidiaryOf"],
                "listed_only": True,
                "has_holdings": True,
                "holding_depends_on": ["relation_search"],
                "structured_depends_on": ["holding_search"],
                "aum_operator": Operator.GTE,
                "aum_value": 100_000_000_000,
                "has_required_research_step": True,
            },
        )

    def test_incomplete_coverage_empty_is_not_complete_no_match(self):
        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            StaticRetriever({}, coverage_complete=False),
        )

        payload = BAgentPipeline(registry=registry).run(
            "coverage-empty",
            "국내 ETF를 알려줘",
        ).to_dict()

        self.assertNotIn("상태=complete", payload["think_trace"])
        self.assertNotIn("사유=no_match", payload["think_trace"])
        self.assertIn("상태=unanswerable", payload["think_trace"])
        self.assertIn("required_field_missing", payload["think_trace"])

    def test_null_sort_values_are_last_for_both_directions(self):
        records = [
            {
                "product_id": "D-LOW",
                "product_type": "domestic_etf",
                "name": "낮은 수익률 ETF",
                "return_1y": 1.0,
                "source_ref": "domestic-etf.xlsx#row=2",
            },
            {
                "product_id": "D-NULL",
                "product_type": "domestic_etf",
                "name": "수익률 결측 ETF",
                "return_1y": None,
                "source_ref": "domestic-etf.xlsx#row=3",
            },
            {
                "product_id": "D-HIGH",
                "product_type": "domestic_etf",
                "name": "높은 수익률 ETF",
                "return_1y": 5.0,
                "source_ref": "domestic-etf.xlsx#row=4",
            },
        ]
        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            InMemoryStructuredRetriever(records),
        )
        pipeline = BAgentPipeline(registry=registry)

        descending = json.loads(
            pipeline.run(
                "sort-desc",
                "최근 1년 수익률 높은 국내 ETF 3개",
            ).retrieved_context
        )
        ascending = json.loads(
            pipeline.run(
                "sort-asc",
                "최근 1년 수익률 낮은 국내 ETF 3개",
            ).retrieved_context
        )

        self.assertEqual(
            [item["product_id"] for item in descending],
            ["D-HIGH", "D-LOW", "D-NULL"],
        )
        self.assertEqual(
            [item["product_id"] for item in ascending],
            ["D-LOW", "D-HIGH", "D-NULL"],
        )

    def test_mixed_domestic_foreign_aum_comparison_is_blocked(self):
        question = "ETF를 순자산 큰 순으로 알려줘"
        plan = BAgentPipeline().plan(question)

        self.assertEqual(
            set(plan.understanding.product_types),
            {ProductType.DOMESTIC_ETF, ProductType.FOREIGN_ETF},
        )
        self.assertTrue(any("통화" in blocker for blocker in plan.blockers))

        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            InMemoryStructuredRetriever(
                [
                    {
                        "product_id": "D1",
                        "product_type": "domestic_etf",
                        "name": "국내 ETF",
                        "net_assets": 1_000_000_000_000,
                        "source_ref": "domestic-etf.xlsx#row=2",
                    },
                    {
                        "product_id": "F1",
                        "product_type": "foreign_etf",
                        "name": "해외 ETF",
                        "net_assets": 5_000_000_000,
                        "source_ref": "foreign-etf.xlsx#row=2",
                    },
                ]
            ),
        )
        payload = BAgentPipeline(registry=registry).run("mixed-aum", question).to_dict()
        self.assertIn("상태=unanswerable", payload["think_trace"])
        self.assertIn("unsupported_query", payload["think_trace"])

    def test_blank_pipeline_returns_exactly_five_string_fields(self):
        payload = BAgentPipeline().run(17, "   ").to_dict()

        self.assertEqual(
            set(payload),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in payload.values()))
        self.assertEqual(payload["question_id"], "17")
        self.assertEqual(payload["question"], "")
        self.assertEqual(json.loads(payload["retrieved_context"]), [])

    def test_fusion_limit_preserves_required_document_evidence(self):
        structured = [
            Evidence(
                evidence_id=f"structured:{index:03d}",
                capability=Capability.STRUCTURED_SEARCH,
                source_id="products",
                source_ref=f"products.xlsx#row={index + 2}",
                product_id=f"P-{index:03d}",
                content=f"구조화 상품 {index}",
                score=100.0 - index,
                structured={"name": f"상품 {index}"},
            )
            for index in range(60)
        ]
        document = Evidence(
            evidence_id="document:critical-risk",
            capability=Capability.DOCUMENT_SEARCH,
            source_id="prospectus",
            source_ref="prospectus.pdf#page=12",
            product_id="P-000",
            content="필수 위험 문서 근거",
            score=0.01,
        )

        fused = EvidenceFusion().fuse(
            [*structured, document],
            required_capabilities=[
                Capability.STRUCTURED_SEARCH,
                Capability.DOCUMENT_SEARCH,
            ],
            limit=50,
        )

        self.assertEqual(len(fused), 50)
        self.assertIn("document:critical-risk", {item.evidence_id for item in fused})
        self.assertEqual(
            {Capability.STRUCTURED_SEARCH, Capability.DOCUMENT_SEARCH},
            {item.capability for item in fused},
        )

    def test_bm25_does_not_return_generic_unrelated_document(self):
        documents = [
            SearchDocument(
                document_id="semiconductor",
                text="반도체 장비와 semiconductor chip 기업에 투자하는 ETF",
                source_id="foreign_etf_master",
                product_id="F-SEMICONDUCTOR",
                product_type=ProductType.FOREIGN_ETF,
                source_ref="foreign-etf.xlsx#row=2",
            ),
            SearchDocument(
                document_id="generic-overseas",
                text="해외 상장 ETF의 일반적인 투자 전략과 순자산 정보",
                source_id="foreign_etf_master",
                product_id="F-UNRELATED",
                product_type=ProductType.FOREIGN_ETF,
                source_ref="foreign-etf.xlsx#row=3",
            ),
        ]
        plan = self.planner.build(
            self.analyzer.analyze("반도체 관련 해외 ETF 전략을 알려줘")
        )
        keyword_step = next(
            step for step in plan.steps if step.capability == Capability.KEYWORD_SEARCH
        )

        batch = BM25KeywordRetriever(documents).retrieve(
            keyword_step,
            RetrievalContext(),
        )

        self.assertEqual(
            [item.product_id for item in batch.evidence],
            ["F-SEMICONDUCTOR"],
        )


if __name__ == "__main__":
    unittest.main()
