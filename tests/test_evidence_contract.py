import unittest
from pathlib import Path

from b_agent.evidence_validation import EvidenceValidator
from b_agent.models import (
    Capability,
    Evidence,
    ProductType,
    RetrievalStep,
    StepOutcome,
    StepResult,
)
from b_agent.ontology import DEFAULT_ONTOLOGY
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.pipeline import BAgentPipeline
from b_agent.planner import RetrievalPlanner
from b_agent.retrievers import (
    BM25KeywordRetriever,
    InMemoryIdentityRetriever,
    InMemoryStructuredRetriever,
    RetrievalContext,
    RetrieverRegistry,
    SearchDocument,
    StaticRetriever,
)
from b_agent.relation_results import relation_result_role


class EvidenceContractTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()

    def test_snapshot_date_resolves_relative_period(self):
        plan = RetrievalPlanner(snapshot_date="2026-08-31").build(
            self.analyzer.analyze("최근 6개월 우주항공 관련 ETF")
        )
        event = next(step for step in plan.steps if step.capability == Capability.EVENT_SEARCH)
        self.assertEqual(event.query["temporal"]["start_date"], "2026-02-28")
        self.assertEqual(event.query["temporal"]["end_date"], "2026-08-31")
        self.assertFalse(any("스냅샷" in blocker for blocker in plan.blockers))

    def test_relative_period_without_snapshot_is_blocked(self):
        plan = RetrievalPlanner().build(
            self.analyzer.analyze("최근 6개월 우주항공 관련 ETF")
        )
        self.assertTrue(any("스냅샷" in blocker for blocker in plan.blockers))

    def test_standalone_tickers_and_product_names_are_extracted(self):
        ticker_query = self.analyzer.analyze("SPY와 QQQ 비교해줘")
        name_query = self.analyzer.analyze("KODEX 200과 TIGER 200 비교")
        self.assertEqual(ticker_query.identifiers, ["SPY", "QQQ"])
        self.assertEqual(name_query.product_mentions, ["KODEX 200", "TIGER 200"])

    def test_identity_search_resolves_exact_tickers(self):
        records = [
            {
                "product_id": "US-SPY",
                "product_type": "foreign_etf",
                "name": "SPDR S&P 500 ETF Trust",
                "ticker": "SPY",
                "source_ref": "foreign.xlsx#row=2",
            },
            {
                "product_id": "US-QQQ",
                "product_type": "foreign_etf",
                "name": "Invesco QQQ Trust",
                "ticker": "QQQ",
                "source_ref": "foreign.xlsx#row=3",
            },
        ]
        plan = RetrievalPlanner().build(self.analyzer.analyze("SPY와 QQQ 비교해줘"))
        step = next(item for item in plan.steps if item.capability == Capability.IDENTITY_SEARCH)
        batch = InMemoryIdentityRetriever(records).retrieve(step, RetrievalContext())
        self.assertEqual({item.product_id for item in batch.evidence}, {"US-SPY", "US-QQQ"})
        self.assertFalse(batch.ambiguous)

    def test_missing_sort_field_makes_answer_partial(self):
        records = [
            {
                "product_id": "D1",
                "product_type": "domestic_etf",
                "name": "수익률 있음",
                "return_1y": 3.0,
                "source_ref": "domestic.xlsx#row=2",
            },
            {
                "product_id": "D2",
                "product_type": "domestic_etf",
                "name": "수익률 없음",
                "return_1y": None,
                "source_ref": "domestic.xlsx#row=3",
            },
        ]
        registry = RetrieverRegistry()
        registry.register(Capability.STRUCTURED_SEARCH, InMemoryStructuredRetriever(records))
        payload = BAgentPipeline(registry=registry).run(
            "coverage", "최근 1년 수익률 높은 국내 ETF 2개"
        )
        self.assertIn("상태=partial", payload.think_trace)
        self.assertIn("return_1y 1/2건 결측", payload.think_trace)

    def test_foreign_native_aum_filter_is_blocked(self):
        plan = RetrievalPlanner().build(
            self.analyzer.analyze("순자산 1,000억원 이상 해외 ETF")
        )
        self.assertTrue(any("원 통화" in blocker for blocker in plan.blockers))

    def test_relation_direction_and_listed_constraint_are_enforced(self):
        plan = RetrievalPlanner().build(
            self.analyzer.analyze("에코프로의 상장 자회사를 편입한 ETF")
        )
        step = next(item for item in plan.steps if item.capability == Capability.RELATION_SEARCH)
        wrong = Evidence(
            evidence_id="relation:wrong",
            capability=Capability.RELATION_SEARCH,
            source_id="relations",
            source_ref="relations.csv#row=2",
            content="방향이 반대인 비상장 관계",
            structured={
                "predicate": "subsidiaryOf",
                "source_entity_name": "에코프로",
                "target_entity_name": "비상장회사",
                "source_listed": False,
            },
        )
        valid, errors = EvidenceValidator().validate(step, [wrong])
        self.assertEqual(valid, [])
        self.assertEqual(len(errors), 1)

    def test_holding_validator_accepts_opposite_endpoint_for_both_anchor_sides(self):
        constraint = {
            "predicate": "affiliateOf",
            "anchor": "에코프로",
            "anchor_role": "either",
            "result_role": "opposite",
            "result_listed": True,
        }
        step = RetrievalStep(
            step_id="holding_search",
            capability=Capability.HOLDING_SEARCH,
            query={
                "product_types": ["domestic_etf"],
                "holding_targets": [],
                "relation_constraints": [constraint],
            },
            depends_on=["relation_search"],
        )
        rows = [
            {
                "source_entity_name": "에코프로",
                "target_entity_name": "에코프로비엠",
                "target_listed": True,
            },
            {
                "source_entity_name": "에코프로비엠",
                "target_entity_name": "에코프로",
                "source_listed": True,
            },
        ]

        for index, relation_values in enumerate(rows):
            with self.subTest(anchor_side=index):
                relation = Evidence(
                    evidence_id=f"relation:{index}",
                    capability=Capability.RELATION_SEARCH,
                    source_id="relations",
                    content="에코프로 계열사 관계",
                    source_ref=f"relations#{index}",
                    structured={"predicate": "affiliateOf", **relation_values},
                )
                holding = Evidence(
                    evidence_id=f"holding:{index}",
                    capability=Capability.HOLDING_SEARCH,
                    source_id="holdings",
                    content="ETF가 에코프로비엠을 편입",
                    product_id="ETF-1",
                    product_type=ProductType.DOMESTIC_ETF,
                    source_ref=f"holdings#{index}",
                    structured={"holding_name": "에코프로비엠"},
                )
                prior = {
                    "relation_search": StepResult(
                        step_id="relation_search",
                        capability=Capability.RELATION_SEARCH,
                        outcome=StepOutcome.SUCCESS,
                        evidence=[relation],
                    )
                }

                relation_step = RetrievalStep(
                    step_id="relation_search",
                    capability=Capability.RELATION_SEARCH,
                    query={"relation_constraints": [constraint]},
                )
                valid_relations, relation_errors = EvidenceValidator().validate(
                    relation_step, [relation]
                )
                valid_holdings, holding_errors = EvidenceValidator().validate(
                    step, [holding], prior
                )

                self.assertEqual(valid_relations, [relation])
                self.assertEqual(relation_errors, [])
                self.assertEqual(valid_holdings, [holding])
                self.assertEqual(holding_errors, [])

    def test_opposite_relation_without_anchor_is_unresolved(self):
        self.assertIsNone(
            relation_result_role(
                {
                    "source_entity_name": "에코프로",
                    "target_entity_name": "에코프로비엠",
                },
                {"result_role": "opposite"},
            )
        )

    def test_concept_document_does_not_require_product_row(self):
        plan = RetrievalPlanner(snapshot_date="2026-08-31").build(
            self.analyzer.analyze("국민성장펀드 구조를 설명해줘")
        )
        step = next(item for item in plan.steps if item.capability == Capability.DOCUMENT_SEARCH)
        document = Evidence(
            evidence_id="document:growth-fund",
            capability=Capability.DOCUMENT_SEARCH,
            source_id="policy_document",
            source_ref="policy.pdf#page=3",
            content="국민성장펀드의 조성 구조",
            structured={"concept_id": "national-growth-fund", "section": "structure"},
        )
        valid, errors = EvidenceValidator().validate(step, [document])
        self.assertEqual(valid, [document])
        self.assertEqual(errors, [])

    def test_bm25_reports_truncation_for_downstream_filters(self):
        documents = [
            SearchDocument(
                document_id=f"D{index}",
                text="반도체 semiconductor ETF",
                source_id="foreign",
                source_ref=f"foreign.xlsx#row={index + 2}",
                product_id=f"D{index}",
                product_type=ProductType.FOREIGN_ETF,
            )
            for index in range(5)
        ]
        plan = RetrievalPlanner().build(
            self.analyzer.analyze("반도체 해외 ETF 중 보수율 1% 이상 1개")
        )
        step = next(item for item in plan.steps if item.capability == Capability.KEYWORD_SEARCH)
        step.top_k = 2
        batch = BM25KeywordRetriever(documents).retrieve(step, RetrievalContext())
        self.assertTrue(batch.truncated)
        self.assertFalse(batch.coverage_complete)
        self.assertEqual(batch.total_hits, 5)

    def test_checked_in_ontology_matches_runtime_registry(self):
        path = Path(__file__).resolve().parents[1] / "ontology.ttl"
        self.assertEqual(path.read_text(encoding="utf-8"), DEFAULT_ONTOLOGY.render_ttl())

    def test_full_ot_chain_can_complete_when_all_evidence_is_bound(self):
        question = (
            "에코프로의 상장 자회사를 편입한 국내 ETF 중 순자산 1,000억원 이상이고 "
            "리서치 의견이 긍정적인 상품 추천"
        )
        evidence = {
            Capability.RELATION_SEARCH: [
                Evidence(
                    "relation:1",
                    Capability.RELATION_SEARCH,
                    "relations",
                    "에코프로비엠은 에코프로의 상장 자회사",
                    source_ref="relations#1",
                    structured={
                        "predicate": "subsidiaryOf",
                        "source_entity_name": "에코프로비엠",
                        "target_entity_name": "에코프로",
                        "source_listed": True,
                    },
                )
            ],
            Capability.HOLDING_SEARCH: [
                Evidence(
                    "holding:1",
                    Capability.HOLDING_SEARCH,
                    "holdings",
                    "ETF-1이 에코프로비엠을 편입",
                    product_id="ETF-1",
                    product_type=ProductType.DOMESTIC_ETF,
                    source_ref="holdings#1",
                    structured={"holding_name": "에코프로비엠"},
                )
            ],
            Capability.STRUCTURED_SEARCH: [
                Evidence(
                    "structured:1",
                    Capability.STRUCTURED_SEARCH,
                    "products",
                    "ETF-1 순자산 2,000억원",
                    product_id="ETF-1",
                    product_type=ProductType.DOMESTIC_ETF,
                    source_ref="products#1",
                    structured={"name": "검증 ETF", "net_assets": 200_000_000_000},
                )
            ],
            Capability.DOCUMENT_SEARCH: [
                Evidence(
                    "document:1",
                    Capability.DOCUMENT_SEARCH,
                    "research",
                    "검증 ETF에 대한 긍정 의견",
                    product_id="ETF-1",
                    product_type=ProductType.DOMESTIC_ETF,
                    source_ref="research#1",
                    structured={"section": "research_opinion", "sentiment": "positive"},
                )
            ],
        }
        registry = RetrieverRegistry()
        for capability in evidence:
            registry.register(capability, StaticRetriever(evidence))
        payload = BAgentPipeline(registry=registry).run("ot", question)
        self.assertIn("상태=complete", payload.think_trace)
        self.assertIn("검증 ETF", payload.answer)

    def test_complete_upstream_zero_is_no_match_even_if_downstream_data_is_absent(self):
        question = "에코프로의 상장 자회사를 편입한 국내 ETF의 위험요인"
        registry = RetrieverRegistry()
        registry.register(
            Capability.RELATION_SEARCH,
            StaticRetriever({Capability.RELATION_SEARCH: []}, coverage_complete=True),
        )
        payload = BAgentPipeline(registry=registry).run("none", question)
        self.assertIn("상태=complete", payload.think_trace)
        self.assertIn("no_match", payload.think_trace)


if __name__ == "__main__":
    unittest.main()
