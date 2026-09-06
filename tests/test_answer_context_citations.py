"""Issue #47: preserve final evidence through the real HTTP serialization path."""

import re
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from api.main import app
from tests.test_postgres_gateway import (
    FakeConnection,
    StaticQueryEmbedder,
    _provider,
    _structured_row,
)
from b_agent.postgres_gateway import PostgresDataGateway


class CitingLLM:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return "\n".join(
            f"{row['canonical_name']}를 확인했습니다. "
            f"[structured:domestic_etf:{row['product_id']}]"
            for row in self.rows
        )


class AnswerContextCitationsTest(unittest.TestCase):
    def _answer(self, rows):
        llm = CitingLLM(rows)
        gateway = PostgresDataGateway(
            _provider(FakeConnection(structured_rows=rows)),
            query_embedder=StaticQueryEmbedder(),
        )
        pipeline = b_adapter._build_pipeline(gateway=gateway, llm_client=llm)
        with patch.object(b_adapter, "_PIPELINE", pipeline):
            response = TestClient(app).get(
                "/answer",
                params={
                    "question_id": "issue-47",
                    "question": f"순자산 높은 국내 ETF {len(rows)}개",
                },
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {"question_id", "question", "retrieved_context", "think_trace", "answer"},
        )
        self.assertTrue(all(isinstance(value, str) for value in body.values()))
        self.assertEqual(llm.calls, 1)
        cited = set(re.findall(r"\[([^\[\]\n]+)\]", body["answer"]))
        expected = {f"structured:domestic_etf:{row['product_id']}" for row in rows}
        self.assertEqual(cited, expected)  # Do not let a composer fallback mask the bug.
        context_ids = set(re.findall(r"^\[([^\[\]\n]+)\]", body["retrieved_context"], re.M))
        self.assertLessEqual(cited, context_ids)
        return body

    @staticmethod
    def _rows(count):
        rows = []
        for index in range(1, count + 1):
            row = _structured_row()
            row.update(
                product_id=100 + index,
                canonical_name=f"검토상품{index} ETF",
                _total_hits=count,
                _value_provenance=[],
            )
            rows.append(row)
        return rows

    def test_all_citations_survive_six_and_fifty_product_responses(self):
        for count in (6, 50):
            with self.subTest(count=count):
                body = self._answer(self._rows(count))
                self.assertEqual(len(body["retrieved_context"].splitlines()), count)

    def test_field_provenance_is_grouped_without_displacing_other_products(self):
        rows = self._rows(2)
        fields = (
            "canonical_name", "currency_code", "asset_amount",
            "expense_ratio_pct", "return_1y_pct", "risk_label",
        )
        for row in rows:
            row["_value_provenance"] = [
                {
                    "field_name": field,
                    "fill_type": "original",
                    "evidence_eligible": True,
                    "was_missing": False,
                    "source_reference": f"source-{row['product_id']}-{field}.csv",
                    "source_as_of_date": "2026-08-31",
                }
                for field in fields
            ]
        body = self._answer(rows)
        lines = body["retrieved_context"].splitlines()
        self.assertEqual(len(lines), 2)
        for row in rows:
            label = f"[structured:domestic_etf:{row['product_id']}]"
            line = next(line for line in lines if line.startswith(label))
            self.assertEqual(body["retrieved_context"].count(label), 1)
            self.assertEqual(line.count("내용: "), 1)
            for field in fields:
                self.assertIn(f"필드: {field}", line)
                self.assertIn(f"source-{row['product_id']}-{field}.csv", line)


if __name__ == "__main__":
    unittest.main()
