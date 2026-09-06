import unittest

from b_agent.models import Capability, Intent, ProductType
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.planner import RetrievalPlanner


class KoreanProductNameTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()
        self.planner = RetrievalPlanner()

    def test_korean_kiwoom_name_is_extracted_without_question_suffix(self):
        parsed = self.analyzer.analyze(
            "키움 미국S&P500 투자전략이랑 위험등급 알려줘"
        )

        self.assertEqual(parsed.product_mentions, ["키움 미국S&P500"])
        self.assertEqual(parsed.product_types, [ProductType.DOMESTIC_ETF])
        self.assertIn(Intent.STRATEGY, parsed.intents)
        self.assertIn(Intent.RISK, parsed.intents)

    def test_product_identity_runs_before_vector_search(self):
        parsed = self.analyzer.analyze(
            "키움 미국S&P500 투자전략이랑 위험등급 알려줘"
        )
        plan = self.planner.build(parsed)
        by_capability = {step.capability: step for step in plan.steps}

        self.assertIn(Capability.IDENTITY_SEARCH, by_capability)
        self.assertLess(
            [step.capability for step in plan.steps].index(Capability.IDENTITY_SEARCH),
            [step.capability for step in plan.steps].index(Capability.VECTOR_SEARCH),
        )
        self.assertEqual(
            by_capability[Capability.VECTOR_SEARCH].depends_on,
            ["identity_search"],
        )

    def test_english_kiwoom_brand_is_supported(self):
        parsed = self.analyzer.analyze("KIWOOM 미국S&P500 운용전략 설명해줘")

        self.assertEqual(parsed.product_mentions, ["KIWOOM 미국S&P500"])
        self.assertEqual(parsed.product_types, [ProductType.DOMESTIC_ETF])

    def test_possessive_product_name_is_extracted_before_strategy_question(self):
        parsed = self.analyzer.analyze("키움 미국S&P500의 투자전략은?")

        self.assertEqual(parsed.product_mentions, ["키움 미국S&P500"])
        self.assertIn(Intent.STRATEGY, parsed.intents)

    def test_punctuated_risk_question_keeps_exact_product_name(self):
        parsed = self.analyzer.analyze("키움 미국S&P500의 위험 등급은?")

        self.assertEqual(parsed.product_mentions, ["키움 미국S&P500"])
        self.assertIn(Intent.RISK, parsed.intents)


if __name__ == "__main__":
    unittest.main()
