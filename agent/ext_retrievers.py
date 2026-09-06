from __future__ import annotations

import calendar
import os
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from b_agent.models import Capability, Evidence, ProductType, RetrievalBatch, RetrievalStep
from b_agent.retrievers import RetrievalContext, RetrieverRegistry


DB_STATEMENT_TIMEOUT_MS = 8_000
# One normalized target per retrieval keeps the external-data stage inside the
# 8-second DB budget, leaving time for routing, fusion, and response rendering.
MAX_QUERY_TERMS = 1


class RowFetcher(Protocol):
    def fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]: ...


@dataclass
class PsycopgFetcher:
    dsn: str
    statement_timeout_ms: int = DB_STATEMENT_TIMEOUT_MS

    def fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        # Import lazily so unit tests and non-DB tooling do not require psycopg.
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as connection:
            connection.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (f"{self.statement_timeout_ms}ms",),
            )
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]


class PostgresHoldingRetriever:
    def __init__(self, fetcher: RowFetcher) -> None:
        self.fetcher = fetcher

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        product_ids = _upstream_product_ids(context)
        product_references = [
            *(step.query.get("product_mentions") or []),
            *(step.query.get("identifiers") or []),
        ]
        all_targets = _holding_targets(step, context)
        targets = all_targets[:MAX_QUERY_TERMS]
        # Issue #34 follow-up (A's review on PR #40): when relation_search
        # resolves to more entities than we can query within the time budget,
        # the unexamined ones must not be silently treated as "no match".
        targets_truncated = len(all_targets) > len(targets)

        if product_references and not product_ids and not targets:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="선행 상품 식별 결과가 없어 보유종목을 임의 상품에 연결하지 않았습니다.",
                total_hits=0,
            )

        terms: list[str | None] = targets or [None]
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        started = time.monotonic()
        examined_all_terms = True
        for term in terms:
            if time.monotonic() - started >= DB_STATEMENT_TIMEOUT_MS / 1_000:
                examined_all_terms = False
                break
            fetched = self.fetcher.fetch_all(
                _HOLDING_SQL,
                (
                    term,
                    product_ids or None,
                    _database_product_types(step.query.get("product_types") or []) or None,
                    True,
                    min(max(step.top_k, 1), 500),
                ),
            )
            for row in fetched:
                # product_id is required in the key: the same source_record_key
                # (e.g. a SEC accession_number+holding_id) can legitimately map
                # to more than one product, and those are distinct evidence.
                record_key = str(row.get("source_record_key") or "")
                key = (str(row.get("product_id") or ""), record_key)
                if record_key and key not in seen:
                    seen.add(key)
                    rows.append(row)

        rows.sort(
            key=lambda row: (
                _numeric_sort_value(row.get("weight_pct")),
                str(row.get("product_name") or ""),
            ),
            reverse=True,
        )
        rows = rows[: min(max(step.top_k, 1), 500)]

        coverage_complete = examined_all_terms and not targets_truncated
        coverage_note = (
            "현재 매핑된 ETF의 최신 적재 기준일 보유종목을 조회했습니다."
            if coverage_complete
            else "시간 예산 제약으로 일부 대상만 조회했습니다; 결과가 불완전할 수 있습니다."
        )
        evidence = [_holding_evidence(row, rank) for rank, row in enumerate(rows, start=1)]
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=coverage_complete,
            coverage_note=coverage_note,
            total_hits=len(rows),
            truncated=len(rows) >= step.top_k,
        )


class PostgresOpenDartEventRetriever:
    def __init__(self, fetcher: RowFetcher) -> None:
        self.fetcher = fetcher

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        product_ids = _upstream_product_ids(context)
        product_references = [
            *(step.query.get("product_mentions") or []),
            *(step.query.get("identifiers") or []),
        ]
        if product_references and not product_ids:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="선행 상품 식별 결과가 없어 공시를 임의 상품에 연결하지 않았습니다.",
                total_hits=0,
            )

        company_query = _first_text(step.query.get("entities") or [])
        start_date, end_date = _temporal_bounds(step.query.get("temporal"))
        rows = self.fetcher.fetch_all(
            _EVENT_SQL,
            (
                company_query,
                None,
                product_ids or None,
                start_date,
                end_date,
                min(max(step.top_k, 1), 500),
            ),
        )
        evidence = [_event_evidence(row, rank) for rank, row in enumerate(rows, start=1)]
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=True,
            coverage_note="적재된 OpenDART 수집 범위에서 운용사 연결 공시를 조회했습니다.",
            total_hits=len(rows),
            truncated=len(rows) >= step.top_k,
        )


def register_ext_retrievers(
    registry: RetrieverRegistry,
    *,
    dsn: str | None = None,
    fetcher: RowFetcher | None = None,
) -> RetrieverRegistry:
    active_fetcher = fetcher
    if active_fetcher is None:
        resolved_dsn = dsn or os.getenv("DATABASE_URL")
        if not resolved_dsn:
            raise RuntimeError("DATABASE_URL is required to register ext retrievers")
        active_fetcher = PsycopgFetcher(resolved_dsn)

    registry.register(Capability.HOLDING_SEARCH, PostgresHoldingRetriever(active_fetcher))
    registry.register(Capability.EVENT_SEARCH, PostgresOpenDartEventRetriever(active_fetcher))
    return registry


def _upstream_product_ids(context: RetrievalContext) -> list[int]:
    product_ids: set[int] = set()
    for result in context.prior_results.values():
        for item in result.evidence:
            try:
                product_ids.add(int(str(item.product_id)))
            except (TypeError, ValueError):
                continue
    return sorted(product_ids)


