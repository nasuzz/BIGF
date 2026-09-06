import unittest

from b_agent.models import (
    AnswerStatus,
    Capability,
    Evidence,
    Intent,
    Operator,
    ProductType,
    QueryUnderstanding,
    RetrievalBatch,
    SortDirection,
)
from b_agent.gateway import CapabilitySnapshot, registry_from_gateway
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.pipeline import BAgentPipeline
from b_agent.planner import RetrievalPlanner


class ParserPlannerTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()
        self.planner = RetrievalPlanner()

    def test_bond_filters_and_sort(self):
        parsed = self.analyzer.analyze(
            "원화 표시이며 매수 가능한 AA- 이상 채권을 수익률 높은 순으로 5개 추천"
        )
        self.assertEqual(parsed.product_types, [ProductType.BOND])
        filters = {(item.field, item.operator, item.value) for item in parsed.filters}
        self.assertIn(("currency", Operator.EQ, "KRW"), filters)
        self.assertIn(("sale_available", Operator.EQ, True), filters)
        self.assertIn(("credit_rating", Operator.CREDIT_AT_LEAST, "AA-"), filters)
        self.assertEqual(parsed.sorts[0].field, "yield")
        self.assertEqual(parsed.sorts[0].direction, SortDirection.DESC)
        self.assertEqual(parsed.limit, 5)

        plan = self.planner.build(parsed)
        self.assertEqual([step.capability for step in plan.steps], [Capability.STRUCTURED_SEARCH])

    def test_korean_money_conversion(self):
        parsed = self.analyzer.analyze("순자산 1,000억원 이상인 국내 ETF 3개")
        aum = next(item for item in parsed.filters if item.field == "net_assets")
        self.assertEqual(aum.value, 100_000_000_000)
        self.assertEqual(aum.operator, Operator.GTE)

    def test_relation_holding_document_chain(self):
        parsed = self.analyzer.analyze(
            "에코프로 자회사를 편입한 ETF를 순자산 큰 순으로 정렬하고 위험요인을 설명해줘"
        )
        self.assertEqual(parsed.entities, ["에코프로"])
        self.assertIn(Intent.RELATION, parsed.intents)
        self.assertIn(Intent.HOLDINGS, parsed.intents)
        self.assertIn(Intent.RISK, parsed.intents)

        plan = self.planner.build(parsed)
        by_capability = {step.capability: step for step in plan.steps}
        self.assertEqual(
            by_capability[Capability.HOLDING_SEARCH].depends_on,
            ["relation_search"],
        )
        self.assertEqual(
            by_capability[Capability.STRUCTURED_SEARCH].depends_on,
            ["holding_search"],
        )
        self.assertTrue(by_capability[Capability.DOCUMENT_SEARCH].required)
        self.assertTrue(any("직접 비교" in warning for warning in plan.warnings))

    def test_named_etf_holdings_depend_on_identity_resolution(self):
        understanding = QueryUnderstanding(
            question="TIGER 미국S&P500의 보유종목",
            product_types=[ProductType.DOMESTIC_ETF],
            intents=[Intent.HOLDINGS],
            product_mentions=["TIGER 미국S&P500"],
        )

        plan = self.planner.build(understanding)
        holding = next(
            step
            for step in plan.steps
            if step.capability == Capability.HOLDING_SEARCH
        )

        self.assertEqual(holding.depends_on, ["identity_search"])

    def test_named_etf_holding_question_runs_analyzer_to_final_answer(self):
        class NamedEtfGateway:
            def __init__(self):
                self.calls = []

            def capability_snapshot(self):
                return CapabilitySnapshot(
                    snapshot_id="named-etf-test",
                    available=frozenset(
                        {
                            Capability.IDENTITY_SEARCH,
                            Capability.HOLDING_SEARCH,
                            Capability.STRUCTURED_SEARCH,
                        }
                    ),
                    registry_version="named-etf-test",
                )

            def retrieve(self, capability, query, upstream_evidence, top_k):
                del top_k
                self.calls.append((capability, query, upstream_evidence))
                if capability == Capability.IDENTITY_SEARCH:
                    return RetrievalBatch(
                        evidence=[
                            Evidence(
                                evidence_id="identity:domestic_etf:101",
                                capability=capability,
                                source_id="products",
                                content="TIGER 미국S&P500",
                                product_id="101",
                                product_type=ProductType.DOMESTIC_ETF,
                                source_ref="products#101",
                                structured={
                                    "name": "TIGER 미국S&P500",
                                    "matched_terms": ["TIGER 미국S&P500"],
                                },
                            )
                        ],
                        coverage_complete=True,
                        total_hits=1,
                    )
                if capability == Capability.HOLDING_SEARCH:
                    return RetrievalBatch(
                        evidence=[
                            Evidence(
                                evidence_id="holding:101:AAPL",
                                capability=capability,
                                source_id="holdings",
                                content="TIGER 미국S&P500이 Apple을 보유합니다.",
                                product_id="101",
                                product_type=ProductType.DOMESTIC_ETF,
                                source_ref="holdings#101:AAPL",
                                structured={"holding_id": "AAPL", "holding_name": "Apple"},
                            )
                        ],
                        coverage_complete=True,
                        total_hits=1,
                    )
                return RetrievalBatch(
                    evidence=[
                        Evidence(
                            evidence_id="structured:domestic_etf:101",
                            capability=capability,
                            source_id="products",
                            content="TIGER 미국S&P500",
                            product_id="101",
                            product_type=ProductType.DOMESTIC_ETF,
                            source_ref="products#101",
                            structured={"name": "TIGER 미국S&P500"},
                        )
                    ],
                    coverage_complete=True,
                    total_hits=1,
                )

        gateway = NamedEtfGateway()
        execution = BAgentPipeline(registry=registry_from_gateway(gateway)).execute(
            "named-etf-holdings",
            "TIGER 미국S&P500의 보유종목을 알려줘",
        )

        self.assertEqual(execution.assessment.status, AnswerStatus.COMPLETE)
        self.assertEqual(
            [capability for capability, _, _ in gateway.calls],
            [
                Capability.IDENTITY_SEARCH,
                Capability.HOLDING_SEARCH,
                Capability.STRUCTURED_SEARCH,
            ],
        )
        identity_query = gateway.calls[0][1]
        holding_query = gateway.calls[1][1]
        self.assertEqual(identity_query["product_mentions"], ["TIGER 미국S&P500"])
        self.assertEqual(identity_query["identifiers"], [])
        self.assertEqual(holding_query["holding_targets"], [])
        self.assertEqual(
            holding_query["product_mentions"], ["TIGER 미국S&P500"]
        )
        self.assertEqual(
            gateway.calls[1][2]["identity_search"][0].product_id, "101"
        )
        self.assertIn("TIGER 미국S&P500", execution.answer)

    def test_recent_theme_requires_event(self):
        parsed = self.analyzer.analyze("최근 6개월 우주항공 관련 ETF를 알려줘")
        self.assertEqual(parsed.temporal.relative_months, 6)
        self.assertEqual(parsed.limit, 10)
        self.assertIn("space_aerospace", parsed.themes)
        plan = self.planner.build(parsed)
        event = next(step for step in plan.steps if step.capability == Capability.EVENT_SEARCH)
        self.assertTrue(event.required)

    def test_unspecified_trend_period_gets_documented_default(self):
        parsed = self.analyzer.analyze("국민성장펀드 구조와 최근 트렌드를 설명해줘")
        self.assertEqual(parsed.temporal.relative_months, 6)
        self.assertTrue(any("기본값" in warning for warning in parsed.warnings))


if __name__ == "__main__":
    unittest.main()
