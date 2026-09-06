from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ContextManager, Protocol

from .embeddings import (
    DEFAULT_DIMENSION,
    EmbeddingDimensionError,
    EmbeddingGenerationError,
    QueryEmbedder,
    default_query_embedder,
)
from .gateway import CapabilitySnapshot, DataGateway
from .models import (
    Capability,
    Evidence,
    ProductType,
    RetrievalBatch,
    ValueProvenance,
)
from .ontology import DEFAULT_ONTOLOGY, canonical_region, database_region_values
from .relation_results import relation_result_values


class CursorLike(Protocol):
    description: Sequence[Any] | None

    def execute(self, query: str, params: Sequence[Any] | None = None) -> None: ...

    def fetchall(self) -> list[Any]: ...

    def __enter__(self) -> "CursorLike": ...

    def __exit__(self, exc_type, exc, traceback) -> None: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...


ConnectionProvider = Callable[[], ContextManager[ConnectionLike]]


class UnsupportedGatewayQuery(ValueError):
    """Raised when B asks for a filter that A's SQL contract cannot represent."""


_AVAILABLE_CAPABILITIES = frozenset(
    {
        Capability.IDENTITY_SEARCH,
        Capability.STRUCTURED_SEARCH,
        Capability.KEYWORD_SEARCH,
        Capability.VECTOR_SEARCH,
        # HOLDING_SEARCH is intentionally not advertised here. Issue #34:
        # agent/ext_retrievers.py's PostgresHoldingRetriever is the single
        # owner of Capability.HOLDING_SEARCH (verified on NCP against
        # ext.search_etf_holdings()/ext.search_opendart_events()). Advertising
        # it here too would let registry_from_gateway() register this class's
        # _holding_search() first, only for register_ext_retrievers() to
        # silently overwrite it in agent/b_adapter.py._build_pipeline().
        Capability.RELATION_SEARCH,
    }
)

_PRODUCT_TYPE_TO_DB = {
    ProductType.PUBLIC_FUND.value: "PUBLIC_FUND_CLASS",
    ProductType.DOMESTIC_ETF.value: "DOMESTIC_ETP",
    ProductType.FOREIGN_ETF.value: "OVERSEAS_ETP",
}
_DB_TO_PRODUCT_TYPE = {
    value: ProductType(key) for key, value in _PRODUCT_TYPE_TO_DB.items()
}
_DB_TO_PRODUCT_TYPE["DOMESTIC_BOND"] = ProductType.BOND

_CREDIT_RATINGS = (
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC",
    "CC",
    "C",
    "D",
)
_CREDIT_RANK = {rating: rank for rank, rating in enumerate(_CREDIT_RATINGS)}
_BOND_CREDIT_RANK_SQL = "CASE replace(upper(btrim(credit_rating)), '0', '') " + " ".join(
    f"WHEN '{rating}' THEN {rank}" for rank, rating in enumerate(_CREDIT_RATINGS)
) + " END"

_FILTER_COLUMNS = {
    "sale_available": "sale_available",
    "currency": "currency_code",
    "asset_type": "asset_type",
    "investment_region": "investment_region",
    "net_assets": "asset_amount",
    "fee_rate": "expense_ratio_pct",
    "one_year_return": "return_1y_pct",
    "return_1y": "return_1y_pct",
    "risk_grade": "risk_label",
}

_SORTS = {
    ("net_assets", "desc"): "asset_amount_desc",
    ("net_assets", "asc"): "asset_amount_asc",
    ("one_year_return", "desc"): "return_1y_desc",
    ("one_year_return", "asc"): "return_1y_asc",
    ("return_1y", "desc"): "return_1y_desc",
    ("return_1y", "asc"): "return_1y_asc",
    ("fee_rate", "asc"): "expense_ratio_asc",
    ("fee_rate", "desc"): "expense_ratio_desc",
    ("name", "asc"): "name_asc",
}
_SORT_SQL = {
    "asset_amount_desc": "asset_amount DESC NULLS LAST, canonical_name ASC",
    "asset_amount_asc": "asset_amount ASC NULLS LAST, canonical_name ASC",
    "return_1y_desc": "return_1y_pct DESC NULLS LAST, canonical_name ASC",
    "return_1y_asc": "return_1y_pct ASC NULLS LAST, canonical_name ASC",
    "expense_ratio_asc": "expense_ratio_pct ASC NULLS LAST, canonical_name ASC",
    "expense_ratio_desc": "expense_ratio_pct DESC NULLS LAST, canonical_name ASC",
    "name_asc": "canonical_name ASC",
}

_VALUE_PROVENANCE_SQL = """
    COALESCE(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'field_name', value_source.field_name,
                    'fill_type', value_source.fill_type,
                    'evidence_eligible', value_source.evidence_eligible,
                    'was_missing', value_source.was_missing,
                    'source_reference', value_source.source_reference,
                    'source_as_of_date', value_source.source_as_of_date,
                    'fill_confidence', value_source.fill_confidence
                )
                ORDER BY value_source.field_name
            )
            FROM meta.value_provenance value_source
            WHERE value_source.raw_row_id = {raw_row_reference}
        ),
        '[]'::jsonb
    ) AS _value_provenance
"""