def _holding_targets(step: RetrievalStep, context: RetrievalContext) -> list[str]:
    targets = [str(item).strip() for item in step.query.get("holding_targets") or [] if str(item).strip()]
    for constraint in step.query.get("relation_constraints") or []:
        result_role = constraint.get("result_role")
        keys = (
            ("source_entity_id", "source_entity_name")
            if result_role == "source"
            else ("target_entity_id", "target_entity_name")
        )
        for result in context.prior_results.values():
            for item in result.evidence:
                if item.capability != Capability.RELATION_SEARCH:
                    continue
                for key in keys:
                    value = item.structured.get(key)
                    if value is not None and str(value).strip():
                        targets.append(str(value).strip())
    return list(dict.fromkeys(targets))


def _database_product_types(values: list[str]) -> list[str]:
    mapping = {
        ProductType.DOMESTIC_ETF.value: "DOMESTIC_ETP",
        ProductType.FOREIGN_ETF.value: "OVERSEAS_ETP",
        "DOMESTIC_ETP": "DOMESTIC_ETP",
        "OVERSEAS_ETP": "OVERSEAS_ETP",
    }
    return list(dict.fromkeys(mapping[value] for value in values if value in mapping))


def _product_type(value: Any) -> ProductType | None:
    mapping = {
        "DOMESTIC_ETP": ProductType.DOMESTIC_ETF,
        "OVERSEAS_ETP": ProductType.FOREIGN_ETF,
    }
    return mapping.get(str(value or "").upper())


def _holding_evidence(row: dict[str, Any], rank: int) -> Evidence:
    product_name = str(row.get("product_name") or row.get("product_id"))
    holding_name = str(row.get("holding_name") or row.get("holding_key"))
    weight = row.get("weight_pct")
    weight_text = f", 비중 {weight}%" if weight is not None else ""
    source_ref = str(row.get("source_record_key") or "") or None
    structured = {
        "name": product_name,
        "product_name": product_name,
        "holding_id": _optional_str(row.get("holding_key")),
        "holding_name": holding_name,
        "holding_ticker": _optional_str(row.get("holding_ticker")),
        "holding_isin": _optional_str(row.get("holding_isin")),
        "asset_type": _optional_str(row.get("asset_type")),
        "quantity": _json_number(row.get("quantity")),
        "market_value": _json_number(row.get("market_value")),
        "weight_pct": _json_number(weight),
        "source_name": _optional_str(row.get("source_name")),
    }
    return Evidence(
        evidence_id=f"holding:{row.get('product_id')}:{source_ref or rank}",
        capability=Capability.HOLDING_SEARCH,
        source_id="ext.api_etf_holding",
        content=f"{product_name}의 보유종목: {holding_name}{weight_text}",
        score=max(0.0, 1.0 - (rank - 1) * 0.001),
        record_id=source_ref,
        product_id=str(row.get("product_id")),
        product_type=_product_type(row.get("product_type")),
        as_of_date=_optional_str(row.get("as_of_date")),
        source_ref=source_ref,
        structured=structured,
        provenance={
            "source": _optional_str(row.get("source_name")),
            "as_of_date": _optional_str(row.get("as_of_date")),
            "mapping": "ext.etf_product_mapping",
        },
    )


def _event_evidence(row: dict[str, Any], rank: int) -> Evidence:
    product_name = str(row.get("product_name") or row.get("product_id"))
    manager_name = str(row.get("manager_name") or row.get("corp_name") or "")
    report_name = str(row.get("report_nm") or row.get("rcept_no"))
    source_ref = _optional_str(row.get("source_url"))
    return Evidence(
        evidence_id=f"event:opendart:{row.get('rcept_no')}:{row.get('product_id')}",
        capability=Capability.EVENT_SEARCH,
        source_id="ext.api_product_opendart_disclosure",
        content=f"{product_name}의 운용사 {manager_name} 공시: {report_name}",
        score=max(0.0, 1.0 - (rank - 1) * 0.001),
        record_id=_optional_str(row.get("rcept_no")),
        product_id=str(row.get("product_id")),
        as_of_date=_optional_str(row.get("rcept_dt")),
        source_ref=source_ref,
        structured={
            "name": product_name,
            "product_name": product_name,
            "manager_name": manager_name,
            "entity_id": _optional_str(row.get("corp_code")),
            "corp_code": _optional_str(row.get("corp_code")),
            "corp_name": _optional_str(row.get("corp_name")),
            "report_name": report_name,
            "disclosure_type": _optional_str(row.get("disclosure_type")),
        },
        provenance={
            "source": "OpenDART",
            "source_batch": _optional_str(row.get("source_batch")),
            "mapping": "ext.product_opendart_company_mapping",
        },
    )


def _temporal_bounds(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    start = _optional_str(value.get("start_date"))
    end = _optional_str(value.get("end_date"))
    if start or end:
        return start, end
    months = value.get("relative_months")
    try:
        relative_months = int(months)
    except (TypeError, ValueError):
        return None, None
    today = date.today()
    return _subtract_months(today, max(1, relative_months)).isoformat(), today.isoformat()


def _subtract_months(value: date, months: int) -> date:
    zero_based = value.year * 12 + value.month - 1 - months
    year, month_index = divmod(zero_based, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _first_text(values: list[Any]) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_sort_value(value: Any) -> float:
    parsed = _json_number(value)
    return float(parsed) if parsed is not None else float("-inf")


_HOLDING_SQL = """
SELECT *
FROM ext.search_etf_holdings(
    p_holding_query => %s,
    p_product_ids => %s,
    p_product_types => %s,
    p_latest_only => %s,
    p_limit => %s
)
"""


_EVENT_SQL = """
SELECT *
FROM ext.search_opendart_events(
    p_company_query => %s,
    p_event_query => %s,
    p_product_ids => %s,
    p_start_date => %s,
    p_end_date => %s,
    p_limit => %s
)
"""
