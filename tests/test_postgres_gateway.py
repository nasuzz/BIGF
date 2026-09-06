from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from agent import b_adapter
from b_agent.executor import PlanExecutor
from b_agent.embeddings import EmbeddingModelLoadError
from b_agent.gateway import CapabilitySnapshot, registry_from_gateway
from b_agent.models import (
    Capability,
    Evidence,
    ProductType,
    QueryUnderstanding,
    RetrievalBatch,
    RetrievalPlan,
    RetrievalStep,
    StepResult,
    StepOutcome,
)
from b_agent.postgres_gateway import (
    PostgresDataGateway,
    UnsupportedGatewayQuery,
    _as_mapping,
)
from b_agent.parser import RuleBasedQuestionAnalyzer
from b_agent.planner import RetrievalPlanner
from contracts import AnswerStatus, FillType, ReasonCode, SearchType
from b_agent.retrievers import RetrievalContext


def _structured_row() -> dict:
    return {
        "product_id": 101,
        "product_type": "DOMESTIC_ETP",
        "source_product_key": "KR7000101",
        "canonical_name": "테스트 국내 ETF",
        "short_name": "테스트ETF",
        "currency_code": "KRW",
        "isin": "KR7000101001",
        "ticker": "000101",
        "manager_name": "테스트운용",
        "asset_type": "equity",
        "investment_region": "한국",
        "offer_type": "public",
        "expense_ratio_pct": Decimal("0.15"),
        "asset_amount": Decimal("120000000000"),
        "return_1d_pct": Decimal("0.2"),
        "return_1y_pct": Decimal("12.3"),
        "risk_label": "2등급",
        "strategy_text": None,
        "has_warning": False,
        "snapshot_id": 7,
        "raw_row_id": 88,
        "source_file_name": "domestic_etf.xlsx",
        "source_sheet": "상품",
        "source_row_number": 12,
        "data_as_of_date": date(2026, 8, 31),
        "provenance_status": "VERIFIED",
        "_total_hits": 1,
        "_missing_net_assets": 0,
        "_value_provenance": [
            {
                "field_name": "canonical_name",
                "fill_type": "original",
                "evidence_eligible": True,
                "was_missing": False,
                "source_reference": None,
                "source_as_of_date": None,
                "fill_confidence": None,
            },
            {
                "field_name": "expense_ratio_pct",
                "fill_type": "official_fill",
                "evidence_eligible": True,
                "was_missing": True,
                "source_reference": "https://example.test/product/101",
                "source_as_of_date": "2026-08-30",
                "fill_confidence": Decimal("0.99"),
            },
        ],
    }


def _identity_row() -> dict:
    row = _structured_row()
    row.update(
        {
            "matched_term": "테스트 국내 ETF",
            "term_order": 1,
            "matched_alias_type": "CANONICAL_NAME",
            "matched_alias_value": "테스트 국내 ETF",
            "match_type": "EXACT",
            "match_score": Decimal("1.0"),
        }
    )
    return row


def _keyword_row() -> dict:
    return {
        "document_id": 501,
        "product_id": 202,
        "source_product_key": "US202",
        "canonical_name": "Global AI ETF",
        "ticker": "GAI",
        "base_index": "Global AI Index",
        "expense_ratio_pct": Decimal("0.35"),
        "aum": Decimal("900000000"),
        "strategy_text": "인공지능 반도체 기업에 투자하는 전략",
        "evidence_excerpt": "인공지능 반도체 기업에 투자",
        "match_type": "KEYWORD",
        "keyword_score": Decimal("0.8"),
        "similarity_score": Decimal("0.7"),
        "combined_score": Decimal("0.9"),
        "has_warning": False,
        "snapshot_id": 9,
        "source_file_name": "overseas_etf.xlsx",
        "source_sheet": "전략",
        "source_row_number": 4,
        "data_as_of_date": date(2026, 8, 31),
        "provenance_status": "VERIFIED",
        "_total_hits": 1,
    }


def _vector_row(document_id=601, score="0.83") -> dict:
    return {
        "document_id": document_id,
        "product_id": 202,
        "product_type": "OVERSEAS_ETP",
        "source_product_key": "US202",
        "canonical_name": "Global AI ETF",
        "short_name": "Global AI",
        "currency_code": "USD",
        "isin": "US0000000202",
        "ticker": "GAI",
        "manager_name": "Global Manager",
        "asset_type": "equity",
        "investment_region": "미국",
        "offer_type": "public",
        "expense_ratio_pct": Decimal("0.35"),
        "asset_amount": Decimal("900000000"),
        "return_1d_pct": Decimal("0.1"),
        "return_1y_pct": Decimal("15.2"),
        "risk_label": None,
        "strategy_text": "인공지능 반도체 기업에 투자하는 전략",
        "raw_text": "인공지능과 반도체 기업에 집중 투자하는 해외 ETF 전략",
        "normalized_text": "인공지능과 반도체 기업에 집중 투자하는 해외 etf 전략",
        "source_field": "cu_strtegy",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
        "embedded_at": "2026-09-01T00:00:00+00:00",
        "cosine_similarity": Decimal(score),
        "has_warning": False,
        "snapshot_id": 9,
        "raw_row_id": 99,
        "source_file_name": "overseas_etf.xlsx",
        "source_sheet": "전략",
        "source_row_number": 4,
        "data_as_of_date": date(2026, 8, 31),
        "provenance_status": "VERIFIED",
        "_total_hits": 1,
    }


def _holding_row() -> dict:
    return {
        "source_kind": "etf_holding",
        "payload": {
            "product_id": 101,
            "holding_id": "KR7005930003",
            "holding_name": "삼성전자",
            "weight_pct": "18.25",
        },
        "product_id": 101,
        "product_type": "DOMESTIC_ETP",
        "canonical_name": "테스트 국내 ETF",
        "holding_id": "KR7005930003",
        "holding_name": "삼성전자",
        "holding_weight": Decimal("18.25"),
        "holding_as_of_date": date(2026, 8, 31),
        "holding_source_ref": "krx-etf-holding#101:KR7005930003",
        "snapshot_id": 7,
        "data_as_of_date": date(2026, 8, 31),
        "source_file_name": "krx_etf_holding.csv",
        "provenance_status": "VERIFIED",
        "_total_hits": 1,
    }


