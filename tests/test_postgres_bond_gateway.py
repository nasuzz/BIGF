from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from b_agent.models import Capability, ProductType
from b_agent.postgres_gateway import PostgresDataGateway


def _bond_row() -> dict:
    return {
        "product_id": "KR123|OTC|1",
        "product_type": "DOMESTIC_BOND",
        "source_product_key": "KR123|OTC|1",
        "canonical_name": "테스트 AA- 회사채",
        "short_name": "테스트채",
        "currency_code": "KRW",
        "issuer_name": "테스트주식회사",
        "credit_rating": "AA-",
        "sale_available": True,
        "yield_pct": Decimal("4.31"),
        "maturity_date": date(2028, 8, 31),
        "remaining_days": 725,
        "risk_label": "낮은위험",
        "has_warning": False,
        "snapshot_id": 17,
        "raw_row_id": 901,
        "source_file_name": "국내채권마스터_filled.xlsx",
        "source_sheet": "채권",
        "source_row_number": 12,
        "data_as_of_date": date(2026, 8, 31),
        "provenance_status": "VERIFIED",
        "_total_hits": 1,
        "_missing_currency": 0,
        "_missing_sale_available": 0,
        "_missing_credit_rating": 0,
        "_missing_yield": 0,
        "_value_provenance": [],
    }


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
        if "core.domestic_bond_quote" in sql:
            self.rows = list(self.connection.bond_rows)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, bond_rows=None):
        self.bond_rows = bond_rows or []
        self.calls = []

    def cursor(self):
        return FakeCursor(self)


def _provider(connection):
    @contextmanager
    def provide():
        yield connection

    return provide


class PostgresBondGatewayTest(unittest.TestCase):
    def test_bond_query_hits_active_table_and_preserves_provenance(self):
        connection = FakeConnection([_bond_row()])
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["bond"],
                "filters": [
                    {"field": "currency", "operator": "eq", "value": "KRW"},
                    {"field": "sale_available", "operator": "eq", "value": True},
                    {
                        "field": "credit_rating",
                        "operator": "credit_at_least",
                        "value": "AA-",
                    },
                ],
                "sorts": [{"field": "yield", "direction": "desc"}],
            },
            {},
            1,
        )

        sql, params = connection.calls[0]
        self.assertIn("core.domestic_bond_quote", sql)
        self.assertIn("raw.source_row", sql)
        self.assertIn("version.status = 'ACTIVE'", sql)
        self.assertIn("buyable_quantity", sql)
        self.assertIn("bdbns_abl_chnl_tcd", sql)
        self.assertIn("yield_pct DESC", sql)
        self.assertEqual(params, ("KRW", True, 3, 1))

        self.assertEqual(batch.total_hits, 1)
        self.assertTrue(batch.coverage_complete)
        evidence = batch.evidence[0]
        self.assertEqual(evidence.product_type, ProductType.BOND)
        self.assertEqual(evidence.product_id, "KR123|OTC|1")
        self.assertEqual(evidence.structured["credit_rating"], "AA-")
        self.assertTrue(evidence.structured["sale_available"])
        self.assertEqual(evidence.structured["yield"], 4.31)
        self.assertEqual(evidence.as_of_date, "2026-08-31")
        self.assertEqual(evidence.provenance["snapshot_id"], 17)
        self.assertIn("sheet=채권", evidence.source_ref)

    def test_real_zero_match_is_reported_only_after_bond_sql_runs(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {
                "product_types": ["bond"],
                "filters": [
                    {
                        "field": "credit_rating",
                        "operator": "credit_at_least",
                        "value": "AAA",
                    }
                ],
                "sorts": [{"field": "yield", "direction": "desc"}],
            },
            {},
            10,
        )

        self.assertEqual(len(connection.calls), 1)
        self.assertIn("core.domestic_bond_quote", connection.calls[0][0])
        self.assertEqual(batch.evidence, [])
        self.assertEqual(batch.total_hits, 0)
        self.assertTrue(batch.coverage_complete)

    def test_unsupported_product_type_is_not_a_false_zero_match(self):
        connection = FakeConnection()
        gateway = PostgresDataGateway(_provider(connection))

        batch = gateway.retrieve(
            Capability.STRUCTURED_SEARCH,
            {"product_types": ["crypto"], "filters": [], "sorts": []},
            {},
            10,
        )

        self.assertEqual(connection.calls, [])
        self.assertFalse(batch.coverage_complete)
        self.assertIsNone(batch.total_hits)


if __name__ == "__main__":
    unittest.main()