class PostgresDataGateway(DataGateway):
    """Production DataGateway backed by A's PostgreSQL search functions.

    The connection provider is injected so the API can share its process-wide
    pool and tests can use a small DB-API compatible fake.
    """

    REGISTRY_VERSION = "postgres-search-v4"

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        query_embedder: QueryEmbedder | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._query_embedder = query_embedder or default_query_embedder()

    def capability_snapshot(self) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            snapshot_id=self.REGISTRY_VERSION,
            available=_AVAILABLE_CAPABILITIES,
            registry_version=self.REGISTRY_VERSION,
        )

    def retrieve(
        self,
        capability: Capability,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch:
        limit = max(1, min(int(top_k), 100))
        if capability == Capability.IDENTITY_SEARCH:
            return self._identity_search(query, limit)
        if capability == Capability.STRUCTURED_SEARCH:
            return self._structured_search(query, upstream_evidence, limit)
        if capability == Capability.KEYWORD_SEARCH:
            return self._keyword_search(query, limit)
        if capability == Capability.VECTOR_SEARCH:
            return self._vector_search(query, upstream_evidence, limit)
        if capability == Capability.RELATION_SEARCH:
            return self._relation_search(query, limit)
        # See the _AVAILABLE_CAPABILITIES comment: HOLDING_SEARCH is owned by
        # agent/ext_retrievers.py, not this gateway. _holding_search() below
        # is kept for its tests/history but is intentionally unreachable here.
        raise UnsupportedGatewayQuery(
            f"PostgresDataGateway does not support {capability.value}"
        )

    def unsupported_reason(self, capability: Capability, query: dict) -> str | None:
        """Check the storage contract during planning without querying the DB."""
        if capability != Capability.STRUCTURED_SEARCH:
            return None
        types = set(query.get("product_types") or [])
        if types == {ProductType.BOND.value}:
            return None  # The dedicated bond contract is validated separately.
        try:
            if len(query.get("sorts") or []) > 1:
                return "현재 PostgreSQL 검색은 복수 정렬 조건을 지원하지 않습니다."
            _sort_parameter(query)
            _structured_where(query, _database_product_types(query), None)
        except UnsupportedGatewayQuery:
            fields = _unique_strings([
                item.get("field")
                for item in [*(query.get("filters") or []), *(query.get("sorts") or [])]
            ])
            return "현재 PostgreSQL 데이터에서 지원하지 않는 조건·정렬입니다: " + ", ".join(fields)
        return None

    def _identity_search(self, query: dict, top_k: int) -> RetrievalBatch:
        terms = _unique_strings(
            [*(query.get("identifiers") or []), *(query.get("product_mentions") or [])]
        )
        if not terms:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="식별 검색어가 없습니다.",
                total_hits=0,
            )

        allowed_types = _database_product_types(query)
        if query.get("product_types") and not allowed_types:
            return _unsupported_product_type_batch()

        # 상품군을 함수 호출 전에 확정해야 다른 상품군의 상위 결과가
        # find_products()의 100건 제한을 선점해도 요청 상품을 놓치지 않는다.
        query_types = allowed_types or [None]
        sql = f"""
            WITH query_terms AS (
                SELECT term, term_order
                FROM unnest(%s::text[]) WITH ORDINALITY AS input(term, term_order)
            ),
            query_types AS (
                SELECT product_type, type_order
                FROM unnest(%s::text[]) WITH ORDINALITY
                     AS input(product_type, type_order)
            ),
            matches AS MATERIALIZED (
                SELECT query_terms.term AS matched_term,
                       query_terms.term_order,
                       query_types.product_type AS requested_product_type,
                       query_types.type_order,
                       result.*
                FROM query_terms
                CROSS JOIN query_types
                CROSS JOIN LATERAL search.find_products(
                    query_terms.term,
                    query_types.product_type,
                    100
                ) AS result
            )
            SELECT matches.*,
                   product.raw_row_id,
                   {_value_provenance_select('product.raw_row_id')},
                   count(*) OVER (
                       PARTITION BY term_order, type_order
                   ) AS _partition_hits
            FROM matches
            JOIN core.product product
              ON product.product_id = matches.product_id
            ORDER BY term_order, type_order, match_score DESC, matches.canonical_name
        """
        rows = self._query(sql, (terms, query_types))

        by_product: dict[str, dict[str, Any]] = {}
        matched_terms: dict[str, list[str]] = defaultdict(list)
        products_per_term: dict[str, set[str]] = defaultdict(set)
        # Resolve each expression across all requested product types before
        # deduplication/top_k. Fuzzy aliases must not make an exact match
        # ambiguous, while shared exact aliases must retain every candidate.
        exact_terms = {
            str(row["matched_term"])
            for row in rows
            if row.get("match_type") == "EXACT"
        }
        for row in rows:
            product_id = str(row["product_id"])
            term = str(row["matched_term"])
            if term in exact_terms and row.get("match_type") != "EXACT":
                continue
            products_per_term[term].add(product_id)
            if term not in matched_terms[product_id]:
                matched_terms[product_id].append(term)
            current = by_product.get(product_id)
            if current is None or _number(row.get("match_score")) > _number(
                current.get("match_score")
            ):
                by_product[product_id] = row

        ordered = sorted(
            by_product.values(),
            key=lambda item: (
                -_number(item.get("match_score")),
                str(item.get("canonical_name") or ""),
            ),
        )
        evidence = [
            _identity_evidence(row, matched_terms[str(row["product_id"])])
            for row in ordered[:top_k]
        ]
        unresolved = [term for term in terms if not products_per_term.get(term)]
        capped = any(int(row.get("_partition_hits") or 0) >= 100 for row in rows)
        coverage_notes: list[str] = []
        if unresolved:
            coverage_notes.append(f"미해결 식별 표현: {', '.join(unresolved)}")
        if capped:
            coverage_notes.append(
                "상품군별 최대 100건까지 조회되어 "
                "전체 일치 건수는 확정할 수 없습니다."
            )
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=not coverage_notes,
            coverage_note=(
                "; ".join(coverage_notes)
                if coverage_notes
                else "ACTIVE 데이터 버전의 상품 별칭을 조회했습니다."
            ),
            total_hits=None if capped else len(ordered),
            truncated=capped or len(ordered) > top_k,
            ambiguous=any(len(products) > 1 for products in products_per_term.values()),
        )

    def _structured_search(
        self,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch:
        requested_types = set(_unique_strings(query.get("product_types") or []))
        if ProductType.BOND.value in requested_types:
            if requested_types == {ProductType.BOND.value}:
                return self._bond_structured_search(query, upstream_evidence, top_k)
            # A question with no explicit single product type defaults to every
            # known type, bond included. Bond's schema doesn't compose with the
            # shared ETF/fund structured search below, so drop it instead of
            # failing this required step outright for the common no-type-filter
            # case (previously: every such query became unanswerable).
            query = {
                **query,
                "product_types": [
                    item
                    for item in query.get("product_types") or []
                    if item != ProductType.BOND.value
                ],
            }

        allowed_types = _database_product_types(query)
        if query.get("product_types") and not allowed_types:
            return _unsupported_product_type_batch()

        sort_by = _sort_parameter(query)
        if any(item.get("field") == "sale_available" for item in query.get("filters") or []):
            return self._sale_structured_search(query, upstream_evidence, top_k, allowed_types)
        order_by = _SORT_SQL[sort_by]
        candidate_ids = _candidate_product_ids(upstream_evidence)
        where_sql, where_params = _structured_where(query, allowed_types, candidate_ids)
        coverage_fields = _coverage_fields(query)
        coverage_sql = "".join(
            f', count(*) FILTER (WHERE "{column}" IS NULL) OVER () AS "_missing_{field}"'
            for field, column in coverage_fields.items()
        )

        if _can_use_filter_function(query, allowed_types, candidate_ids):
            product_type = allowed_types[0] if allowed_types else None
            function_params = _filter_function_params(query, product_type)
            stats_coverage_sql = "".join(
                f', count(*) FILTER (WHERE metrics."{column}" IS NULL) '
                f'AS "_missing_{field}"'
                for field, column in coverage_fields.items()
            )
            sql = f"""
                WITH function_results AS MATERIALIZED (
                    SELECT *
                    FROM search.filter_products(
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, 100
                    )
                ),
                statistics AS (
                    SELECT count(*) AS _total_hits
                           {stats_coverage_sql}
                    FROM search.product_metrics metrics
                    WHERE {_active_version_predicate('metrics')}
                      AND {where_sql}
                ),
                limited_results AS (
                    SELECT *
                    FROM function_results
                    ORDER BY {order_by}
                    LIMIT %s
                )
                SELECT limited_results.*, statistics.*,
                       {_value_provenance_select('limited_results.raw_row_id')}
                FROM limited_results
                CROSS JOIN statistics
                ORDER BY {order_by}
            """
            params = (
                *function_params,
                sort_by,
                *where_params,
                top_k,
            )
        else:
            # 복수 상품군과 선행 후보 ID는 함수의 내부 LIMIT보다 먼저
            # 적용할 수 없으므로 canonical view에서 필터링한 뒤 제한한다.
            sql = f"""
                WITH filtered AS MATERIALIZED (
                    SELECT metrics.*
                    FROM search.product_metrics metrics
                    WHERE {_active_version_predicate('metrics')}
                      AND {where_sql}
                )
                SELECT filtered.*,
                       {_value_provenance_select('filtered.raw_row_id')},
                       count(*) OVER () AS _total_hits
                       {coverage_sql}
                FROM filtered
                ORDER BY {order_by}
                LIMIT %s
            """
            params = (*where_params, top_k)
        rows = self._query(sql, params)
        total_hits = int(rows[0].get("_total_hits") or 0) if rows else 0
        coverage_by_field: dict[str, dict[str, int]] = {}
        for field in coverage_fields:
            missing = int(rows[0].get(f"_missing_{field}") or 0) if rows else 0
            coverage_by_field[field] = {
                "population": total_hits,
                "present": max(total_hits - missing, 0),
                "missing": missing,
            }
        incomplete = [
            f"{field} {counts['missing']}/{counts['population']}건 결측"
            for field, counts in coverage_by_field.items()
            if counts["missing"]
        ]
        evidence = [
            _structured_evidence(row, rank)
            for rank, row in enumerate(rows, start=1)
        ]
        return RetrievalBatch(
            evidence=evidence,
            coverage_complete=not incomplete,
            coverage_note=(
                "; ".join(incomplete)
                if incomplete
                else (
                    "ACTIVE 데이터 버전에서 요청한 구조화 필드를 "
                    "조회했습니다."
                )
            ),
            coverage_by_field=coverage_by_field,
            total_hits=total_hits,
            truncated=total_hits > top_k,
        )

    def _sale_structured_search(
        self, query: dict, upstream_evidence: dict[str, list[Evidence]],
        top_k: int, allowed_types: list[str],
    ) -> RetrievalBatch:
        """Filter known master statuses while counting unknowns before filtering."""
        sale_filters = [item for item in query.get("filters") or []
                        if item.get("field") == "sale_available"]
        other_query = {**query, "filters": [item for item in query.get("filters") or []
                                           if item.get("field") != "sale_available"]}
        where_sql, where_params = _structured_where(
            other_query, allowed_types, _candidate_product_ids(upstream_evidence)
        )
        conditions = []
        for spec in sale_filters:
            _validate_sale_filter(spec)
            expected = spec["value"] if spec["operator"] == "eq" else not spec["value"]
            conditions.append("sale_available IS " + ("TRUE" if expected else "FALSE"))
        sale_condition = " AND ".join(conditions)
        order_by = _SORT_SQL[_sort_parameter(query)]
        coverage_fields = _coverage_fields(other_query)
        coverage_sql = "".join(
            f', count(*) FILTER (WHERE {sale_condition} AND "{column}" IS NULL) '
            f'AS "_missing_{field}"' for field, column in coverage_fields.items()
        )
        sql = f"""
            WITH population AS MATERIALIZED (
                SELECT metrics.*, availability.sale_available,
                       availability.sale_available_reason,
                       availability.sale_available_scope,
                       availability.sale_status_conflict,
                       availability.status_basis_date AS sale_status_basis_date
                FROM search.product_metrics metrics
                LEFT JOIN core.v_product_sale_availability availability
                  ON availability.product_id = metrics.product_id
                 AND availability.raw_row_id = metrics.raw_row_id
                 AND availability.snapshot_id = metrics.snapshot_id
                WHERE {_active_version_predicate('metrics')}
                  AND {where_sql}
            ), statistics AS (
                SELECT count(*) AS _sale_population,
                       count(*) FILTER (WHERE sale_available IS NULL) AS _missing_sale_available,
                       count(*) FILTER (WHERE {sale_condition}) AS _total_hits
                       {coverage_sql}
                FROM population
            ), limited_results AS (
                SELECT * FROM population WHERE {sale_condition}
                ORDER BY {order_by} LIMIT %s
            )
            SELECT limited_results.*, statistics.*,
                   {_value_provenance_select('limited_results.raw_row_id')}
            FROM statistics LEFT JOIN limited_results ON true
            ORDER BY {order_by}
        """
        rows = self._query(sql, (*where_params, top_k))
        stats = rows[0] if rows else {}
        total_hits = int(stats.get("_total_hits") or 0)
        population = int(stats.get("_sale_population") or 0)
        missing = int(stats.get("_missing_sale_available") or 0)
        coverage = {"sale_available": {
            "population": population, "present": population - missing, "missing": missing,
        }}
        for field in coverage_fields:
            field_missing = int(stats.get(f"_missing_{field}") or 0)
            coverage[field] = {"population": total_hits, "present": total_hits - field_missing,
                               "missing": field_missing}
        incomplete = [f"{field} {counts['missing']}/{counts['population']}건 결측"
                      for field, counts in coverage.items() if counts["missing"]]
        if missing:
            incomplete.append("매수 후보 여부를 확인하지 못한 상품이 있어 전체 일치 여부는 확정할 수 없습니다.")
        evidence = []
        for rank, row in enumerate((r for r in rows if r.get("product_id") is not None), start=1):
            item = _structured_evidence(row, rank)
            item.value_provenance.append(ValueProvenance(
                field_name="sale_available", fill_type="original", evidence_eligible=True,
                source_reference=item.source_ref,
                source_as_of_date=row.get("sale_status_basis_date") or item.as_of_date,
            ))
            evidence.append(item)
        return RetrievalBatch(
            evidence=evidence, coverage_complete=not incomplete,
            coverage_note="; ".join(incomplete) if incomplete else "마스터 기준일의 판매·거래 상태로 매수 후보를 조회했습니다.",
            coverage_by_field=coverage, total_hits=total_hits, truncated=total_hits > top_k,
        )

    def _bond_structured_search(
        self,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch:
        candidate_ids = _candidate_product_ids(upstream_evidence)
        where_sql, where_params = _bond_where(query, candidate_ids)
        order_by = _bond_order_by(query)
        coverage_fields = _bond_coverage_fields(query)
        coverage_sql = "".join(
            f', count(*) FILTER (WHERE "{column}" IS NULL) OVER () AS "_missing_{field}"'
            for field, column in coverage_fields.items()
        )

        sql = f"""
            WITH bond_metrics AS MATERIALIZED (
                SELECT concat_ws(
                           '|', bond.pd_no, bond.pd_exg_mkt, bond.info_seq::text
                       ) AS product_id,
                       'DOMESTIC_BOND'::text AS product_type,
                       concat_ws(
                           '|', bond.pd_no, bond.pd_exg_mkt, bond.info_seq::text
                       ) AS source_product_key,
                       bond.product_name AS canonical_name,
                       bond.short_name,
                       bond.currency_code,
                       bond.issuer_name,
                       bond.credit_grade,
                       CASE
                           WHEN nullif(
                               btrim(source_row.payload ->> 'buyable_quantity'), ''
                           ) IS NULL
                             OR nullif(
                               btrim(source_row.payload ->> 'bdbns_abl_chnl_tcd'), ''
                           ) IS NULL
                               THEN NULL
                           WHEN regexp_replace(
                               source_row.payload ->> 'buyable_quantity',
                               '[,[:space:]]', '', 'g'
                           ) ~ '^[0-9]+([.][0-9]+)?$'
                             AND regexp_replace(
                               source_row.payload ->> 'buyable_quantity',
                               '[,[:space:]]', '', 'g'
                             )::numeric > 0
                             AND upper(btrim(
                               source_row.payload ->> 'bdbns_abl_chnl_tcd'
                             )) NOT IN ('0', 'N', 'NO', 'NONE', '불가')
                               THEN TRUE
                           ELSE FALSE
                       END AS sale_available,
                       bond.applied_yield_pct AS yield_pct,
                       bond.maturity_date,
                       bond.remaining_days,
                       bond.risk_name AS risk_label,
                       bond.has_warning,
                       bond.snapshot_id,
                       bond.raw_row_id,
                       snapshot.source_file_name,
                       source_row.source_sheet,
                       source_row.source_row_number,
                       COALESCE(
                           snapshot.data_as_of_date, bond.info_base_date
                       ) AS data_as_of_date,
                       snapshot.provenance_status
                FROM core.domestic_bond_quote bond
                JOIN raw.source_row source_row
                  ON source_row.raw_row_id = bond.raw_row_id
                JOIN meta.dataset_snapshot snapshot
                  ON snapshot.snapshot_id = bond.snapshot_id
                WHERE {_active_version_predicate('bond')}
            ),
            filtered AS MATERIALIZED (
                SELECT bond_metrics.*
                FROM bond_metrics
                WHERE {where_sql}
            )
            SELECT filtered.*,
                   {_value_provenance_select('filtered.raw_row_id')},
                   count(*) OVER () AS _total_hits
                   {coverage_sql}
            FROM filtered
            ORDER BY {order_by}
            LIMIT %s
        """
        rows = self._query(sql, (*where_params, top_k))
        total_hits = int(rows[0].get("_total_hits") or 0) if rows else 0
        coverage_by_field: dict[str, dict[str, int]] = {}
        for field in coverage_fields:
            missing = int(rows[0].get(f"_missing_{field}") or 0) if rows else 0
            coverage_by_field[field] = {
                "population": total_hits,
                "present": max(total_hits - missing, 0),
                "missing": missing,
            }
        incomplete = [
            f"{field} {counts['missing']}/{counts['population']}건 결측"
            for field, counts in coverage_by_field.items()
            if counts["missing"]
        ]
        return RetrievalBatch(
            evidence=[
                _bond_structured_evidence(row, rank)
                for rank, row in enumerate(rows, start=1)
            ],
            coverage_complete=not incomplete,
            coverage_note=(
                "; ".join(incomplete)
                if incomplete
                else "ACTIVE 국내채권 데이터에서 요청한 구조화 필드를 조회했습니다."
            ),
            coverage_by_field=coverage_by_field,
            total_hits=total_hits,
            truncated=total_hits > top_k,
        )

    def _keyword_search(self, query: dict, top_k: int) -> RetrievalBatch:
        allowed_types = set(query.get("product_types") or [])
        if allowed_types and ProductType.FOREIGN_ETF.value not in allowed_types:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="해외 ETF 전략문 대상 상품군이 아닙니다.",
                total_hits=0,
            )

        search_text = " ".join(
            _unique_strings(
                [
                    *(query.get("themes") or []),
                    *(query.get("text_entities") or query.get("entities") or []),
                    *(query.get("product_mentions") or []),
                ]
            )
        )
        if not search_text:
            search_text = str(query.get("question") or "").strip()
        if not search_text:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="전략문 검색어가 없습니다.",
                total_hits=0,
            )

        sql = f"""
            WITH function_results AS MATERIALIZED (
                SELECT *
                FROM search.find_overseas_strategies(%s, 100)
            )
            SELECT function_results.*,
                   product.raw_row_id,
                   {_value_provenance_select('product.raw_row_id')},
                   count(*) OVER () AS _total_hits
            FROM function_results
            JOIN core.product product
              ON product.product_id = function_results.product_id
            LIMIT %s
        """
        rows = self._query(sql, (search_text, top_k))
        capped_total = int(rows[0].get("_total_hits") or 0) if rows else 0
        capped = capped_total >= 100
        return RetrievalBatch(
            evidence=[_keyword_evidence(row) for row in rows],
            coverage_complete=not capped,
            coverage_note=(
                "최대 100건의 전략문 표본을 조회했으며 "
                "전체 일치 건수는 확정할 수 없습니다."
                if capped
                else "ACTIVE 데이터 버전의 해외 ETF 전략문을 조회했습니다."
            ),
            total_hits=None if capped else capped_total,
            truncated=capped or capped_total > top_k,
        )

    def _vector_search(
        self,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch:
        requested_types = set(query.get("product_types") or [])
        if requested_types and ProductType.FOREIGN_ETF.value not in requested_types:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="해외 ETF 전략문 대상 상품군이 아닙니다.",
                total_hits=0,
            )

        search_text = _semantic_search_text(query)
        if not search_text:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="전략문 의미 검색어가 없습니다.",
                total_hits=0,
            )

        if self._query_embedder.dimension != DEFAULT_DIMENSION:
            raise EmbeddingDimensionError(
                "configured query embedding dimension mismatch: "
                f"database expects {DEFAULT_DIMENSION}, "
                f"configured {self._query_embedder.dimension}"
            )
        vector = self._query_embedder.encode_query(search_text)
        if len(vector) != DEFAULT_DIMENSION:
            # Custom embedders are accepted for tests/deployment, so enforce the
            # DB contract again even if their own implementation omitted it.
            raise EmbeddingDimensionError(
                "query embedding dimension mismatch: "
                f"expected {DEFAULT_DIMENSION}, received {len(vector)}"
            )
        vector_value = _pgvector_literal(vector)

        candidate_ids = _combined_candidate_product_ids(query, upstream_evidence)
        where_sql, where_params = _structured_where(
            query,
            [_PRODUCT_TYPE_TO_DB[ProductType.FOREIGN_ETF.value]],
            candidate_ids,
        )
        sql = f"""
            WITH query_input AS (
                SELECT %s::vector({DEFAULT_DIMENSION}) AS query_embedding
            ),
            eligible_products AS MATERIALIZED (
                SELECT metrics.*
                FROM search.product_metrics metrics
                WHERE {_active_version_predicate('metrics')}
                  AND {where_sql}
            ),
            vector_candidates AS MATERIALIZED (
                SELECT strategy.document_id,
                       strategy.raw_text,
                       strategy.normalized_text,
                       strategy.source_field,
                       strategy.embedding_model,
                       strategy.embedded_at,
                       metrics.*,
                       1 - (strategy.embedding <=> query_input.query_embedding)
                           AS cosine_similarity
                FROM search.overseas_etf_strategy strategy
                JOIN eligible_products metrics
                  ON metrics.product_id = strategy.product_id
                 AND metrics.snapshot_id = strategy.snapshot_id
                CROSS JOIN query_input
                WHERE strategy.embedding IS NOT NULL
                  AND strategy.embedding_model = %s
            )
            SELECT vector_candidates.*,
                   {_value_provenance_select('vector_candidates.raw_row_id')},
                   count(*) OVER () AS _total_hits
            FROM vector_candidates
            ORDER BY cosine_similarity DESC, document_id ASC
            LIMIT %s
        """
        rows = self._query(
            sql,
            (
                vector_value,
                *where_params,
                self._query_embedder.model_name,
                top_k,
            ),
        )
        rows.sort(
            key=lambda row: (
                -_number(row.get("cosine_similarity")),
                int(row.get("document_id") or 0),
            )
        )
        total_hits = int(rows[0].get("_total_hits") or 0) if rows else 0
        return RetrievalBatch(
            evidence=[_vector_evidence(row) for row in rows],
            coverage_complete=True,
            coverage_note=(
                "ACTIVE 데이터 버전의 해외 ETF 전략문 임베딩을 조회했습니다."
            ),
            total_hits=total_hits,
            truncated=total_hits > top_k,
        )

    def _holding_search(
        self,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch:
        targets = _holding_targets(query, upstream_evidence)
        candidate_ids = _holding_candidate_product_ids(query, upstream_evidence)
        if not targets and not candidate_ids:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="편입 대상 기업 또는 ETF 식별자가 없습니다.",
                total_hits=0,
            )

        etf_types = {
            _PRODUCT_TYPE_TO_DB[ProductType.DOMESTIC_ETF.value],
            _PRODUCT_TYPE_TO_DB[ProductType.FOREIGN_ETF.value],
        }
        allowed_types = [
            item for item in _database_product_types(query) if item in etf_types
        ]
        if query.get("product_types") and not allowed_types:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="편입종목 검색은 국내·해외 ETF만 지원합니다.",
                total_hits=0,
            )

        target_patterns = [_escaped_like_pattern(item) for item in targets] or None
        sql = f"""
            WITH source_rows AS (
                SELECT 'v_etf_holding'::text AS source_kind,
                       to_jsonb(holding) AS payload
                FROM ext.v_etf_holding holding
                UNION ALL
                SELECT 'v_product_opendart_disclosure'::text AS source_kind,
                       to_jsonb(disclosure) AS payload
                FROM ext.v_product_opendart_disclosure disclosure
            ),
            normalized AS (
                SELECT source_kind,
                       payload,
                       COALESCE(
                           payload ->> 'product_id',
                           payload ->> 'etf_product_id'
                       ) AS product_id,
                       COALESCE(
                           payload ->> 'holding_id',
                           payload ->> 'component_entity_id',
                           payload ->> 'entity_id',
                           payload ->> 'corp_code',
                           payload ->> 'stock_code',
                           payload ->> 'cusip',
                           payload ->> 'cik',
                           payload ->> 'lei'
                       ) AS holding_id,
                       COALESCE(
                           payload ->> 'holding_name',
                           payload ->> 'component_name',
                           payload ->> 'component_security_name',
                           payload ->> 'corp_name',
                           payload ->> 'stock_name',
                           payload ->> 'issuer_name',
                           payload ->> 'name_of_issuer'
                       ) AS holding_name,
                       COALESCE(
                           payload ->> 'weight_pct',
                           payload ->> 'holding_weight_pct',
                           payload ->> 'pct_value',
                           payload ->> 'weight'
                       ) AS holding_weight,
                       COALESCE(
                           payload ->> 'data_as_of_date',
                           payload ->> 'as_of_date',
                           payload ->> 'report_date',
                           payload ->> 'filing_date',
                           payload ->> 'rcept_dt'
                       ) AS holding_as_of_date,
                       COALESCE(
                           payload ->> 'source_ref',
                           payload ->> 'source_url',
                           payload ->> 'accession_number',
                           payload ->> 'rcept_no'
                       ) AS holding_source_ref,
                       concat_ws(
                           ' ',
                           payload ->> 'holding_id',
                           payload ->> 'component_entity_id',
                           payload ->> 'entity_id',
                           payload ->> 'corp_code',
                           payload ->> 'stock_code',
                           payload ->> 'cusip',
                           payload ->> 'cik',
                           payload ->> 'lei',
                           payload ->> 'holding_name',
                           payload ->> 'component_name',
                           payload ->> 'component_security_name',
                           payload ->> 'corp_name',
                           payload ->> 'stock_name',
                           payload ->> 'issuer_name',
                           payload ->> 'name_of_issuer'
                       ) AS holding_search_text
                FROM source_rows
            ),
            matched AS MATERIALIZED (
                SELECT normalized.*,
                       metrics.product_type,
                       metrics.canonical_name,
                       metrics.snapshot_id,
                       metrics.data_as_of_date,
                       metrics.source_file_name,
                       metrics.source_sheet,
                       metrics.source_row_number,
                       metrics.provenance_status,
                       metrics.has_warning
                FROM normalized
                JOIN search.product_metrics metrics
                  ON metrics.product_id::text = normalized.product_id
                WHERE {_active_version_predicate('metrics')}
                  AND (
                      %s::text[] IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM unnest(%s::text[]) AS requested(pattern)
                          WHERE normalized.holding_search_text
                                ILIKE requested.pattern ESCAPE '\\'
                      )
                  )
                  AND (
                      %s::text[] IS NULL
                      OR normalized.product_id = ANY(%s::text[])
                  )
                  AND (
                      %s::text[] IS NULL
                      OR metrics.product_type = ANY(%s::text[])
                  )
                  AND (normalized.holding_id IS NOT NULL
                       OR normalized.holding_name IS NOT NULL)
            ),
            deduplicated AS MATERIALIZED (
                SELECT DISTINCT ON (
                           product_id,
                           COALESCE(holding_id, holding_name)
                       )
                       *
                FROM matched
                ORDER BY product_id,
                         COALESCE(holding_id, holding_name),
                         CASE source_kind
                             WHEN 'v_etf_holding' THEN 0
                             ELSE 1
                         END,
                         holding_as_of_date DESC NULLS LAST
            )
            SELECT deduplicated.*, count(*) OVER () AS _total_hits
            FROM deduplicated
            ORDER BY canonical_name, holding_name, holding_id
            LIMIT %s
        """
        rows = self._query(
            sql,
            (
                target_patterns,
                target_patterns,
                candidate_ids,
                candidate_ids,
                allowed_types or None,
                allowed_types or None,
                top_k,
            ),
        )
        total_hits = int(rows[0].get("_total_hits") or 0) if rows else 0
        return RetrievalBatch(
            evidence=[_holding_evidence(row, rank) for rank, row in enumerate(rows, 1)],
            coverage_complete=True,
            coverage_note=(
                "ACTIVE 데이터 버전에 연결된 KRX·SEC 편입종목과 "
                "OpenDART 공시 매핑을 조회했습니다."
            ),
            total_hits=total_hits,
            truncated=total_hits > top_k,
        )

    def _relation_search(self, query: dict, top_k: int) -> RetrievalBatch:
        constraints = _relation_query_constraints(query)
        if not constraints:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=True,
                coverage_note="관계 검색 기준 기업 또는 관계 조건이 없습니다.",
                total_hits=0,
            )

        sql = """
            WITH query_constraints AS (
                SELECT constraint_order,
                       predicate,
                       anchor,
                       anchor_role,
                       result_role,
                       result_listed
                FROM jsonb_to_recordset(%s::jsonb) AS input(
                    constraint_order integer,
                    predicate text,
                    anchor text,
                    anchor_role text,
                    result_role text,
                    result_listed boolean
                )
            ),
            source_status AS (
                SELECT EXISTS (
                    SELECT 1
                    FROM meta.dataset_snapshot snapshot
                    JOIN meta.data_version_snapshot mapping
                      ON mapping.snapshot_id = snapshot.snapshot_id
                    JOIN meta.data_version version
                      ON version.data_version_id = mapping.data_version_id
                    WHERE snapshot.dataset_code = 'ORGANIZATION_RELATION'
                      AND snapshot.load_status = 'LOADED'
                      AND version.status = 'ACTIVE'
                ) AS source_available
            ),
            matches AS MATERIALIZED (
                SELECT query_constraints.constraint_order,
                       result.*,
                       result.total_hits AS _constraint_total_hits
                FROM query_constraints
                CROSS JOIN LATERAL search.find_organization_relations(
                    query_constraints.anchor,
                    ARRAY[query_constraints.predicate],
                    query_constraints.anchor_role,
                    query_constraints.result_role,
                    query_constraints.result_listed,
                    100
                ) AS result
            ),
            deduplicated AS MATERIALIZED (
                SELECT DISTINCT ON (relation_id) *
                FROM matches
                ORDER BY relation_id, constraint_order
            ),
            result_rows AS (
                SELECT deduplicated.*,
                       count(*) OVER () AS _total_hits
                FROM deduplicated
                ORDER BY relation_as_of_date DESC NULLS LAST,
                         relation_id
                LIMIT %s
            )
            SELECT result_rows.*,
                   source_status.source_available AS _source_available
            FROM source_status
            LEFT JOIN result_rows ON true
            ORDER BY result_rows.relation_as_of_date DESC NULLS LAST,
                     result_rows.relation_id
        """
        rows = self._query(
            sql,
            (json.dumps(constraints, ensure_ascii=False), top_k),
        )
        source_available = bool(rows and rows[0].get("_source_available"))
        relation_rows = [row for row in rows if row.get("relation_id") is not None]
        if not source_available:
            return RetrievalBatch(
                evidence=[],
                coverage_complete=False,
                coverage_note=(
                    "ACTIVE 데이터 버전에 연결된 ORGANIZATION_RELATION "
                    "스냅샷이 없습니다."
                ),
                total_hits=None,
            )

        total_hits = (
            int(relation_rows[0].get("_total_hits") or 0)
            if relation_rows
            else 0
        )
        source_truncated = any(
            int(row.get("_constraint_total_hits") or 0) > 100
            for row in relation_rows
        )
        truncated = source_truncated or total_hits > top_k
        return RetrievalBatch(
            evidence=[_relation_evidence(row) for row in relation_rows],
            coverage_complete=not source_truncated,
            coverage_note=(
                "ACTIVE 기업 관계 스냅샷을 조회했습니다."
                if not source_truncated
                else "관계 조건별 100건 상한으로 결과가 절단되었습니다."
            ),
            total_hits=None if source_truncated else total_hits,
            truncated=truncated,
        )

    def _query(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        with self._connection_provider() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                raw_rows = cursor.fetchall()
                return [_as_mapping(cursor, row) for row in raw_rows]


def _filter_function_params(
    query: dict, product_type: str | None
) -> tuple[Any, ...]:
    values: dict[str, Any] = {
        "currency": None,
        "asset_type": None,
        "investment_region": None,
        "net_assets": None,
        "fee_rate": None,
        "one_year_return": None,
        "risk_grade": None,
    }
    for item in query.get("filters") or []:
        field = str(item.get("field") or "")
        operator = str(item.get("operator") or "")
        value = item.get("value")
        if field not in _FILTER_COLUMNS:
            raise UnsupportedGatewayQuery(
                f"search.filter_products does not support filter '{field}'"
            )
        if field in {"currency", "asset_type"} and operator == "eq":
            values[field] = value
        elif field == "investment_region" and operator == "eq":
            values[field] = value
        elif field in {"net_assets", "one_year_return"} and operator == "gte":
            values[field] = value
        elif field == "fee_rate" and operator == "lte":
            values[field] = value
        elif field == "risk_grade" and operator == "contains":
            values[field] = value
    return (
        product_type,
        values["currency"],
        values["asset_type"],
        values["investment_region"],
        values["net_assets"],
        values["fee_rate"],
        values["one_year_return"],
        values["risk_grade"],
    )


def _value_provenance_select(raw_row_reference: str) -> str:
    """Return the fixed SQL projection for one trusted table alias.

    Callers pass only aliases declared in this module; user input is never
    interpolated into this SQL fragment.
    """

    return _VALUE_PROVENANCE_SQL.format(raw_row_reference=raw_row_reference).strip()


def _structured_where(
    query: dict, allowed_types: list[str], candidate_ids: list[str] | None
) -> tuple[str, list[Any]]:
    clauses = [
        "(%s::text[] IS NULL OR metrics.product_type = ANY(%s::text[]))"
    ]
    params: list[Any] = [allowed_types or None, allowed_types or None]
    if candidate_ids is not None:
        clauses.append("metrics.product_id::text = ANY(%s::text[])")
        params.append(candidate_ids)

    operators = {
        "eq": "=",
        "ne": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }
    for item in query.get("filters") or []:
        field = str(item.get("field") or "")
        operator = str(item.get("operator") or "")
        column = _FILTER_COLUMNS.get(field)
        if column is None:
            raise UnsupportedGatewayQuery(
                f"search.filter_products does not support filter '{field}'"
            )
        value = item.get("value")
        if field == "sale_available":
            _validate_sale_filter(item)
            expected = value if operator == "eq" else not value
            clauses.append(
                "EXISTS (SELECT 1 FROM core.v_product_sale_availability availability "
                "WHERE availability.product_id = metrics.product_id "
                "AND availability.raw_row_id = metrics.raw_row_id "
                "AND availability.snapshot_id = metrics.snapshot_id "
                "AND availability.sale_available IS " + ("TRUE" if expected else "FALSE") + ")"
            )
            continue
        if field == "investment_region" and operator in {
            "eq",
            "ne",
            "contains",
            "in",
        }:
            requested = (
                list(value)
                if operator == "in" and isinstance(value, (list, tuple, set))
                else [value]
            )
            recognized = any(
                canonical_region(str(item_value or "")) is not None
                for item_value in requested
            )
            if operator == "contains" and not recognized:
                clauses.append(f'metrics."{column}" ILIKE %s')
                params.append(f"%{value}%")
                continue
            region_values = _unique_strings(
                [
                    db_value.casefold()
                    for item_value in requested
                    for db_value in database_region_values(str(item_value or ""))
                ]
            )
            if operator == "ne":
                clauses.append(
                    f'NOT (lower(btrim(metrics."{column}")) = ANY(%s::text[]))'
                )
            else:
                clauses.append(
                    f'lower(btrim(metrics."{column}")) = ANY(%s::text[])'
                )
            params.append(region_values)
            continue
        if operator in operators:
            if operator == "eq" and field in {
                "currency",
                "asset_type",
                "investment_region",
            }:
                clauses.append(
                    f'lower(metrics."{column}") = lower(btrim(%s::text))'
                )
            else:
                clauses.append(f'metrics."{column}" {operators[operator]} %s')
            params.append(value)
        elif operator == "contains":
            clauses.append(f'metrics."{column}" ILIKE %s')
            params.append(f"%{value}%")
        elif operator == "in":
            clauses.append(f'metrics."{column}" = ANY(%s)')
            params.append(list(value) if isinstance(value, (list, tuple, set)) else [value])
        else:
            raise UnsupportedGatewayQuery(
                f"search.filter_products does not support operator '{operator}' for '{field}'"
            )
    return " AND ".join(clauses), params


def _validate_sale_filter(spec: dict) -> None:
    if spec.get("operator") not in {"eq", "ne"} or not isinstance(spec.get("value"), bool):
        raise UnsupportedGatewayQuery("sale_available requires eq/ne and a boolean value")


def _bond_where(
    query: dict, candidate_ids: list[str] | None
) -> tuple[str, list[Any]]:
    clauses = ["TRUE"]
    params: list[Any] = []
    if candidate_ids is not None:
        clauses.append("bond_metrics.product_id = ANY(%s::text[])")
        params.append(candidate_ids)

    numeric_operators = {
        "eq": "=",
        "ne": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }
    for item in query.get("filters") or []:
        field = str(item.get("field") or "")
        operator = str(item.get("operator") or "")
        value = item.get("value")

        if field == "currency" and operator in {"eq", "ne"}:
            comparison = "=" if operator == "eq" else "<>"
            clauses.append(
                f"lower(btrim(bond_metrics.currency_code)) {comparison} "
                "lower(btrim(%s::text))"
            )
            params.append(value)
            continue
        if field == "currency" and operator == "in":
            values = list(value) if isinstance(value, (list, tuple, set)) else [value]
            clauses.append(
                "lower(btrim(bond_metrics.currency_code)) = ANY(%s::text[])"
            )
            params.append([str(item_value).strip().casefold() for item_value in values])
            continue
        if field == "sale_available" and operator in {"eq", "ne"}:
            clauses.append(
                f"bond_metrics.sale_available {'=' if operator == 'eq' else '<>'} %s"
            )
            params.append(bool(value))
            continue
        if field == "yield" and operator in numeric_operators:
            clauses.append(
                f"bond_metrics.yield_pct {numeric_operators[operator]} %s"
            )
            params.append(value)
            continue
        if field == "credit_rating" and operator in {
            "credit_at_least",
            "credit_at_most",
        }:
            rating = str(value or "").upper().strip().replace("0", "")
            if rating not in _CREDIT_RANK:
                raise UnsupportedGatewayQuery(
                    f"unsupported credit rating '{value}'"
                )
            comparison = "<=" if operator == "credit_at_least" else ">="
            clauses.append(f"{_BOND_CREDIT_RANK_SQL} {comparison} %s")
            params.append(_CREDIT_RANK[rating])
            continue
        if field == "credit_rating" and operator in {"eq", "ne"}:
            comparison = "=" if operator == "eq" else "<>"
            clauses.append(
                "replace(upper(btrim(bond_metrics.credit_rating)), '0', '') "
                f"{comparison} replace(upper(btrim(%s::text)), '0', '')"
            )
            params.append(value)
            continue
        if field == "name" and operator in {"eq", "contains"}:
            if operator == "eq":
                clauses.append(
                    "lower(btrim(bond_metrics.canonical_name)) = "
                    "lower(btrim(%s::text))"
                )
                params.append(value)
            else:
                clauses.append("bond_metrics.canonical_name ILIKE %s")
                params.append(f"%{value}%")
            continue
        raise UnsupportedGatewayQuery(
            f"domestic bond search does not support {field}:{operator}"
        )
    return " AND ".join(clauses), params


def _bond_order_by(query: dict) -> str:
    sorts = query.get("sorts") or []
    if not sorts:
        return "canonical_name ASC, product_id ASC"
    first = sorts[0]
    field = str(first.get("field") or "")
    direction = str(first.get("direction") or "").lower()
    if direction not in {"asc", "desc"}:
        raise UnsupportedGatewayQuery(
            f"domestic bond search does not support sort direction '{direction}'"
        )
    columns = {
        "yield": "yield_pct",
        "name": "canonical_name",
        "credit_rating": _BOND_CREDIT_RANK_SQL,
        "maturity_date": "maturity_date",
    }
    column = columns.get(field)
    if column is None:
        raise UnsupportedGatewayQuery(
            f"domestic bond search does not support sort {field}:{direction}"
        )
    return f"{column} {direction.upper()} NULLS LAST, canonical_name ASC, product_id ASC"


def _bond_coverage_fields(query: dict) -> dict[str, str]:
    columns = {
        "currency": "currency_code",
        "credit_rating": "credit_rating",
        "sale_available": "sale_available",
        "yield": "yield_pct",
        "name": "canonical_name",
        "maturity_date": "maturity_date",
    }
    fields: dict[str, str] = {}
    for item in [*(query.get("filters") or []), *(query.get("sorts") or [])]:
        field = str(item.get("field") or "")
        column = columns.get(field)
        if column:
            fields[field] = column
    return fields


def _can_use_filter_function(
    query: dict,
    allowed_types: list[str],
    candidate_ids: list[str] | None,
) -> bool:
    # The database function supports only these four orderings. New directions
    # must order the full canonical view before LIMIT, not re-sort its top 100.
    if _sort_parameter(query) not in {
        "name_asc", "asset_amount_desc", "return_1y_desc", "expense_ratio_asc"
    }:
        return False
    if len(allowed_types) > 1 or candidate_ids is not None:
        return False
    filters = query.get("filters") or []
    filter_fields = [str(item.get("field") or "") for item in filters]
    if len(filter_fields) != len(set(filter_fields)):
        return False
    # The SQL function accepts only one exact region string. Region aliases
    # need the direct query path so all equivalent DB labels are considered
    # before ordering and limiting.
    if "investment_region" in filter_fields:
        return False
    native_operators = {
        "currency": "eq",
        "asset_type": "eq",
        "investment_region": "eq",
        "net_assets": "gte",
        "fee_rate": "lte",
        "one_year_return": "gte",
        "risk_grade": "contains",
    }
    return all(
        native_operators.get(str(item.get("field") or ""))
        == str(item.get("operator") or "")
        for item in filters
    )


def _active_version_predicate(table_alias: str) -> str:
    return f"""EXISTS (
        SELECT 1
        FROM meta.data_version_snapshot mapping
        JOIN meta.data_version version
          ON version.data_version_id = mapping.data_version_id
        WHERE mapping.snapshot_id = {table_alias}.snapshot_id
          AND version.status = 'ACTIVE'
    )"""


def _sort_parameter(query: dict) -> str:
    sorts = query.get("sorts") or []
    if not sorts:
        return "name_asc"
    first = sorts[0]
    key = (str(first.get("field") or ""), str(first.get("direction") or ""))
    try:
        return _SORTS[key]
    except KeyError as exc:
        raise UnsupportedGatewayQuery(
            f"search.filter_products does not support sort {key[0]}:{key[1]}"
        ) from exc


def _coverage_fields(query: dict) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in [*(query.get("filters") or []), *(query.get("sorts") or [])]:
        field = str(item.get("field") or "")
        column = _FILTER_COLUMNS.get(field)
        if field == "name":
            column = "canonical_name"
        if column:
            fields[field] = column
    return fields


def _candidate_product_ids(
    upstream_evidence: dict[str, list[Evidence]],
) -> list[str] | None:
    if not upstream_evidence:
        return None
    candidate_sets = [
        {str(item.product_id) for item in evidence if item.product_id is not None}
        for evidence in upstream_evidence.values()
    ]
    return sorted(set.intersection(*candidate_sets))


def _combined_candidate_product_ids(
    query: dict,
    upstream_evidence: dict[str, list[Evidence]],
) -> list[str] | None:
    upstream_ids = _candidate_product_ids(upstream_evidence)
    explicit_ids = _unique_strings(query.get("candidate_product_ids") or [])
    if upstream_ids is None:
        return explicit_ids or None
    if not explicit_ids:
        return upstream_ids
    return sorted(set(upstream_ids).intersection(explicit_ids))


def _holding_candidate_product_ids(
    query: dict,
    upstream_evidence: dict[str, list[Evidence]],
) -> list[str] | None:
    explicit_ids = set(_unique_strings(query.get("candidate_product_ids") or []))
    identity_ids = {
        str(item.product_id)
        for item in upstream_evidence.get("identity_search", [])
        if item.product_id is not None
    }
    if explicit_ids and identity_ids:
        return sorted(explicit_ids.intersection(identity_ids))
    return sorted(explicit_ids or identity_ids) or None


def _holding_targets(
    query: dict,
    upstream_evidence: dict[str, list[Evidence]],
) -> list[str]:
    targets: list[Any] = list(query.get("holding_targets") or [])
    relations = upstream_evidence.get("relation_search", [])
    constraints = query.get("relation_constraints") or []
    if relations and constraints:
        for relation in relations:
            for constraint in constraints:
                targets.extend(
                    relation_result_values(relation.structured, constraint)
                )
    elif relations:
        for relation in relations:
            targets.extend(
                relation.structured.get(key)
                for key in (
                    "source_entity_id",
                    "source_entity_name",
                    "target_entity_id",
                    "target_entity_name",
                )
            )
    return _unique_strings(targets)


def _relation_query_constraints(query: dict) -> list[dict[str, Any]]:
    raw_constraints = [
        item
        for item in (query.get("relation_constraints") or [])
        if isinstance(item, Mapping)
    ]
    if not raw_constraints:
        raw_constraints = [{}]

    default_anchors = _unique_strings(query.get("entities") or [])
    default_predicates = _unique_strings(query.get("relation_predicates") or [])
    constraints: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in raw_constraints:
        anchors = _unique_strings([raw.get("anchor")]) or default_anchors
        predicates = _unique_strings([raw.get("predicate")]) or default_predicates
        if not anchors or not predicates:
            continue

        anchor_role = str(raw.get("anchor_role") or "either")
        result_role = str(raw.get("result_role") or "opposite")
        traversal_hops = int(raw.get("traversal_hops") or 1)
        if anchor_role not in {"source", "target", "either"}:
            raise UnsupportedGatewayQuery(
                f"unsupported relation anchor_role: {anchor_role}"
            )
        if result_role not in {"source", "target", "opposite"}:
            raise UnsupportedGatewayQuery(
                f"unsupported relation result_role: {result_role}"
            )
        if traversal_hops != 1:
            raise UnsupportedGatewayQuery(
                "PostgreSQL relation search currently supports one hop only"
            )

        listed = raw.get("result_listed")
        if listed is None and "listed_only" in query:
            listed = query.get("listed_only") or None
        if listed is not None and not isinstance(listed, bool):
            raise UnsupportedGatewayQuery("result_listed must be boolean or null")

        for predicate in predicates:
            if not DEFAULT_ONTOLOGY.has_relation(predicate) or predicate == "holds":
                raise UnsupportedGatewayQuery(
                    f"unsupported organization relation predicate: {predicate}"
                )
            for anchor in anchors:
                key = (
                    predicate,
                    anchor,
                    anchor_role,
                    result_role,
                    listed,
                )
                if key in seen:
                    continue
                seen.add(key)
                constraints.append(
                    {
                        "constraint_order": len(constraints) + 1,
                        "predicate": predicate,
                        "anchor": anchor,
                        "anchor_role": anchor_role,
                        "result_role": result_role,
                        "result_listed": listed,
                    }
                )
    return constraints


def _escaped_like_pattern(value: Any) -> str:
    escaped = (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _semantic_search_text(query: dict) -> str:
    semantic_query = str(query.get("semantic_query") or "").strip()
    if semantic_query:
        return semantic_query
    question = str(query.get("question") or "").strip()
    if question:
        return question
    return " ".join(
        _unique_strings(
            [
                *(query.get("themes") or []),
                *(query.get("text_entities") or query.get("entities") or []),
                *(query.get("product_mentions") or []),
            ]
        )
    )


def _pgvector_literal(vector: Sequence[float]) -> str:
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingGenerationError("query embedding contains a non-finite value")
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _database_product_types(query: dict) -> list[str]:
    return [
        _PRODUCT_TYPE_TO_DB[item]
        for item in _unique_strings(query.get("product_types") or [])
        if item in _PRODUCT_TYPE_TO_DB
    ]


def _unsupported_product_type_batch() -> RetrievalBatch:
    return RetrievalBatch(
        evidence=[],
        coverage_complete=False,
        coverage_note=(
            "PostgreSQL 상품 스키마가 지원하는 상품군과 일치하지 않습니다."
        ),
        total_hits=None,
    )


def _identity_evidence(row: Mapping[str, Any], matched_terms: list[str]) -> Evidence:
    structured = _with_logical_fields(_public_row(row), row)
    structured["matched_terms"] = list(matched_terms)
    product_id = str(row["product_id"])
    product_type = _product_type(row.get("product_type"))
    return Evidence(
        evidence_id=f"identity:{product_type.value if product_type else 'unknown'}:{product_id}",
        capability=Capability.IDENTITY_SEARCH,
        source_id=str(row.get("source_file_name") or "fund_ontology"),
        content=str(
            row.get("canonical_name")
            or row.get("matched_alias_value")
            or product_id
        ),
        score=_number(row.get("match_score")),
        record_id=product_id,
        product_id=product_id,
        product_type=product_type,
        as_of_date=_optional_iso(row.get("data_as_of_date")),
        source_ref=_source_ref(row),
        structured=structured,
        provenance=_provenance(row),
        value_provenance=_value_provenance(row),
    )


def _structured_evidence(row: Mapping[str, Any], rank: int) -> Evidence:
    structured = _with_logical_fields(_public_row(row), row)
    product_id = str(row["product_id"])
    product_type = _product_type(row.get("product_type"))
    return Evidence(
        evidence_id=f"structured:{product_type.value if product_type else 'unknown'}:{product_id}",
        capability=Capability.STRUCTURED_SEARCH,
        source_id=str(row.get("source_file_name") or "fund_ontology"),
        content=json.dumps(structured, ensure_ascii=False, default=str, sort_keys=True),
        score=max(0.0, 1.0 - (rank - 1) * 0.001),
        record_id=product_id,
        product_id=product_id,
        product_type=product_type,
        as_of_date=_optional_iso(row.get("data_as_of_date")),
        source_ref=_source_ref(row),
        structured=structured,
        provenance=_provenance(row),
        value_provenance=_value_provenance(row),
    )


def _bond_structured_evidence(row: Mapping[str, Any], rank: int) -> Evidence:
    structured = _public_row(row)
    structured.update(
        {
            "name": _json_value(row.get("canonical_name")),
            "currency": _json_value(row.get("currency_code")),
            "credit_rating": _json_value(row.get("credit_rating")),
            "sale_available": _json_value(row.get("sale_available")),
            "yield": _json_value(row.get("yield_pct")),
        }
    )
    product_id = str(row["product_id"])
    return Evidence(
        evidence_id=f"structured:bond:{product_id}",
        capability=Capability.STRUCTURED_SEARCH,
        source_id=str(row.get("source_file_name") or "domestic_bond_quote"),
        content=json.dumps(structured, ensure_ascii=False, default=str, sort_keys=True),
        score=max(0.0, 1.0 - (rank - 1) * 0.001),
        record_id=product_id,
        product_id=product_id,
        product_type=ProductType.BOND,
        as_of_date=_optional_iso(row.get("data_as_of_date")),
        source_ref=_source_ref(row),
        structured=structured,
        provenance=_provenance(row),
        value_provenance=_value_provenance(row),
    )


def _keyword_evidence(row: Mapping[str, Any]) -> Evidence:
    structured = _with_logical_fields(_public_row(row), row)
    product_id = str(row["product_id"])
    return Evidence(
        evidence_id=f"keyword:foreign_etf:{row.get('document_id')}",
        capability=Capability.KEYWORD_SEARCH,
        source_id=str(row.get("source_file_name") or "overseas_etf_strategy"),
        content=str(row.get("evidence_excerpt") or row.get("strategy_text") or ""),
        score=_number(row.get("combined_score")),
        record_id=str(row.get("document_id")),
        product_id=product_id,
        product_type=ProductType.FOREIGN_ETF,
        as_of_date=_optional_iso(row.get("data_as_of_date")),
        source_ref=_source_ref(row),
        structured=structured,
        provenance=_provenance(row),
        value_provenance=_value_provenance(row),
    )


def _vector_evidence(row: Mapping[str, Any]) -> Evidence:
    structured = _with_logical_fields(_public_row(row), row)
    structured["strategy"] = _json_value(row.get("raw_text"))
    product_id = str(row["product_id"])
    return Evidence(
        evidence_id=f"vector:foreign_etf:{row.get('document_id')}",
        capability=Capability.VECTOR_SEARCH,
        source_id=str(row.get("source_file_name") or "overseas_etf_strategy"),
        content=str(row.get("raw_text") or row.get("strategy_text") or ""),
        score=_number(row.get("cosine_similarity")),
        record_id=str(row.get("document_id")),
        product_id=product_id,
        product_type=ProductType.FOREIGN_ETF,
        as_of_date=_optional_iso(row.get("data_as_of_date")),
        source_ref=_source_ref(row),
        structured=structured,
        provenance=_provenance(row),
        value_provenance=_value_provenance(row),
    )


def _holding_evidence(row: Mapping[str, Any], rank: int) -> Evidence:
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    structured = (
        {key: _json_value(value) for key, value in payload.items()}
        if isinstance(payload, Mapping)
        else {}
    )
    structured.update(
        {
            "source_kind": row.get("source_kind"),
            "product_name": row.get("canonical_name"),
            "holding_id": row.get("holding_id"),
            "holding_name": row.get("holding_name"),
            "holding_weight": _json_value(row.get("holding_weight")),
        }
    )
    structured = {key: value for key, value in structured.items() if value is not None}

    product_id = str(row["product_id"])
    holding_id = str(
        row.get("holding_id")
        or row.get("holding_name")
        or row.get("holding_source_ref")
        or rank
    )
    source_kind = str(row.get("source_kind") or "v_etf_holding")
    product_name = str(row.get("canonical_name") or product_id)
    holding_name = str(
        row.get("holding_name") or row.get("holding_id") or "편입종목"
    )
    source_ref = str(
        row.get("holding_source_ref")
        or f"ext.{source_kind}#{product_id}:{holding_id}"
    )
    return Evidence(
        evidence_id=f"holding:{source_kind}:{product_id}:{holding_id}",
        capability=Capability.HOLDING_SEARCH,
        source_id=f"ext.{source_kind}",
        content=f"{product_name}이(가) {holding_name}을(를) 편입 또는 보유합니다.",
        score=max(0.0, _number(row.get("holding_weight"))),
        record_id=holding_id,
        product_id=product_id,
        product_type=_product_type(row.get("product_type")),
        as_of_date=_optional_iso(
            row.get("holding_as_of_date") or row.get("data_as_of_date")
        ),
        source_ref=source_ref,
        structured=structured,
        provenance={**_provenance(row), "source_kind": source_kind},
    )


def _relation_evidence(row: Mapping[str, Any]) -> Evidence:
    predicate = str(row.get("predicate") or "relation")
    relation_id = str(row["relation_id"])
    source_name = str(
        row.get("source_entity_name") or row.get("source_entity_id") or "source"
    )
    target_name = str(
        row.get("target_entity_name") or row.get("target_entity_id") or "target"
    )
    structured = _public_row(row)
    source_ref = str(row.get("source_ref") or _source_ref(row))
    return Evidence(
        evidence_id=f"relation:{predicate}:{relation_id}",
        capability=Capability.RELATION_SEARCH,
        source_id="core.organization_relation",
        content=f"{source_name} --{predicate}--> {target_name}",
        score=max(0.0, min(1.0, _number(row.get("confidence")))),
        record_id=relation_id,
        as_of_date=_optional_iso(
            row.get("relation_as_of_date") or row.get("data_as_of_date")
        ),
        source_ref=source_ref,
        structured=structured,
        provenance=_provenance(row),
    )


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _json_value(value)
        for key, value in row.items()
        if not key.startswith("_") and key not in {"term_order"}
    }


def _with_logical_fields(
    structured: dict[str, Any], row: Mapping[str, Any]
) -> dict[str, Any]:
    aliases = {
        "name": "canonical_name",
        "currency": "currency_code",
        "net_assets": "asset_amount",
        "fee_rate": "expense_ratio_pct",
        "one_year_return": "return_1y_pct",
        "return_1y": "return_1y_pct",
        "risk_grade": "risk_label",
    }
    for logical, database_column in aliases.items():
        structured[logical] = _json_value(row.get(database_column))
    return structured


def _provenance(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": _json_value(row.get("snapshot_id")),
        "source_file_name": row.get("source_file_name"),
        "source_sheet": row.get("source_sheet"),
        "source_row_number": row.get("source_row_number"),
        "data_as_of_date": _optional_iso(row.get("data_as_of_date")),
        "provenance_status": row.get("provenance_status"),
        "has_warning": row.get("has_warning"),
    }


def _value_provenance(row: Mapping[str, Any]) -> list[ValueProvenance]:
    raw_items = row.get("_value_provenance") or []
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw_items, list):
        return []

    items: list[ValueProvenance] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        field_name = str(raw_item.get("field_name") or "").strip()
        fill_type = str(raw_item.get("fill_type") or "").strip().lower()
        if not field_name or fill_type not in {
            "original",
            "official_fill",
            "manual_verified",
            "estimated",
        }:
            continue
        confidence = raw_item.get("fill_confidence")
        items.append(
            ValueProvenance(
                field_name=field_name,
                fill_type=fill_type,
                evidence_eligible=bool(raw_item.get("evidence_eligible")),
                was_missing=bool(raw_item.get("was_missing")),
                source_reference=_optional_str(raw_item.get("source_reference")),
                source_as_of_date=_optional_iso(raw_item.get("source_as_of_date")),
                fill_confidence=(
                    _number(confidence) if confidence is not None else None
                ),
            )
        )
    return items


def _source_ref(row: Mapping[str, Any]) -> str:
    file_name = str(row.get("source_file_name") or "fund_ontology")
    sheet = row.get("source_sheet")
    row_number = row.get("source_row_number")
    parts = [file_name]
    if sheet is not None:
        parts.append(f"sheet={sheet}")
    if row_number is not None:
        parts.append(f"row={row_number}")
    if len(parts) == 1 and row.get("snapshot_id") is not None:
        parts.append(f"snapshot={row['snapshot_id']}")
    return "#".join([parts[0], "&".join(parts[1:])]) if len(parts) > 1 else parts[0]


def _product_type(value: Any) -> ProductType | None:
    return _DB_TO_PRODUCT_TYPE.get(str(value))


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _optional_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _unique_strings(values: Sequence[Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if value is not None and str(value).strip()
        )
    )


def _as_mapping(cursor: CursorLike, row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    description = cursor.description or []
    columns = [
        str(getattr(item, "name", None) or item[0])
        for item in description
    ]
    return dict(zip(columns, row, strict=True))