def _relation_row(
    *,
    relation_id=301,
    source_id="247540",
    source_name="에코프로비엠",
    target_id="086520",
    target_name="에코프로",
) -> dict:
    return {
        "relation_id": relation_id,
        "predicate": "subsidiaryOf",
        "source_entity_id": source_id,
        "source_entity_name": source_name,
        "target_entity_id": target_id,
        "target_entity_name": target_name,
        "source_is_listed": True,
        "target_is_listed": True,
        "relation_as_of_date": date(2026, 8, 31),
        "source_ref": "opendart.example#relation=301",
        "confidence": Decimal("0.99"),
        "has_warning": False,
        "snapshot_id": 11,
        "raw_row_id": 301,
        "source_file_name": "organization_relations.csv",
        "source_sheet": "relations",
        "source_row_number": 2,
        "data_as_of_date": date(2026, 8, 31),
        "provenance_status": "VERIFIED",
        "_constraint_total_hits": 1,
        "_total_hits": 1,
        "_source_available": True,
    }


class StaticQueryEmbedder:
    model_name = "Qwen/Qwen3-Embedding-0.6B"
    dimension = 1024

    def __init__(self, vector=None, error=None):
        self.vector = vector if vector is not None else [0.001] * self.dimension
        self.error = error
        self.queries = []

    def encode_query(self, text):
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        return list(self.vector)


class FakeCursor:
    description = None

    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, sql, params=None):
        self.connection.calls.append((sql, params))
        if self.connection.error is not None:
            raise self.connection.error
        if "find_organization_relations" in sql:
            self.rows = list(self.connection.relation_rows)
        elif "ext.v_etf_holding" in sql:
            self.rows = list(self.connection.holding_rows)
        elif "vector_candidates" in sql:
            self.rows = list(self.connection.vector_rows)
        elif "find_products" in sql:
            self.rows = list(self.connection.identity_rows)
        elif "filter_products" in sql:
            self.rows = list(self.connection.structured_rows)
        elif "search.product_metrics" in sql:
            self.rows = list(self.connection.structured_rows)
        elif "find_overseas_strategies" in sql:
            self.rows = list(self.connection.keyword_rows)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        identity_rows=None,
        structured_rows=None,
        keyword_rows=None,
        vector_rows=None,
        holding_rows=None,
        relation_rows=None,
        error=None,
    ):
        self.identity_rows = identity_rows or []
        self.structured_rows = structured_rows or []
        self.keyword_rows = keyword_rows or []
        self.vector_rows = vector_rows or []
        self.holding_rows = holding_rows or []
        self.relation_rows = relation_rows or []
        self.error = error
        self.calls = []

    def cursor(self):
        return FakeCursor(self)


def _provider(connection):
    @contextmanager
    def provide():
        yield connection

    return provide


