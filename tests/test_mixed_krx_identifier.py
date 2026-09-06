import unittest

from b_agent.models import Capability, Intent
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.planner import RetrievalPlanner


class MixedKrxIdentifierTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()
        self.planner = RetrievalPlanner()

    def test_mixed_krx_ticker_plans_identity_before_holding_search(self):
        parsed = self.analyzer.analyze("0007F0 보유종목 알려줘")

        self.assertEqual(parsed.identifiers, ["0007F0"])
        self.assertIn(Intent.HOLDINGS, parsed.intents)

        plan = self.planner.build(parsed)
        by_capability = {step.capability: step for step in plan.steps}
        self.assertIn(Capability.IDENTITY_SEARCH, by_capability)
        self.assertEqual(
            by_capability[Capability.HOLDING_SEARCH].depends_on,
            ["identity_search"],
        )

    def test_mixed_krx_ticker_is_case_insensitive(self):
        parsed = self.analyzer.analyze("0007f0 보유종목 알려줘")

        self.assertEqual(parsed.identifiers, ["0007F0"])

    def test_legacy_numeric_krx_ticker_is_still_recognized(self):
        parsed = self.analyzer.analyze("449770 보유종목 알려줘")

        self.assertEqual(parsed.identifiers, ["449770"])

    def test_plain_six_letter_word_is_not_a_krx_ticker(self):
        self.assertEqual(self.analyzer._identifiers("GLOBAL"), [])

    def test_embedded_six_character_fragment_is_not_a_krx_ticker(self):
        self.assertEqual(self.analyzer._identifiers("ABC0007F0XYZ"), [])


if __name__ == "__main__":
    unittest.main()
