"""Exercise issue #46 through the production gateway and HTTP adapter."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import b_adapter
from api.main import app
from b_agent.answering import _grounded_structured
from b_agent.models import Capability, StepOutcome, ValueProvenance
from b_agent.postgres_gateway import PostgresDataGateway
from tests.test_postgres_gateway import FakeConnection, _provider, _structured_row


class StructuredQueryContractTest(unittest.TestCase):
    def pipeline(self, rows=None):
        connection = FakeConnection(structured_rows=rows or [])
        gateway = PostgresDataGateway(_provider(connection))
        with patch.object(b_adapter, "build_hcx_client_from_env", return_value=None):
            pipeline = b_adapter._build_pipeline(gateway=gateway)
        return pipeline, connection

    def answer(self, question, rows=None):
        pipeline, connection = self.pipeline(rows)
        with patch.object(b_adapter, "_PIPELINE", pipeline):
            response = TestClient(app).get("/answer", params={
                "question_id": "issue-46", "question": question,
            })
        self.assertEqual(response.status_code, 200)
        return response.json(), connection

    def test_reported_sort_questions_reach_sql_and_return_complete(self):
        for question, ordering in (
            ("최근 1년 수익률 높은 국내 ETF 3개", "return_1y_pct DESC NULLS LAST"),
            ("순자산 낮은 국내 ETF 3개", "asset_amount ASC NULLS LAST"),
        ):
            with self.subTest(question=question):
                body, connection = self.answer(question, [_structured_row()])
                self.assertEqual(len(connection.calls), 1)
                self.assertIn(ordering, connection.calls[0][0])
                self.assertIn("B상태: complete", body["think_trace"])
                self.assertIn("테스트 국내 ETF", body["answer"])
                self.assertIn("2026-08-31", body["retrieved_context"])
                self.assertIn("domestic_etf.xlsx", body["retrieved_context"])

    def test_all_numeric_sort_directions_use_correct_query_before_limit(self):
        for field, column, native in (
            ("net_assets", "asset_amount", "desc"),
            ("fee_rate", "expense_ratio_pct", "asc"),
            ("one_year_return", "return_1y_pct", "desc"),
            ("return_1y", "return_1y_pct", "desc"),
        ):
            for direction in ("asc", "desc"):
                with self.subTest(field=field, direction=direction):
                    connection = FakeConnection(structured_rows=[_structured_row()])
                    batch = PostgresDataGateway(_provider(connection)).retrieve(
                        Capability.STRUCTURED_SEARCH,
                        {"product_types": ["domestic_etf"],
                         "sorts": [{"field": field, "direction": direction}]}, {}, 3,
                    )
                    sql, params = connection.calls[0]
                    self.assertIn(f"{column} {direction.upper()} NULLS LAST", sql)
                    self.assertEqual("search.filter_products(" in sql, direction == native)
                    self.assertEqual(params[-1], 3)
                    self.assertTrue(batch.coverage_complete)

    def test_both_return_aliases_preserve_filter_value_and_evidence(self):
        for field in ("return_1y", "one_year_return"):
            with self.subTest(field=field):
                connection = FakeConnection(structured_rows=[_structured_row()])
                batch = PostgresDataGateway(_provider(connection)).retrieve(
                    Capability.STRUCTURED_SEARCH,
                    {"product_types": ["domestic_etf"], "filters": [
                        {"field": field, "operator": "lte", "value": -5.0}
                    ]}, {}, 3,
                )
                sql, params = connection.calls[0]
                self.assertIn('metrics."return_1y_pct" <= %s', sql)
                self.assertIn(-5.0, params)
                self.assertEqual(batch.evidence[0].structured[field], 12.3)

    def test_missing_sort_values_are_partial_and_actual_zero_is_zero_match(self):
        row = _structured_row()
        row.update(asset_amount=None, _missing_net_assets=1)
        body, _ = self.answer("순자산 낮은 국내 ETF 3개", [row])
        self.assertIn("B상태: partial", body["think_trace"])
        body, connection = self.answer("순자산 낮은 국내 ETF 3개")
        self.assertEqual(len(connection.calls), 1)
        self.assertIn("B상태: complete", body["think_trace"])
        self.assertIn("일치하는 상품이 없습니다", body["answer"])

    def test_sale_questions_reach_gateway_for_all_master_product_types(self):
        for product, database_type in (("국내 ETF", "DOMESTIC_ETP"),
                                       ("해외 ETF", "OVERSEAS_ETP"),
                                       ("공모펀드", "PUBLIC_FUND_CLASS")):
            with self.subTest(product=product):
                row = _structured_row()
                row.update(product_type=database_type, sale_available=True,
                           sale_available_scope="MASTER_STATUS_ONLY",
                           sale_status_basis_date="2026-08-22",
                           _sale_population=1, _missing_sale_available=0)
                body, connection = self.answer(f"매수 가능한 {product} 3개", [row])
                self.assertEqual(len(connection.calls), 1)
                self.assertIn("core.v_product_sale_availability", connection.calls[0][0])
                self.assertIn("sale_available IS TRUE", connection.calls[0][0])
                self.assertIn("B상태: complete", body["think_trace"])
                self.assertIn("마스터 기준일", body["answer"])
                self.assertIn("실제 주문 가능 여부", body["answer"])
                self.assertIn("필드: sale_available", body["retrieved_context"])
                self.assertIn("2026-08-22", body["retrieved_context"])

    def test_unknown_sale_status_is_not_false_or_zero_match(self):
        sentinel = {"product_id": None, "_sale_population": 5,
                    "_missing_sale_available": 2, "_total_hits": 0}
        body, connection = self.answer("매수 가능한 국내 ETF 3개", [sentinel])
        self.assertEqual(len(connection.calls), 1)
        self.assertIn("B상태: unanswerable", body["think_trace"])
        self.assertIn("확정할 수 없습니다", body["answer"])
        self.assertNotIn("일치하는 상품이 없습니다", body["answer"])
        self.assertEqual(body["retrieved_context"], "")
        sentinel["_missing_sale_available"] = 0
        body, _ = self.answer("매수 가능한 국내 ETF 3개", [sentinel])
        self.assertIn("B상태: complete", body["think_trace"])
        self.assertIn("일치하는 상품이 없습니다", body["answer"])

    def test_known_matches_with_unknown_candidates_remain_partial(self):
        row = _structured_row()
        row.update(sale_available=True, sale_available_scope="MASTER_STATUS_ONLY",
                   _sale_population=10, _missing_sale_available=4)
        body, _ = self.answer("매수 가능한 국내 ETF 3개", [row])
        self.assertIn("B상태: partial", body["think_trace"])
        self.assertIn("4/10", body["think_trace"])
        self.assertIn("마스터 기준일", body["answer"])

    def test_sale_filter_rejects_non_boolean_values_and_invalid_operators(self):
        for operator, value in (("eq", "true"), ("eq", 1), ("contains", True), ("eq", None)):
            with self.subTest(operator=operator, value=value):
                pipeline, connection = self.pipeline()
                gateway = PostgresDataGateway(_provider(connection))
                query = {"product_types": ["domestic_etf"], "filters": [
                    {"field": "sale_available", "operator": operator, "value": value}
                ]}
                self.assertIsNotNone(gateway.unsupported_reason(Capability.STRUCTURED_SEARCH, query))
                self.assertEqual(connection.calls, [])

    def test_unimplemented_metric_is_not_reported_as_search_failure(self):
        body, connection = self.answer("최근 3개월 수익률 높은 국내 ETF 3개")
        self.assertEqual(connection.calls, [])
        self.assertIn("return_3m", body["answer"])
        self.assertNotIn("B상태: error", body["think_trace"])

    def test_new_return_alias_cannot_bypass_provenance_sanitization(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        evidence = PostgresDataGateway(_provider(connection)).retrieve(
            Capability.STRUCTURED_SEARCH, {"product_types": ["domestic_etf"]}, {}, 3,
        ).evidence[0]
        for field in ("return_1y", "return_1y_pct", "one_year_return"):
            with self.subTest(field=field):
                excluded = replace(evidence, value_provenance=[ValueProvenance(
                    field_name=field, fill_type="estimated", evidence_eligible=False,
                )])
                for projected in (_grounded_structured(excluded),
                                  b_adapter._public_evidence(excluded).structured):
                    for alias in ("return_1y", "return_1y_pct", "one_year_return"):
                        self.assertNotIn(alias, projected)


if __name__ == "__main__":
    unittest.main()