class PostgresDataGatewayTest(unittest.TestCase):
    def test_dbapi_tuple_rows_are_mapped_from_cursor_description(self):
        cursor = FakeCursor(FakeConnection())
        cursor.description = [("product_id",), ("canonical_name",)]

        self.assertEqual(
            _as_mapping(cursor, (101, "테스트 국내 ETF")),
            {"product_id": 101, "canonical_name": "테스트 국내 ETF"},
        )

    def test_capability_snapshot_and_registry_only_advertise_implemented_paths(self):
        gateway = PostgresDataGateway(_provider(FakeConnection()))

        snapshot = gateway.capability_snapshot()
        registry = registry_from_gateway(gateway)

        expected = {
            Capability.IDENTITY_SEARCH,
            Capability.STRUCTURED_SEARCH,
            Capability.KEYWORD_SEARCH,
            Capability.VECTOR_SEARCH,
            # HOLDING_SEARCH intentionally excluded: agent/ext_retrievers.py's
            # PostgresHoldingRetriever is the single registered owner of this
            # capability (issue #34). This gateway's own _holding_search()
            # stays in the module for reference but is unreachable by design.
            Capability.RELATION_SEARCH,
        }
        self.assertEqual(snapshot.available, expected)
        self.assertEqual(set(registry.capabilities()), expected)
        self.assertEqual(registry.snapshot_id, "postgres-search-v4")
        self.assertEqual(
            b_adapter._CAPABILITY_TO_SEARCH_TYPE["holding_search"],
            SearchType.SQL,
        )
        self.assertEqual(
            b_adapter._CAPABILITY_TO_SEARCH_TYPE["relation_search"],
            SearchType.SQL,
        )

    def test_identity_search_calls_find_products_and_preserves_provenance(self):
        connection = FakeConnection(identity_rows=[_identity_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.IDENTITY_SEARCH,
            {
                "identifiers": [],
                "product_mentions": ["테스트 국내 ETF"],
                "product_types": ["domestic_etf"],
            },
            {},
            20,
        )

        self.assertIn("search.find_products", connection.calls[0][0])
        self.assertEqual(len(connection.calls), 1)
        self.assertEqual(batch.total_hits, 1)
        self.assertFalse(batch.ambiguous)
        evidence = batch.evidence[0]
        self.assertEqual(evidence.product_id, "101")
        self.assertEqual(evidence.product_type, ProductType.DOMESTIC_ETF)
        self.assertEqual(evidence.structured["matched_terms"], ["테스트 국내 ETF"])
        self.assertEqual(evidence.as_of_date, "2026-08-31")
        self.assertEqual(evidence.source_ref, "domestic_etf.xlsx#sheet=상품&row=12")
        self.assertEqual(evidence.provenance["snapshot_id"], 7)

    def test_identity_search_batches_multiple_terms_into_one_database_call(self):
        connection = FakeConnection(identity_rows=[_identity_row()])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.IDENTITY_SEARCH,
            {
                "identifiers": ["000101"],
                "product_mentions": ["테스트 국내 ETF"],
                "product_types": ["domestic_etf"],
            },
            {},
            20,
        )

        self.assertEqual(len(connection.calls), 1)
        self.assertIn("unnest(%s::text[])", connection.calls[0][0])
        self.assertEqual(
            connection.calls[0][1][0], ["000101", "테스트 국내 ETF"]
        )

    def test_identity_search_applies_each_requested_product_type_inside_function(self):
        connection = FakeConnection(identity_rows=[_identity_row()])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.IDENTITY_SEARCH,
            {
                "product_mentions": ["테스트 ETF"],
                "product_types": ["domestic_etf", "foreign_etf"],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn("query_types.product_type", sql)
        self.assertIn(
            "search.find_products(\n"
            "                    query_terms.term,\n"
            "                    query_types.product_type",
            sql,
        )
        self.assertEqual(params[1], ["DOMESTIC_ETP", "OVERSEAS_ETP"])

    def test_unresolved_identity_term_reports_incomplete_coverage(self):
        gateway = PostgresDataGateway(_provider(FakeConnection()))

        batch = gateway.retrieve(
            Capability.IDENTITY_SEARCH,
            {"product_mentions": ["없는 상품"], "product_types": ["domestic_etf"]},
            {},
            20,
        )

        self.assertFalse(batch.coverage_complete)
        self.assertIn("미해결 식별 표현", batch.coverage_note)

    def test_identity_search_marks_function_cap_as_unknown_total(self):
        row = _identity_row()
        row["_partition_hits"] = 100
        gateway = PostgresDataGateway(
            _provider(FakeConnection(identity_rows=[row]))
        )

        batch = gateway.retrieve(
            Capability.IDENTITY_SEARCH,
            {"product_mentions": ["테스트 ETF"], "product_types": ["domestic_etf"]},
            {},
            20,
        )

        self.assertIsNone(batch.total_hits)
        self.assertTrue(batch.truncated)
        self.assertFalse(batch.coverage_complete)
        self.assertIn("최대 100건", batch.coverage_note)

    def test_structured_search_calls_filter_products_and_preserves_contract(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["domestic_etf"],
                "filters": [
                    {
                        "field": "net_assets",
                        "operator": "gte",
                        "value": 100_000_000_000,
                    }
                ],
                "sorts": [{"field": "net_assets", "direction": "desc"}],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn("search.filter_products", sql)
        self.assertIn("asset_amount", sql)
        self.assertIn("ORDER BY asset_amount DESC NULLS LAST", sql)
        self.assertEqual(params[0], "DOMESTIC_ETP")
        self.assertIn("asset_amount_desc", params)
        self.assertEqual(batch.total_hits, 1)
        self.assertTrue(batch.coverage_complete)
        self.assertEqual(batch.coverage_by_field["net_assets"]["missing"], 0)
        evidence = batch.evidence[0]
        self.assertEqual(evidence.structured["name"], "테스트 국내 ETF")
        self.assertEqual(evidence.structured["net_assets"], 120_000_000_000.0)
        self.assertEqual(evidence.structured["fee_rate"], 0.15)
        self.assertEqual(evidence.provenance["provenance_status"], "VERIFIED")
        self.assertEqual(
            [item.field_name for item in evidence.value_provenance],
            ["canonical_name", "expense_ratio_pct"],
        )
        self.assertEqual(evidence.value_provenance[0].fill_type, "original")
        self.assertTrue(evidence.value_provenance[0].evidence_eligible)
        self.assertEqual(evidence.value_provenance[1].fill_confidence, 0.99)
        self.assertIn("meta.value_provenance", sql)

    def test_invalid_value_provenance_is_not_exposed_as_field_evidence(self):
        row = _structured_row()
        row["_value_provenance"] = [
            {
                "field_name": "canonical_name",
                "fill_type": "unreviewed_fill",
                "evidence_eligible": True,
            },
            "malformed",
        ]
        gateway = PostgresDataGateway(
            _provider(FakeConnection(structured_rows=[row]))
        )

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {"product_types": ["domestic_etf"], "filters": [], "sorts": []},
            {},
            20,
        )

        self.assertEqual(batch.evidence[0].value_provenance, [])

    def test_structured_single_type_is_filtered_inside_function_before_cap(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {"product_types": ["domestic_etf"], "filters": [], "sorts": []},
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn("search.filter_products", sql)
        self.assertEqual(params[0], "DOMESTIC_ETP")
        self.assertIn("statistics", sql)

    def test_structured_candidate_ids_are_filtered_before_order_and_limit(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        gateway = PostgresDataGateway(_provider(connection))
        candidate = Evidence(
            evidence_id="identity:domestic_etf:101",
            capability=Capability.IDENTITY_SEARCH,
            source_id="identity",
            content="테스트 국내 ETF",
            product_id="101",
        )

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {"product_types": ["domestic_etf"], "filters": [], "sorts": []},
            {"identity_search": [candidate]},
            20,
        )

        sql, params = connection.calls[0]
        self.assertNotIn("search.filter_products", sql)
        self.assertLess(sql.index("metrics.product_id::text"), sql.index("ORDER BY"))
        self.assertLess(sql.index("metrics.product_id::text"), sql.index("LIMIT"))
        self.assertIn(["101"], params)
        self.assertEqual(batch.evidence[0].product_id, "101")

    def test_structured_multi_type_filter_is_applied_before_final_limit(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["domestic_etf", "foreign_etf"],
                "filters": [],
                "sorts": [],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertNotIn("search.filter_products", sql)
        self.assertLess(sql.index("metrics.product_type"), sql.index("ORDER BY"))
        self.assertIn(["DOMESTIC_ETP", "OVERSEAS_ETP"], params)

    def test_repeated_native_filters_use_one_consistent_direct_population(self):
        connection = FakeConnection(structured_rows=[])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["domestic_etf"],
                "filters": [
                    {"field": "currency", "operator": "eq", "value": "KRW"},
                    {"field": "currency", "operator": "eq", "value": "USD"},
                ],
                "sorts": [],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertNotIn("search.filter_products", sql)
        self.assertEqual(sql.count('lower(metrics."currency_code")'), 2)
        self.assertIn("KRW", params)
        self.assertIn("USD", params)
        self.assertEqual(batch.total_hits, 0)

    def test_us_region_filter_expands_to_exact_database_aliases(self):
        connection = FakeConnection(structured_rows=[])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["foreign_etf"],
                "filters": [
                    {
                        "field": "investment_region",
                        "operator": "contains",
                        "value": "미국",
                    }
                ],
                "sorts": [],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        region_values = next(
            value
            for value in params
            if isinstance(value, list) and "united states of america" in value
        )
        self.assertNotIn("search.filter_products", sql)
        self.assertIn(
            'lower(btrim(metrics."investment_region")) = ANY(%s::text[])',
            sql,
        )
        self.assertNotIn("global ex us", region_values)
        self.assertIn("usa", region_values)

    def test_global_region_filter_includes_global_ex_us(self):
        connection = FakeConnection(structured_rows=[])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["foreign_etf"],
                "filters": [
                    {
                        "field": "investment_region",
                        "operator": "contains",
                        "value": "글로벌",
                    }
                ],
                "sorts": [],
            },
            {},
            20,
        )

        _, params = connection.calls[0]
        region_values = next(
            value
            for value in params
            if isinstance(value, list) and "global ex us" in value
        )
        self.assertIn("global", region_values)

    def test_unknown_region_keeps_existing_partial_match_behavior(self):
        connection = FakeConnection(structured_rows=[])
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["foreign_etf"],
                "filters": [
                    {
                        "field": "investment_region",
                        "operator": "contains",
                        "value": "아시아태평양",
                    }
                ],
                "sorts": [],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn('metrics."investment_region" ILIKE %s', sql)
        self.assertIn("%아시아태평양%", params)

    def test_vector_search_uses_the_same_us_region_alias_filter(self):
        connection = FakeConnection(vector_rows=[_vector_row()])
        gateway = PostgresDataGateway(
            _provider(connection), StaticQueryEmbedder()
        )

        gateway.retrieve(
            Capability.VECTOR_SEARCH,
            {
                "question": "미국 성장주에 집중 투자하는 해외 ETF 전략 알려줘",
                "product_types": ["foreign_etf"],
                "filters": [
                    {
                        "field": "investment_region",
                        "operator": "contains",
                        "value": "미국",
                    }
                ],
            },
            {},
            10,
        )

        sql, params = connection.calls[0]
        self.assertIn("eligible_products AS MATERIALIZED", sql)
        self.assertTrue(
            any(
                isinstance(value, list)
                and "united states of america" in value
                and "global ex us" not in value
                for value in params
            )
        )

    def test_structured_total_and_truncation_use_filtered_population(self):
        row = _structured_row()
        row["_total_hits"] = 137
        row["_missing_net_assets"] = 2
        gateway = PostgresDataGateway(
            _provider(FakeConnection(structured_rows=[row]))
        )

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["domestic_etf"],
                "filters": [],
                "sorts": [{"field": "net_assets", "direction": "desc"}],
            },
            {},
            20,
        )

        self.assertEqual(batch.total_hits, 137)
        self.assertTrue(batch.truncated)
        self.assertEqual(
            batch.coverage_by_field["net_assets"],
            {"population": 137, "present": 135, "missing": 2},
        )

    def test_empty_query_result_is_not_reported_as_failure(self):
        gateway = PostgresDataGateway(_provider(FakeConnection()))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {"product_types": ["domestic_etf"], "filters": [], "sorts": []},
            {},
            20,
        )

        self.assertEqual(batch.evidence, [])
        self.assertEqual(batch.total_hits, 0)
        self.assertTrue(batch.coverage_complete)

    def test_unsupported_filter_is_rejected_before_sql_execution(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))

        with self.assertRaisesRegex(UnsupportedGatewayQuery, "volatility"):
            gateway.retrieve(
                Capability.STRUCTURED_SEARCH,
                {
                    "product_types": ["domestic_etf"],
                    "filters": [
                        {"field": "volatility", "operator": "lte", "value": 10}
                    ],
                    "sorts": [],
                },
                {},
                20,
            )

        self.assertEqual(connection.calls, [])

    def test_keyword_search_calls_overseas_strategy_function(self):
        connection = FakeConnection(keyword_rows=[_keyword_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.KEYWORD_SEARCH,
            {
                "question": "인공지능 반도체 해외 ETF 전략",
                "product_types": ["foreign_etf"],
            },
            {},
            10,
        )

        self.assertIn("search.find_overseas_strategies", connection.calls[0][0])
        evidence = batch.evidence[0]
        self.assertEqual(evidence.product_type, ProductType.FOREIGN_ETF)
        self.assertEqual(evidence.product_id, "202")
        self.assertEqual(evidence.record_id, "501")
        self.assertEqual(evidence.score, 0.9)
        self.assertIn("인공지능", evidence.content)

    def test_keyword_search_reports_capped_sample_instead_of_false_total(self):
        row = _keyword_row()
        row["_total_hits"] = 100
        gateway = PostgresDataGateway(
            _provider(FakeConnection(keyword_rows=[row]))
        )

        batch = gateway.retrieve(
            Capability.KEYWORD_SEARCH,
            {"question": "반도체 전략", "product_types": ["foreign_etf"]},
            {},
            10,
        )

        self.assertIsNone(batch.total_hits)
        self.assertTrue(batch.truncated)
        self.assertFalse(batch.coverage_complete)
        self.assertIn("표본", batch.coverage_note)

    def test_relation_search_preserves_direction_listing_and_provenance(self):
        connection = FakeConnection(relation_rows=[_relation_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.RELATION_SEARCH,
            {
                "entities": ["에코프로"],
                "relation_predicates": ["subsidiaryOf"],
                "relation_constraints": [
                    {
                        "predicate": "subsidiaryOf",
                        "anchor": "에코프로",
                        "anchor_role": "target",
                        "result_role": "source",
                        "result_listed": True,
                        "traversal_hops": 1,
                    }
                ],
            },
            {},
            50,
        )

        sql, params = connection.calls[0]
        constraints = json.loads(params[0])
        self.assertIn("search.find_organization_relations", sql)
        self.assertIn("version.status = 'ACTIVE'", sql)
        self.assertEqual(
            constraints,
            [
                {
                    "constraint_order": 1,
                    "predicate": "subsidiaryOf",
                    "anchor": "에코프로",
                    "anchor_role": "target",
                    "result_role": "source",
                    "result_listed": True,
                }
            ],
        )
        self.assertEqual(batch.total_hits, 1)
        self.assertTrue(batch.coverage_complete)
        evidence = batch.evidence[0]
        self.assertEqual(evidence.capability, Capability.RELATION_SEARCH)
        self.assertEqual(evidence.record_id, "301")
        self.assertEqual(evidence.structured["source_entity_name"], "에코프로비엠")
        self.assertEqual(evidence.structured["target_entity_name"], "에코프로")
        self.assertTrue(evidence.structured["source_is_listed"])
        self.assertEqual(evidence.source_ref, "opendart.example#relation=301")
        self.assertEqual(evidence.as_of_date, "2026-08-31")
        self.assertEqual(evidence.provenance["snapshot_id"], 11)

    def test_relation_search_passes_source_target_and_opposite_roles(self):
        cases = [
            ("source", "target"),
            ("target", "source"),
            ("either", "opposite"),
        ]

        for anchor_role, result_role in cases:
            with self.subTest(anchor_role=anchor_role, result_role=result_role):
                connection = FakeConnection(
                    relation_rows=[
                        {
                            "relation_id": None,
                            "_source_available": True,
                            "_total_hits": 0,
                        }
                    ]
                )
                gateway = PostgresDataGateway(_provider(connection))
                gateway.retrieve(
                    Capability.RELATION_SEARCH,
                    {
                        "relation_constraints": [
                            {
                                "predicate": "affiliateOf",
                                "anchor": "에코프로",
                                "anchor_role": anchor_role,
                                "result_role": result_role,
                                "traversal_hops": 1,
                            }
                        ]
                    },
                    {},
                    50,
                )

                constraint = json.loads(connection.calls[0][1][0])[0]
                self.assertEqual(constraint["anchor_role"], anchor_role)
                self.assertEqual(constraint["result_role"], result_role)

    def test_relation_search_distinguishes_unloaded_source_from_no_match(self):
        unavailable_gateway = PostgresDataGateway(_provider(FakeConnection()))
        unavailable = unavailable_gateway.retrieve(
            Capability.RELATION_SEARCH,
            {
                "relation_constraints": [
                    {
                        "predicate": "subsidiaryOf",
                        "anchor": "에코프로",
                        "anchor_role": "target",
                        "result_role": "source",
                    }
                ]
            },
            {},
            50,
        )
        no_match_gateway = PostgresDataGateway(
            _provider(
                FakeConnection(
                    relation_rows=[
                        {
                            "relation_id": None,
                            "_source_available": True,
                            "_total_hits": 0,
                        }
                    ]
                )
            )
        )
        no_match = no_match_gateway.retrieve(
            Capability.RELATION_SEARCH,
            {
                "relation_constraints": [
                    {
                        "predicate": "subsidiaryOf",
                        "anchor": "없는회사",
                        "anchor_role": "target",
                        "result_role": "source",
                    }
                ]
            },
            {},
            50,
        )

        self.assertFalse(unavailable.coverage_complete)
        self.assertIsNone(unavailable.total_hits)
        self.assertIn("스냅샷이 없습니다", unavailable.coverage_note)
        self.assertTrue(no_match.coverage_complete)
        self.assertEqual(no_match.total_hits, 0)

    def test_relation_search_rejects_unsupported_direction_or_hops(self):
        gateway = PostgresDataGateway(_provider(FakeConnection()))
        invalid_queries = [
            {
                "relation_constraints": [
                    {
                        "predicate": "subsidiaryOf",
                        "anchor": "에코프로",
                        "anchor_role": "invalid",
                    }
                ]
            },
            {
                "relation_constraints": [
                    {
                        "predicate": "subsidiaryOf",
                        "anchor": "에코프로",
                        "traversal_hops": 2,
                    }
                ]
            },
        ]

        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises(UnsupportedGatewayQuery):
                    gateway.retrieve(Capability.RELATION_SEARCH, query, {}, 50)

    @unittest.skip(
        "Issue #34: HOLDING_SEARCH is no longer registered from this gateway "
        "alone (agent/ext_retrievers.py owns it). This test exercised the "
        "chain using only registry_from_gateway(gateway); a replacement that "
        "also calls register_ext_retrievers() with a compatible fake fetcher "
        "is tracked as issue #34 follow-up work."
    )
    def test_relation_to_holding_chain_executes_with_gateway(self):
        holding_row = _holding_row()
        holding_row.update(
            {
                "holding_id": "247540",
                "holding_name": "에코프로비엠",
            }
        )
        connection = FakeConnection(
            relation_rows=[_relation_row()],
            holding_rows=[holding_row],
            structured_rows=[_structured_row()],
        )
        gateway = PostgresDataGateway(_provider(connection))
        plan = RetrievalPlanner().build(
            RuleBasedQuestionAnalyzer().analyze(
                "에코프로의 상장 자회사를 편입한 국내 ETF 알려줘"
            )
        )

        report = PlanExecutor(registry_from_gateway(gateway)).execute(plan)
        by_capability = {
            result.capability: result for result in report.step_results
        }

        self.assertEqual(
            by_capability[Capability.RELATION_SEARCH].outcome,
            StepOutcome.SUCCESS,
        )
        self.assertEqual(
            by_capability[Capability.HOLDING_SEARCH].outcome,
            StepOutcome.SUCCESS,
        )
        self.assertEqual(
            by_capability[Capability.STRUCTURED_SEARCH].outcome,
            StepOutcome.SUCCESS,
        )
        holding_sql, holding_params = next(
            (sql, params)
            for sql, params in connection.calls
            if "ext.v_etf_holding" in sql
        )
        self.assertIn("ext.v_etf_holding", holding_sql)
        self.assertEqual(
            holding_params[0],
            ["%247540%", "%에코프로비엠%"],
        )

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_queries_ext_views_and_preserves_contract(self):
        connection = FakeConnection(holding_rows=[_holding_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {
                "holding_targets": ["삼성전자"],
                "product_types": ["domestic_etf"],
                "relation_constraints": [],
            },
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn("ext.v_etf_holding", sql)
        self.assertIn("ext.v_product_opendart_disclosure", sql)
        self.assertIn("search.product_metrics", sql)
        self.assertIn("version.status = 'ACTIVE'", sql)
        self.assertEqual(params[0], ["%삼성전자%"])
        self.assertEqual(params[4], ["DOMESTIC_ETP"])
        self.assertEqual(batch.total_hits, 1)
        evidence = batch.evidence[0]
        self.assertEqual(evidence.capability, Capability.HOLDING_SEARCH)
        self.assertEqual(evidence.product_id, "101")
        self.assertEqual(evidence.product_type, ProductType.DOMESTIC_ETF)
        self.assertEqual(evidence.structured["holding_name"], "삼성전자")
        self.assertEqual(evidence.structured["holding_id"], "KR7005930003")
        self.assertEqual(
            evidence.source_ref, "krx-etf-holding#101:KR7005930003"
        )
        self.assertEqual(evidence.as_of_date, "2026-08-31")

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_escapes_like_wildcards(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))

        gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {"holding_targets": ["A_등급 5%"], "product_types": []},
            {},
            20,
        )

        sql, params = connection.calls[0]
        self.assertIn("ESCAPE '\\'", sql)
        self.assertEqual(params[0], [r"%A\_등급 5\%%"])

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_uses_relation_result_as_target(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))
        relation = Evidence(
            evidence_id="relation:1",
            capability=Capability.RELATION_SEARCH,
            source_id="relations",
            content="에코프로비엠은 에코프로의 자회사",
            source_ref="relations#1",
            structured={
                "source_entity_id": "247540",
                "source_entity_name": "에코프로비엠",
                "target_entity_name": "에코프로",
            },
        )

        gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {
                "holding_targets": [],
                "product_types": ["domestic_etf"],
                "relation_constraints": [{"result_role": "source"}],
            },
            {"relation_search": [relation]},
            20,
        )

        params = connection.calls[0][1]
        self.assertEqual(params[0], ["%247540%", "%에코프로비엠%"])

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_opposite_uses_target_for_source_anchor(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))
        relation = Evidence(
            evidence_id="relation:source-anchor",
            capability=Capability.RELATION_SEARCH,
            source_id="relations",
            content="에코프로와 에코프로비엠은 계열사",
            source_ref="relations#source-anchor",
            structured={
                "source_entity_id": "086520",
                "source_entity_name": "에코프로",
                "target_entity_id": "247540",
                "target_entity_name": "에코프로비엠",
            },
        )

        gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {
                "holding_targets": [],
                "product_types": ["domestic_etf"],
                "relation_constraints": [
                    {
                        "anchor": "에코프로",
                        "anchor_role": "either",
                        "result_role": "opposite",
                    }
                ],
            },
            {"relation_search": [relation]},
            20,
        )

        self.assertEqual(
            connection.calls[0][1][0], ["%247540%", "%에코프로비엠%"]
        )

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_opposite_uses_source_for_target_anchor(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))
        relation = Evidence(
            evidence_id="relation:target-anchor",
            capability=Capability.RELATION_SEARCH,
            source_id="relations",
            content="에코프로비엠과 에코프로는 계열사",
            source_ref="relations#target-anchor",
            structured={
                "source_entity_id": "247540",
                "source_entity_name": "에코프로비엠",
                "target_entity_id": "086520",
                "target_entity_name": "에코프로",
            },
        )

        gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {
                "holding_targets": [],
                "product_types": ["domestic_etf"],
                "relation_constraints": [
                    {
                        "anchor": "에코프로",
                        "anchor_role": "either",
                        "result_role": "opposite",
                    }
                ],
            },
            {"relation_search": [relation]},
            20,
        )

        self.assertEqual(
            connection.calls[0][1][0], ["%247540%", "%에코프로비엠%"]
        )

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_can_scope_named_etf_from_identity_dependency(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))
        product = Evidence(
            evidence_id="identity:domestic_etf:101",
            capability=Capability.IDENTITY_SEARCH,
            source_id="products",
            content="테스트 국내 ETF",
            product_id="101",
            source_ref="products#101",
        )

        gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {"holding_targets": [], "product_types": ["domestic_etf"]},
            {"identity_search": [product]},
            20,
        )

        params = connection.calls[0][1]
        self.assertIsNone(params[0])
        self.assertEqual(params[2], ["101"])

    @unittest.skip(
        "Issue #34: PostgresDataGateway._holding_search() is no longer "
        "reachable via retrieve() (agent/ext_retrievers.py owns "
        "Capability.HOLDING_SEARCH). Kept for reference."
    )
    def test_holding_search_without_target_or_product_skips_database(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.HOLDING_SEARCH,
            {"holding_targets": [], "product_types": []},
            {},
            20,
        )

        self.assertEqual(connection.calls, [])
        self.assertEqual(batch.total_hits, 0)
        self.assertTrue(batch.coverage_complete)

    def test_vector_search_uses_bound_cosine_query_and_preserves_provenance(self):
        rows = [_vector_row(602, "0.71"), _vector_row(601, "0.83")]
        for row in rows:
            row["_total_hits"] = 2
        connection = FakeConnection(vector_rows=rows)
        embedder = StaticQueryEmbedder()
        gateway = PostgresDataGateway(_provider(connection), embedder)
        candidate = Evidence(
            evidence_id="identity:foreign_etf:202",
            capability=Capability.IDENTITY_SEARCH,
            source_id="identity",
            content="Global AI ETF",
            product_id="202",
        )

        batch = gateway.retrieve(
            Capability.VECTOR_SEARCH,
            {
                "semantic_query": "인공지능 산업 투자 전략",
                "product_types": ["foreign_etf"],
                "filters": [
                    {"field": "fee_rate", "operator": "lte", "value": 0.5}
                ],
            },
            {"identity_search": [candidate]},
            5,
        )

        sql, params = connection.calls[0]
        self.assertIn("strategy.embedding <=> query_input.query_embedding", sql)
        self.assertIn("%s::vector(1024)", sql)
        self.assertLess(sql.index("metrics.product_id::text"), sql.index("ORDER BY"))
        self.assertLess(sql.index('metrics."expense_ratio_pct"'), sql.index("ORDER BY"))
        self.assertTrue(str(params[0]).startswith("["))
        self.assertIn(["202"], params)
        self.assertIn("Qwen/Qwen3-Embedding-0.6B", params)
        self.assertEqual(params[-1], 5)
        self.assertEqual(embedder.queries, ["인공지능 산업 투자 전략"])
        self.assertEqual(batch.total_hits, 2)
        self.assertEqual([item.score for item in batch.evidence], [0.83, 0.71])
        evidence = batch.evidence[0]
        self.assertEqual(evidence.capability, Capability.VECTOR_SEARCH)
        self.assertEqual(evidence.product_id, "202")
        self.assertEqual(evidence.structured["name"], "Global AI ETF")
        self.assertIn("반도체", evidence.content)
        self.assertEqual(evidence.as_of_date, "2026-08-31")
        self.assertEqual(evidence.source_ref, "overseas_etf.xlsx#sheet=전략&row=4")

    def test_vector_search_zero_rows_is_a_valid_empty_batch(self):
        embedder = StaticQueryEmbedder()
        gateway = PostgresDataGateway(_provider(FakeConnection()), embedder)

        batch = gateway.retrieve(
            Capability.VECTOR_SEARCH,
            {"question": "배당 중심 ETF", "product_types": ["foreign_etf"]},
            {},
            5,
        )

        self.assertEqual(batch.evidence, [])
        self.assertEqual(batch.total_hits, 0)
        self.assertTrue(batch.coverage_complete)
        self.assertEqual(embedder.queries, ["배당 중심 ETF"])

    def test_vector_search_skips_non_foreign_product_without_loading_model(self):
        embedder = StaticQueryEmbedder(error=AssertionError("must not encode"))
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection), embedder)

        batch = gateway.retrieve(
            Capability.VECTOR_SEARCH,
            {"question": "반도체", "product_types": ["domestic_etf"]},
            {},
            5,
        )

        self.assertEqual(batch.total_hits, 0)
        self.assertEqual(embedder.queries, [])
        self.assertEqual(connection.calls, [])

    def test_vector_model_load_error_becomes_failed_step(self):
        gateway = PostgresDataGateway(
            _provider(FakeConnection()),
            StaticQueryEmbedder(error=EmbeddingModelLoadError("model unavailable")),
        )
        plan = RetrievalPlan(
            version="test",
            understanding=QueryUnderstanding(question="반도체 해외 ETF"),
            steps=[
                RetrievalStep(
                    step_id="vector_search",
                    capability=Capability.VECTOR_SEARCH,
                    query={"question": "반도체", "product_types": ["foreign_etf"]},
                )
            ],
        )

        result = PlanExecutor(registry_from_gateway(gateway)).execute(plan).step_results[0]

        self.assertEqual(result.outcome, StepOutcome.FAILED)
        self.assertIn("EmbeddingModelLoadError", result.message)

    def test_vector_dimension_mismatch_becomes_failed_step_before_database(self):
        embedder = StaticQueryEmbedder(vector=[0.1, 0.2])
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection), embedder)
        plan = RetrievalPlan(
            version="test",
            understanding=QueryUnderstanding(question="반도체 해외 ETF"),
            steps=[
                RetrievalStep(
                    step_id="vector_search",
                    capability=Capability.VECTOR_SEARCH,
                    query={"question": "반도체", "product_types": ["foreign_etf"]},
                )
            ],
        )

        result = PlanExecutor(registry_from_gateway(gateway)).execute(plan).step_results[0]

        self.assertEqual(result.outcome, StepOutcome.FAILED)
        self.assertIn("EmbeddingDimensionError", result.message)
        self.assertEqual(connection.calls, [])

    def test_vector_database_error_becomes_failed_step_not_empty_result(self):
        gateway = PostgresDataGateway(
            _provider(FakeConnection(error=ConnectionError("database unavailable"))),
            StaticQueryEmbedder(),
        )
        plan = RetrievalPlan(
            version="test",
            understanding=QueryUnderstanding(question="반도체 해외 ETF"),
            steps=[
                RetrievalStep(
                    step_id="vector_search",
                    capability=Capability.VECTOR_SEARCH,
                    query={"question": "반도체", "product_types": ["foreign_etf"]},
                )
            ],
        )

        result = PlanExecutor(registry_from_gateway(gateway)).execute(plan).step_results[0]

        self.assertEqual(result.outcome, StepOutcome.FAILED)
        self.assertIn("ConnectionError", result.message)

    def test_gateway_passes_only_declared_dependency_results(self):
        class CapturingGateway:
            def __init__(self):
                self.upstream = None

            def capability_snapshot(self):
                return CapabilitySnapshot(
                    snapshot_id="test",
                    available=frozenset({Capability.STRUCTURED_SEARCH}),
                    registry_version="test",
                )

            def retrieve(self, capability, query, upstream_evidence, top_k):
                self.upstream = upstream_evidence
                return RetrievalBatch(evidence=[], total_hits=0)

        wanted = Evidence(
            evidence_id="wanted",
            capability=Capability.IDENTITY_SEARCH,
            source_id="test",
            content="wanted",
            product_id="101",
        )
        unrelated = Evidence(
            evidence_id="unrelated",
            capability=Capability.KEYWORD_SEARCH,
            source_id="test",
            content="unrelated",
            product_id="999",
        )
        context = RetrievalContext(
            prior_results={
                "identity_search": StepResult(
                    step_id="identity_search",
                    capability=Capability.IDENTITY_SEARCH,
                    outcome=StepOutcome.SUCCESS,
                    evidence=[wanted],
                ),
                "keyword_search": StepResult(
                    step_id="keyword_search",
                    capability=Capability.KEYWORD_SEARCH,
                    outcome=StepOutcome.SUCCESS,
                    evidence=[unrelated],
                ),
            }
        )
        gateway = CapturingGateway()
        retriever = registry_from_gateway(gateway).get(Capability.STRUCTURED_SEARCH)

        retriever.retrieve(
            RetrievalStep(
                step_id="structured_search",
                capability=Capability.STRUCTURED_SEARCH,
                query={},
                depends_on=["identity_search"],
            ),
            context,
        )

        self.assertEqual(set(gateway.upstream), {"identity_search"})
        self.assertEqual(gateway.upstream["identity_search"][0].product_id, "101")

    def test_database_error_becomes_failed_step_not_empty_result(self):
        secret = "postgresql://user:password@private-db/funds"
        gateway = PostgresDataGateway(
            _provider(FakeConnection(error=ConnectionError(secret)))
        )
        registry = registry_from_gateway(gateway)
        plan = RetrievalPlan(
            version="test",
            understanding=QueryUnderstanding(question="국내 ETF"),
            steps=[
                RetrievalStep(
                    step_id="structured_search",
                    capability=Capability.STRUCTURED_SEARCH,
                    query={"product_types": ["domestic_etf"], "filters": [], "sorts": []},
                )
            ],
        )

        report = PlanExecutor(registry).execute(plan)

        self.assertEqual(report.step_results[0].outcome, StepOutcome.FAILED)
        self.assertNotEqual(report.step_results[0].outcome, StepOutcome.EMPTY)


