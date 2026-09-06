from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from b_agent.models import Capability, Evidence, ProductType, RetrievalBatch, RetrievalStep
from b_agent.retrievers import RetrievalContext, RetrieverRegistry


DB_STATEMENT_TIMEOUT_MS = 8_000
SUPPORTED_SECTIONS = {
    "benchmark",
    "derivative",
    "fee",
    "hedge",
    "investment_asset",
    "mixed",
    "objective",
    "other",
    "redemption",
    "risk",
    "strategy",
    "structure",
    "suitability",
    "tax",
}


class RowFetcher(Protocol):
    def fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]: ...


@dataclass
class PsycopgFetcher:
    dsn: str
    statement_timeout_ms: int = DB_STATEMENT_TIMEOUT_MS

    def fetch_all(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
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


class PostgresProspectusRetriever:
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
                coverage_note="선행 상품 식별 결과가 없어 투자설명서를 임의 상품에 연결하지 않았습니다.",
                total_hits=0,
            )

        # identity_search can resolve one product mention/identifier to several
        # ambiguous candidates (issue #42). Querying with all of them would
        # confidently serve another product's prospectus text as if it were
        # the requested one, so an unconfirmed single target must not search.
        if product_references and len(product_ids) > 1:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="상품 식별 후보가 여러 개여서 투자설명서를 특정 상품에 연결하지 않았습니다.",
                total_hits=0,
            )

        requested_sections = [
            str(value)
            for value in step.query.get("document_sections") or []
            if str(value) in SUPPORTED_SECTIONS
        ]
        unsupported_sections = {
            str(value)
            for value in step.query.get("document_sections") or []
            if str(value) not in SUPPORTED_SECTIONS
        }
        if unsupported_sections and not requested_sections:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=False,
                coverage_note=(
                    "현재 투자설명서 데이터에 없는 문서 구역입니다: "
                    + ", ".join(sorted(unsupported_sections))
                ),
                total_hits=0,
            )

        query_text = _query_text(step, product_ids, requested_sections)
        rows = self.fetcher.fetch_all(
            _DOCUMENT_SQL,
            (
                product_ids or None,
                requested_sections or None,
                query_text,
                min(max(step.top_k, 1), 40),
            ),
        )
        evidence = [_document_evidence(row, rank) for rank, row in enumerate(rows, start=1)]
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=True,
            coverage_note=(
                "OpenDART 기준일 2026-08-24의 사용 가능 투자설명서 1,211건에서 "
                "반복 문구를 제외한 검색 청크를 조회했습니다. 전문 미확보 2건은 제외됩니다."
            ),
            total_hits=len(rows),
            truncated=len(rows) >= min(max(step.top_k, 1), 40),
        )


def register_prospectus_retriever(
    registry: RetrieverRegistry,
    *,
    dsn: str | None = None,
    fetcher: RowFetcher | None = None,
) -> RetrieverRegistry:
    active_fetcher = fetcher
    if active_fetcher is None:
        resolved_dsn = dsn or os.getenv("DATABASE_URL")
        if not resolved_dsn:
            raise RuntimeError("DATABASE_URL is required to register the prospectus retriever")
        active_fetcher = PsycopgFetcher(resolved_dsn)

    registry.register(Capability.DOCUMENT_SEARCH, PostgresProspectusRetriever(active_fetcher))
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


def _query_text(
    step: RetrievalStep,
    product_ids: list[int],
    requested_sections: list[str],
) -> str | None:
    # For an identified product and a planner-selected section, ordered section
    # chunks are more reliable than requiring every word of the natural-language
    # question to appear in the same chunk.
    if product_ids and requested_sections:
        return None

    for values_key in ("themes", "entities"):
        for value in step.query.get(values_key) or []:
            text = str(value).strip()
            if text:
                return text

    question = str(step.query.get("question") or "").strip()
    return question or None


def _document_evidence(row: dict[str, Any], rank: int) -> Evidence:
    product_name = str(row.get("product_name") or row.get("product_id"))
    section_type = str(row.get("section_type") or "other")
    heading = str(row.get("heading") or section_type)
    record_id = str(row.get("source_record_key") or "") or None
    return Evidence(
        evidence_id=f"document:prospectus:{record_id or rank}",
        capability=Capability.DOCUMENT_SEARCH,
        source_id="ext.search_prospectus_chunks",
        content=str(row.get("chunk_text") or ""),
        score=float(row.get("relevance") or 0.0),
        record_id=record_id,
        product_id=str(row.get("product_id")),
        product_type=ProductType.DOMESTIC_ETF,
        as_of_date=_optional_str(row.get("rcept_dt")),
        source_ref=_optional_str(row.get("source_url")),
        structured={
            "name": product_name,
            "product_name": product_name,
            "source_product_key": _optional_str(row.get("source_product_key")),
            "section": section_type,
            "section_type": section_type,
            "heading": heading,
            "rcept_no": _optional_str(row.get("rcept_no")),
        },
        provenance=dict(row.get("provenance") or {}),
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


_DOCUMENT_SQL = """
SELECT *
FROM ext.search_prospectus_chunks(
    p_product_ids => %s,
    p_section_types => %s,
    p_query => %s,
    p_limit => %s
)
"""
