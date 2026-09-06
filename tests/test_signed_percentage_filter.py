from __future__ import annotations

import json
import unittest
from contextlib import contextmanager

from b_agent.models import Capability, Operator
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.pipeline import BAgentPipeline
from b_agent.planner import RetrievalPlanner
from b_agent.postgres_gateway import PostgresDataGateway
from b_agent.retrievers import InMemoryStructuredRetriever, RetrieverRegistry


class FakeCursor:
    description = None

    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return FakeCursor(self.calls)


class SignedPercentageFilterTest(unittest.TestCase):
    def setUp(self):
        self.analyzer = RuleBasedQuestionAnalyzer()

    def test_signed_percentages_preserve_sign_and_comparison(self):
        cases = (
            ("1년 수익률 -5% 이하 국내 ETF", -5.0, Operator.LTE),
            ("1년 수익률 -0.5% 이상 국내 ETF", -0.5, Operator.GTE),
            ("1년 수익률 +5% 초과 국내 ETF", 5.0, Operator.GT),
            ("1년 수익률 0% 미만 국내 ETF", 0.0, Operator.LT),
        )

        for question, expected_value, expected_operator in cases:
            with self.subTest(question=question):
                parsed = self.analyzer.analyze(question)
                result = next(
                    item
                    for item in parsed.filters
                    if item.field == "one_year_return"
                )
                self.assertEqual(result.value, expected_value)
                self.assertEqual(result.operator, expected_operator)

    def test_negative_filter_excludes_positive_return_product(self):
        registry = RetrieverRegistry()
        registry.register(
            Capability.STRUCTURED_SEARCH,
            InMemoryStructuredRetriever(
                [
                    {
                        "product_id": "POSITIVE",
                        "product_type": "domestic_etf",
                        "name": "양수 수익률 ETF",
                        "one_year_return": 3.0,
                        "source_ref": "domestic-etf.xlsx#row=2",
                    },
                    {
                        "product_id": "NEGATIVE",
                        "product_type": "domestic_etf",
                        "name": "음수 수익률 ETF",
                        "one_year_return": -6.0,
                        "source_ref": "domestic-etf.xlsx#row=3",
                    },
                ]
            ),
        )

        context = json.loads(
            BAgentPipeline(registry=registry)
            .run("negative-return", "1년 수익률 -5% 이하 국내 ETF 2개")
            .retrieved_context
        )

        self.assertEqual([item["product_id"] for item in context], ["NEGATIVE"])

    def test_negative_value_reaches_postgres_binding(self):
        connection = FakeConnection()

        @contextmanager
        def provider():
            yield connection

        understanding = self.analyzer.analyze(
            "1년 수익률 -5% 이하 국내 ETF"
        )
        step = next(
            item
            for item in RetrievalPlanner().build(understanding).steps
            if item.capability == Capability.STRUCTURED_SEARCH
        )

        PostgresDataGateway(provider).retrieve(
            step.capability, step.query, {}, step.top_k
        )

        sql, params = connection.calls[0]
        self.assertIn('metrics."return_1y_pct" <= %s', sql)
        self.assertIn(-5.0, params)


if __name__ == "__main__":
    unittest.main()