class PostgresGatewayAdapterIntegrationTest(unittest.TestCase):
    def test_us_region_strategy_succeeds_when_keyword_is_empty(self):
        foreign_row = _vector_row()
        foreign_row["investment_region"] = "United States of America"
        foreign_row["_missing_investment_region"] = 0
        connection = FakeConnection(
            keyword_rows=[],
            vector_rows=[foreign_row],
            structured_rows=[foreign_row],
        )
        pipeline = b_adapter._build_pipeline(
            PostgresDataGateway(_provider(connection), StaticQueryEmbedder())
        )

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            result = b_adapter.answer_question(
                "issue-28",
                "미국 성장주에 집중 투자하는 해외 ETF 전략 알려줘",
            )

        self.assertEqual(result.status, AnswerStatus.ANSWERED)
        self.assertEqual(result.retrieved_context[0].product_id, "202")
        self.assertIn("keyword_search:empty(0)", result.think_trace[1].summary)
        self.assertIn("vector_search:success(1)", result.think_trace[1].summary)
        self.assertIn("structured_search:success(1)", result.think_trace[1].summary)

    def test_answer_uses_structured_search_and_returns_real_context(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        pipeline = b_adapter._build_pipeline(
            PostgresDataGateway(_provider(connection))
        )

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            result = b_adapter.answer_question(
                "issue-8",
                (
                    "순자산 1000억원 이상 국내 ETF를 "
                    "순자산 큰 순으로 1개 추천해줘"
                ),
            )

        self.assertEqual(result.status, AnswerStatus.ANSWERED)
        self.assertEqual(result.retrieved_context[0].product_id, "101")
        self.assertEqual(result.retrieved_context[0].product_name, "테스트 국내 ETF")
        by_field = {item.field: item for item in result.retrieved_context}
        self.assertEqual(by_field["canonical_name"].value, "테스트 국내 ETF")
        self.assertEqual(by_field["canonical_name"].fill_type, FillType.ORIGINAL)
        self.assertEqual(by_field["expense_ratio_pct"].value, 0.15)
        self.assertEqual(
            by_field["expense_ratio_pct"].fill_type,
            FillType.OFFICIAL_FILL,
        )
        self.assertIn("structured_search:success(1)", result.think_trace[1].summary)

    def test_original_issue_question_has_successful_route_and_context(self):
        connection = FakeConnection(structured_rows=[_structured_row()])
        pipeline = b_adapter._build_pipeline(
            PostgresDataGateway(_provider(connection))
        )

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            result = b_adapter.answer_question("test-001", "국내ETF 상품 알려줘")

        self.assertEqual(result.retrieved_context[0].product_id, "101")
        self.assertIn("structured_search:success(1)", result.think_trace[1].summary)

    def test_database_failure_is_a_safe_retrieval_error(self):
        secret = "postgresql://user:password@private-db/funds"
        pipeline = b_adapter._build_pipeline(
            PostgresDataGateway(
                _provider(FakeConnection(error=ConnectionError(secret)))
            )
        )

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            result = b_adapter.answer_question("issue-8-error", "국내ETF 상품 알려줘")

        self.assertEqual(result.status, AnswerStatus.UNANSWERABLE)
        self.assertEqual(result.reason_code, ReasonCode.RETRIEVAL_ERROR)
        self.assertIn("structured_search:failed(0)", result.think_trace[1].summary)
        self.assertNotIn(secret, result.model_dump_json())

    def test_answer_trace_and_context_include_successful_vector_search(self):
        foreign_row = _vector_row()
        foreign_row["_missing_net_assets"] = 0
        connection = FakeConnection(
            keyword_rows=[_keyword_row()],
            vector_rows=[_vector_row()],
            structured_rows=[foreign_row],
        )
        pipeline = b_adapter._build_pipeline(
            PostgresDataGateway(_provider(connection), StaticQueryEmbedder())
        )

        with patch.object(b_adapter, "_PIPELINE", pipeline):
            result = b_adapter.answer_question(
                "issue-11",
                "반도체 관련 해외 ETF 추천해줘",
            )

        self.assertIn("vector_search:success(1)", result.think_trace[1].summary)
        vector_context = [
            item for item in result.retrieved_context if item.search_type == SearchType.VECTOR
        ]
        self.assertEqual(len(vector_context), 1)
        self.assertEqual(vector_context[0].product_id, "202")
        self.assertEqual(
            vector_context[0].source_ref,
            "overseas_etf.xlsx#sheet=전략&row=4",
        )


if __name__ == "__main__":
    unittest.main()
