--
-- PostgreSQL database dump
--

\restrict iFuAHVBScgZnn8b7e03dpUmbIlxovdPd7xQAxb110ava0LcS5ZBfmy9XadvL6qi

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: audit; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA audit;


--
-- Name: core; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA core;


--
-- Name: meta; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA meta;


--
-- Name: raw; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA raw;


--
-- Name: search; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA search;


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: domestic_etp; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.domestic_etp (
    product_id bigint NOT NULL,
    listing_date date,
    delisting_date date,
    strategy_type text,
    base_index text,
    leverage_factor numeric,
    expense_ratio_pct numeric,
    close_price numeric,
    nav numeric,
    aum numeric,
    trading_volume numeric,
    return_1d_pct numeric,
    return_1m_pct numeric,
    return_1y_pct numeric,
    dividend_yield_pct numeric,
    nav_base_date date,
    risk_name text,
    pension_eligible boolean,
    core_product boolean,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fund_class; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.fund_class (
    product_id bigint NOT NULL,
    benchmark_name text,
    benchmark_name_en text,
    fund_type text,
    business_type text,
    investment_attributes text,
    class_name text,
    class_fee_type text,
    class_sales_channel text,
    risk_grade text,
    sale_status text,
    person_corporate_type text,
    hedged boolean,
    overseas_fund boolean,
    net_asset_amount numeric,
    sales_company_fee_pct numeric,
    trustee_fee_pct numeric,
    return_1m_pct numeric,
    return_3m_pct numeric,
    return_6m_pct numeric,
    return_1y_pct numeric,
    return_3y_pct numeric,
    daily_base_date date,
    price_base_date date,
    attribute_search_text text,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: overseas_etp; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.overseas_etp (
    product_id bigint NOT NULL,
    listing_date date,
    strategy_text text,
    base_index text,
    replication_method text,
    expense_ratio_pct numeric,
    leverage_factor numeric,
    index_tracking boolean,
    inverse_or_short boolean,
    is_etn boolean,
    core_product boolean,
    close_price numeric,
    nav numeric,
    aum numeric,
    listing_price numeric,
    trading_volume numeric,
    return_1d_pct numeric,
    close_price_base_date date,
    nav_base_date date,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: product; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.product (
    product_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    raw_row_id bigint NOT NULL,
    product_type text NOT NULL,
    source_product_key text NOT NULL,
    canonical_name text NOT NULL,
    short_name text,
    currency_code text,
    isin text,
    ticker text,
    market_code text,
    product_group text,
    offer_type text,
    manager_name text,
    asset_type text,
    investment_region text,
    identifiers jsonb DEFAULT '{}'::jsonb NOT NULL,
    has_warning boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT product_product_type_check CHECK ((product_type = ANY (ARRAY['PUBLIC_FUND_CLASS'::text, 'DOMESTIC_ETP'::text, 'OVERSEAS_ETP'::text])))
);


--
-- Name: dataset_snapshot; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.dataset_snapshot (
    snapshot_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_file_name text NOT NULL,
    file_sha256 character(64) NOT NULL,
    row_count integer,
    load_status text DEFAULT 'PENDING'::text NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL,
    data_as_of_date date,
    schema_version text DEFAULT 'v1'::text NOT NULL,
    provenance_status text DEFAULT 'PENDING'::text NOT NULL
);


--
-- Name: source_row; Type: TABLE; Schema: raw; Owner: -
--

CREATE TABLE raw.source_row (
    raw_row_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_sheet text NOT NULL,
    source_row_number integer NOT NULL,
    source_key text,
    payload jsonb NOT NULL,
    row_hash character(64) NOT NULL,
    validation_errors jsonb DEFAULT '[]'::jsonb NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: product_metrics; Type: VIEW; Schema: search; Owner: -
--

CREATE VIEW search.product_metrics AS
 SELECT product.product_id,
    product.product_type,
    product.source_product_key,
    product.canonical_name,
    product.short_name,
    product.currency_code,
    product.isin,
    product.ticker,
    product.manager_name,
    product.asset_type,
    product.investment_region,
    product.offer_type,
    COALESCE(domestic.expense_ratio_pct, overseas.expense_ratio_pct) AS expense_ratio_pct,
    COALESCE(domestic.aum, overseas.aum, fund.net_asset_amount) AS asset_amount,
    COALESCE(domestic.return_1d_pct, overseas.return_1d_pct) AS return_1d_pct,
    COALESCE(domestic.return_1y_pct, fund.return_1y_pct) AS return_1y_pct,
    COALESCE(domestic.risk_name, fund.risk_grade) AS risk_label,
    overseas.strategy_text,
    product.has_warning,
    product.snapshot_id,
    product.raw_row_id,
    snapshot.source_file_name,
    source.source_sheet,
    source.source_row_number,
    snapshot.data_as_of_date,
    snapshot.provenance_status
   FROM (((((core.product product
     LEFT JOIN core.domestic_etp domestic ON ((domestic.product_id = product.product_id)))
     LEFT JOIN core.overseas_etp overseas ON ((overseas.product_id = product.product_id)))
     LEFT JOIN core.fund_class fund ON ((fund.product_id = product.product_id)))
     JOIN raw.source_row source ON ((source.raw_row_id = product.raw_row_id)))
     JOIN meta.dataset_snapshot snapshot ON ((snapshot.snapshot_id = product.snapshot_id)));


--
-- Name: filter_products(text, text, text, text, numeric, numeric, numeric, text, text, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.filter_products(p_product_type text DEFAULT NULL::text, p_currency_code text DEFAULT NULL::text, p_asset_type text DEFAULT NULL::text, p_investment_region text DEFAULT NULL::text, p_min_asset_amount numeric DEFAULT NULL::numeric, p_max_expense_ratio_pct numeric DEFAULT NULL::numeric, p_min_return_1y_pct numeric DEFAULT NULL::numeric, p_risk_keyword text DEFAULT NULL::text, p_sort_by text DEFAULT 'name_asc'::text, p_limit integer DEFAULT 20) RETURNS SETOF search.product_metrics
    LANGUAGE sql STABLE
    AS $$

SELECT metrics.*
FROM search.product_metrics metrics

WHERE
    EXISTS (
        SELECT 1
        FROM meta.data_version_snapshot mapping
        JOIN meta.data_version version
            ON version.data_version_id =
               mapping.data_version_id
        WHERE mapping.snapshot_id = metrics.snapshot_id
          AND version.status = 'ACTIVE'
    )

    AND
    (
        p_product_type IS NULL
        OR upper(metrics.product_type) =
           upper(btrim(p_product_type))
    )

    AND (
        p_currency_code IS NULL
        OR upper(metrics.currency_code) =
           upper(btrim(p_currency_code))
    )

    AND (
        p_asset_type IS NULL
        OR lower(metrics.asset_type) =
           lower(btrim(p_asset_type))
    )

    AND (
        p_investment_region IS NULL
        OR lower(metrics.investment_region) =
           lower(btrim(p_investment_region))
    )

    AND (
        p_min_asset_amount IS NULL
        OR metrics.asset_amount >= p_min_asset_amount
    )

    AND (
        p_max_expense_ratio_pct IS NULL
        OR metrics.expense_ratio_pct <=
           p_max_expense_ratio_pct
    )

    AND (
        p_min_return_1y_pct IS NULL
        OR metrics.return_1y_pct >=
           p_min_return_1y_pct
    )

    AND (
        p_risk_keyword IS NULL
        OR metrics.risk_label ILIKE
           '%' || btrim(p_risk_keyword) || '%'
    )

ORDER BY
    CASE
        WHEN lower(p_sort_by) = 'asset_amount_desc'
        THEN metrics.asset_amount
    END DESC NULLS LAST,

    CASE
        WHEN lower(p_sort_by) = 'return_1y_desc'
        THEN metrics.return_1y_pct
    END DESC NULLS LAST,

    CASE
        WHEN lower(p_sort_by) = 'expense_ratio_asc'
        THEN metrics.expense_ratio_pct
    END ASC NULLS LAST,

    CASE
        WHEN lower(p_sort_by) = 'name_asc'
        THEN metrics.canonical_name
    END ASC NULLS LAST,

    metrics.canonical_name

LIMIT least(
    greatest(coalesce(p_limit, 20), 1),
    100
);

$$;


--
-- Name: filter_products_response(text, text, text, text, numeric, numeric, numeric, text, text, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.filter_products_response(p_product_type text DEFAULT NULL::text, p_currency_code text DEFAULT NULL::text, p_asset_type text DEFAULT NULL::text, p_investment_region text DEFAULT NULL::text, p_min_asset_amount numeric DEFAULT NULL::numeric, p_max_expense_ratio_pct numeric DEFAULT NULL::numeric, p_min_return_1y_pct numeric DEFAULT NULL::numeric, p_risk_keyword text DEFAULT NULL::text, p_sort_by text DEFAULT 'name_asc'::text, p_limit integer DEFAULT 20) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$

WITH current_version AS (
    SELECT
        data_version_id,
        version_name,
        status
    FROM meta.data_version
    WHERE status = 'ACTIVE'
    LIMIT 1
),

result_rows AS MATERIALIZED (
    SELECT
        row_number() OVER () AS result_rank,
        result.*
    FROM search.filter_products(
        p_product_type,
        p_currency_code,
        p_asset_type,
        p_investment_region,
        p_min_asset_amount,
        p_max_expense_ratio_pct,
        p_min_return_1y_pct,
        p_risk_keyword,
        p_sort_by,
        p_limit
    ) AS result
),

coverage_info AS (
    SELECT
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'dataset_code',
                        coverage.dataset_code,
                    'field',
                        coverage.normalized_field,
                    'total_rows',
                        coverage.total_rows,
                    'non_null_rows',
                        coverage.non_null_rows,
                    'missing_rows',
                        coverage.missing_rows,
                    'coverage_pct',
                        coverage.coverage_pct,
                    'distinct_count',
                        coverage.distinct_count
                )
                ORDER BY
                    coverage.dataset_code,
                    coverage.normalized_field
            ),
            '[]'::jsonb
        ) AS coverage

    FROM meta.coverage_profile coverage
    JOIN current_version version
        ON version.data_version_id =
           coverage.data_version_id

    WHERE
        p_product_type IS NULL
        OR coverage.dataset_code = 'ALL'
        OR coverage.dataset_code =
           upper(btrim(p_product_type))
),

source_info AS (
    SELECT
        coalesce(
            jsonb_agg(
                jsonb_build_object(
                    'dataset_code',
                        snapshot.dataset_code,
                    'snapshot_id',
                        snapshot.snapshot_id,
                    'source_file_name',
                        snapshot.source_file_name,
                    'row_count',
                        snapshot.row_count,
                    'load_status',
                        snapshot.load_status,
                    'data_as_of_date',
                        snapshot.data_as_of_date,
                    'provenance_status',
                        snapshot.provenance_status
                )
                ORDER BY snapshot.dataset_code
            ),
            '[]'::jsonb
        ) AS sources,

        coalesce(
            bool_and(
                snapshot.provenance_status = 'VERIFIED'
                AND snapshot.data_as_of_date IS NOT NULL
            ),
            false
        ) AS provenance_ready

    FROM current_version version

    JOIN meta.data_version_snapshot mapping
        ON mapping.data_version_id =
           version.data_version_id

    JOIN meta.dataset_snapshot snapshot
        ON snapshot.snapshot_id =
           mapping.snapshot_id
)

SELECT jsonb_build_object(
    'response_schema_version',
        '1.0',

    'data_version',
        (
            SELECT jsonb_build_object(
                'data_version_id',
                    version.data_version_id,
                'version_name',
                    version.version_name,
                'status',
                    version.status
            )
            FROM current_version version
        ),

    'query',
        jsonb_strip_nulls(
            jsonb_build_object(
                'product_type',
                    p_product_type,
                'currency_code',
                    p_currency_code,
                'asset_type',
                    p_asset_type,
                'investment_region',
                    p_investment_region,
                'min_asset_amount',
                    p_min_asset_amount,
                'max_expense_ratio_pct',
                    p_max_expense_ratio_pct,
                'min_return_1y_pct',
                    p_min_return_1y_pct,
                'risk_keyword',
                    p_risk_keyword,
                'sort_by',
                    p_sort_by,
                'limit',
                    p_limit
            )
        ),

    'returned_count',
        (
            SELECT count(*)
            FROM result_rows
        ),

    'warning_count',
        (
            SELECT count(*)
            FROM result_rows
            WHERE has_warning = true
        ),

    'provenance_ready',
        (
            SELECT provenance_ready
            FROM source_info
        ),

    'notice',
        CASE
            WHEN (
                SELECT provenance_ready
                FROM source_info
            )
            THEN '출처 검증이 완료된 데이터입니다.'
            ELSE
                '파일 기준일과 채움 이력이 아직 검증되지 않았습니다.'
        END,

    'sources',
        (
            SELECT sources
            FROM source_info
        ),

    'coverage',
        (
            SELECT coverage
            FROM coverage_info
        ),

    'results',
        (
            SELECT coalesce(
                jsonb_agg(
                    to_jsonb(result_rows)
                    - 'result_rank'
                    ORDER BY result_rank
                ),
                '[]'::jsonb
            )
            FROM result_rows
        )
);

$$;


--
-- Name: find_overseas_strategies(text, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.find_overseas_strategies(p_query text, p_limit integer DEFAULT 10) RETURNS TABLE(document_id bigint, product_id bigint, source_product_key text, canonical_name text, ticker text, base_index text, expense_ratio_pct numeric, aum numeric, strategy_text text, evidence_excerpt text, match_type text, keyword_score numeric, similarity_score numeric, combined_score numeric, has_warning boolean, snapshot_id bigint, source_file_name text, source_sheet text, source_row_number integer, data_as_of_date date, provenance_status text)
    LANGUAGE sql STABLE
    AS $$

WITH query_input AS (
    SELECT
        nullif(
            lower(
                regexp_replace(
                    btrim(p_query),
                    '\s+',
                    ' ',
                    'g'
                )
            ),
            ''
        ) AS normalized_query,

        websearch_to_tsquery(
            'simple',
            coalesce(p_query, '')
        ) AS keyword_query
),

scored AS (
    SELECT
        strategy.document_id,
        strategy.product_id,
        strategy.snapshot_id,
        strategy.raw_row_id,
        strategy.raw_text,
        strategy.normalized_text,
        strategy.has_warning,

        query_input.normalized_query,
        query_input.keyword_query,

        ts_rank_cd(
            strategy.fts,
            query_input.keyword_query
        )::numeric AS keyword_score,

        similarity(
            strategy.normalized_text,
            query_input.normalized_query
        )::numeric AS similarity_score,

        CASE
            WHEN strategy.fts @@ query_input.keyword_query
                THEN 'KEYWORD'

            WHEN strategy.normalized_text LIKE
                 '%' || query_input.normalized_query || '%'
                THEN 'PARTIAL'

            ELSE 'SIMILAR'
        END AS match_type

    FROM search.overseas_etf_strategy strategy
    CROSS JOIN query_input

    WHERE query_input.normalized_query IS NOT NULL

      AND EXISTS (
            SELECT 1
            FROM meta.data_version_snapshot mapping
            JOIN meta.data_version version
                ON version.data_version_id = mapping.data_version_id
            WHERE mapping.snapshot_id = strategy.snapshot_id
              AND version.status = 'ACTIVE'
      )

      AND (
            strategy.fts @@ query_input.keyword_query

         OR strategy.normalized_text LIKE
                '%' || query_input.normalized_query || '%'

         OR strategy.normalized_text %
                query_input.normalized_query
      )
),

ranked AS (
    SELECT
        scored.*,

        greatest(
            CASE
                WHEN scored.match_type = 'KEYWORD'
                    THEN least(
                        1.0,
                        0.70 + scored.keyword_score
                    )

                WHEN scored.match_type = 'PARTIAL'
                    THEN 0.65

                ELSE 0
            END,

            scored.similarity_score
        )::numeric AS combined_score

    FROM scored
)

SELECT
    ranked.document_id,
    product.product_id,
    product.source_product_key,
    product.canonical_name,
    product.ticker,

    etp.base_index,
    etp.expense_ratio_pct,
    etp.aum,

    ranked.raw_text AS strategy_text,

    CASE
        WHEN ranked.match_type = 'KEYWORD'
            THEN ts_headline(
                'simple',
                ranked.raw_text,
                ranked.keyword_query,
                'StartSel=<<, StopSel=>>, MaxWords=45, MinWords=15'
            )

        ELSE left(ranked.raw_text, 500)
    END AS evidence_excerpt,

    ranked.match_type,
    round(ranked.keyword_score, 4),
    round(ranked.similarity_score, 4),
    round(ranked.combined_score, 4),

    ranked.has_warning,

    snapshot.snapshot_id,
    snapshot.source_file_name,
    source.source_sheet,
    source.source_row_number,
    snapshot.data_as_of_date,
    snapshot.provenance_status

FROM ranked

JOIN core.product product
    ON product.product_id = ranked.product_id

JOIN core.overseas_etp etp
    ON etp.product_id = ranked.product_id

JOIN raw.source_row source
    ON source.raw_row_id = ranked.raw_row_id

JOIN meta.dataset_snapshot snapshot
    ON snapshot.snapshot_id = ranked.snapshot_id

ORDER BY
    ranked.combined_score DESC,
    ranked.keyword_score DESC,
    product.canonical_name

LIMIT least(
    greatest(coalesce(p_limit, 10), 1),
    100
);

$$;


--
-- Name: find_products(text, text, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.find_products(p_query text, p_product_type text DEFAULT NULL::text, p_limit integer DEFAULT 20) RETURNS TABLE(product_id bigint, product_type text, source_product_key text, canonical_name text, short_name text, ticker text, isin text, currency_code text, manager_name text, matched_alias_type text, matched_alias_value text, match_type text, match_score numeric, has_warning boolean, snapshot_id bigint, source_file_name text, source_sheet text, source_row_number integer, data_as_of_date date, provenance_status text)
    LANGUAGE sql STABLE
    AS $$

WITH query_input AS (
    SELECT nullif(
        lower(
            regexp_replace(
                btrim(p_query),
                '\s+',
                ' ',
                'g'
            )
        ),
        ''
    ) AS normalized_query
),

raw_matches AS (
    SELECT
        alias.product_id,
        alias.alias_type,
        alias.alias_value,
        alias.priority,

        CASE
            WHEN alias.normalized_alias =
                 query_input.normalized_query
                THEN 'EXACT'

            WHEN alias.normalized_alias LIKE
                 query_input.normalized_query || '%'
                THEN 'PREFIX'

            WHEN alias.normalized_alias LIKE
                 '%' || query_input.normalized_query || '%'
                THEN 'PARTIAL'

            ELSE 'SIMILAR'
        END AS match_type,

        CASE
            WHEN alias.normalized_alias =
                 query_input.normalized_query
                THEN 1.00

            WHEN alias.normalized_alias LIKE
                 query_input.normalized_query || '%'
                THEN greatest(
                    0.90,
                    similarity(
                        alias.normalized_alias,
                        query_input.normalized_query
                    )
                )

            WHEN alias.normalized_alias LIKE
                 '%' || query_input.normalized_query || '%'
                THEN greatest(
                    0.75,
                    similarity(
                        alias.normalized_alias,
                        query_input.normalized_query
                    )
                )

            ELSE similarity(
                alias.normalized_alias,
                query_input.normalized_query
            )
        END::numeric AS match_score

    FROM search.product_alias alias
    CROSS JOIN query_input

    JOIN core.product product
        ON product.product_id = alias.product_id

    WHERE alias.is_active = true

      AND EXISTS (
            SELECT 1
            FROM meta.data_version_snapshot mapping
            JOIN meta.data_version version
                ON version.data_version_id = mapping.data_version_id
            WHERE mapping.snapshot_id = product.snapshot_id
              AND version.status = 'ACTIVE'
      )

      AND query_input.normalized_query IS NOT NULL

      AND (
            alias.normalized_alias =
                query_input.normalized_query

         OR alias.normalized_alias LIKE
                query_input.normalized_query || '%'

         OR alias.normalized_alias LIKE
                '%' || query_input.normalized_query || '%'

         OR alias.normalized_alias %
                query_input.normalized_query
      )

      AND (
            p_product_type IS NULL
         OR product.product_type = p_product_type
      )
),

ranked_matches AS (
    SELECT
        raw_matches.*,

        row_number() OVER (
            PARTITION BY raw_matches.product_id
            ORDER BY
                raw_matches.match_score DESC,
                raw_matches.priority DESC,
                raw_matches.alias_type
        ) AS product_rank

    FROM raw_matches
)

SELECT
    product.product_id,
    product.product_type,
    product.source_product_key,

    product.canonical_name,
    product.short_name,
    product.ticker,
    product.isin,
    product.currency_code,
    product.manager_name,

    ranked.alias_type AS matched_alias_type,
    ranked.alias_value AS matched_alias_value,
    ranked.match_type,
    round(ranked.match_score, 4) AS match_score,

    product.has_warning,

    snapshot.snapshot_id,
    snapshot.source_file_name,
    source.source_sheet,
    source.source_row_number,
    snapshot.data_as_of_date,
    snapshot.provenance_status

FROM ranked_matches ranked

JOIN core.product product
    ON product.product_id = ranked.product_id

JOIN raw.source_row source
    ON source.raw_row_id = product.raw_row_id

JOIN meta.dataset_snapshot snapshot
    ON snapshot.snapshot_id = product.snapshot_id

WHERE ranked.product_rank = 1

ORDER BY
    ranked.match_score DESC,
    ranked.priority DESC,
    product.canonical_name

LIMIT least(
    greatest(coalesce(p_limit, 20), 1),
    100
);

$$;


--
-- Name: validate_retrieval_plan(jsonb, text); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.validate_retrieval_plan(p_plan jsonb, p_contract_version text DEFAULT 'retrieval_v1'::text) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
    v_dataset text;
    v_filters jsonb := '{}'::jsonb;
    v_required jsonb := '[]'::jsonb;
    v_sort jsonb;

    v_unsupported jsonb := '[]'::jsonb;
    v_invalid jsonb := '[]'::jsonb;

    v_field text;
    v_rule jsonb;
    v_operator text;
    v_filter_value jsonb;
    v_mapping record;

    v_dataset_supported boolean := false;
    v_out_of_scope boolean := false;
    v_semantic_requested boolean := false;
    v_top_k numeric;
    v_reason_hint text;
BEGIN
    /* 전체 입력 형식 검사 */
    IF p_plan IS NULL
       OR jsonb_typeof(p_plan) <> 'object' THEN
        RETURN jsonb_build_object(
            'valid', false,
            'contract_version', p_contract_version,
            'dataset', null,
            'unsupported_fields', '[]'::jsonb,
            'invalid_values', jsonb_build_array(
                jsonb_build_object(
                    'field', '$',
                    'value', p_plan,
                    'reason', 'plan_must_be_object'
                )
            ),
            'reason_hint', 'INVALID_VALUE'
        );
    END IF;

    /* 필수 최상위 필드 검사 */
    FOREACH v_field IN ARRAY ARRAY[
        'intent',
        'dataset',
        'filters',
        'semantic_query',
        'sort',
        'top_k',
        'required_fields'
    ]
    LOOP
        IF NOT (p_plan ? v_field) THEN
            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', v_field,
                    'value', null,
                    'reason', 'required_key_missing'
                )
            );
        END IF;
    END LOOP;

    /* intent 검사 */
    IF p_plan ? 'intent'
       AND (
           jsonb_typeof(p_plan->'intent') <> 'string'
           OR btrim(p_plan->>'intent') = ''
       ) THEN
        v_invalid := v_invalid || jsonb_build_array(
            jsonb_build_object(
                'field', 'intent',
                'value', p_plan->'intent',
                'reason', 'non_empty_string_required'
            )
        );
    END IF;

    /* dataset 검사 */
    IF p_plan ? 'dataset'
       AND jsonb_typeof(p_plan->'dataset') = 'string'
       AND btrim(p_plan->>'dataset') <> '' THEN

        v_dataset := lower(btrim(p_plan->>'dataset'));

        SELECT EXISTS (
            SELECT 1
            FROM meta.retrieval_field_mapping AS mapping
            WHERE mapping.contract_version = p_contract_version
              AND mapping.contract_dataset = v_dataset
              AND mapping.is_active = true
        )
        INTO v_dataset_supported;

        IF NOT v_dataset_supported THEN
            v_out_of_scope := true;

            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', 'dataset',
                    'value', v_dataset,
                    'reason', 'dataset_not_supported'
                )
            );
        END IF;
    ELSE
        v_invalid := v_invalid || jsonb_build_array(
            jsonb_build_object(
                'field', 'dataset',
                'value', p_plan->'dataset',
                'reason', 'non_empty_string_required'
            )
        );
    END IF;

    /* filters 검사 */
    IF p_plan ? 'filters'
       AND jsonb_typeof(p_plan->'filters') = 'object' THEN
        v_filters := p_plan->'filters';
    ELSIF p_plan ? 'filters' THEN
        v_invalid := v_invalid || jsonb_build_array(
            jsonb_build_object(
                'field', 'filters',
                'value', p_plan->'filters',
                'reason', 'object_required'
            )
        );
    END IF;

    /* required_fields 검사 */
    IF p_plan ? 'required_fields'
       AND jsonb_typeof(p_plan->'required_fields') = 'array' THEN
        v_required := p_plan->'required_fields';

        IF EXISTS (
            SELECT 1
            FROM jsonb_array_elements(v_required) AS item(value)
            WHERE jsonb_typeof(item.value) <> 'string'
        ) THEN
            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', 'required_fields',
                    'value', v_required,
                    'reason', 'string_array_required'
                )
            );
        END IF;
    ELSIF p_plan ? 'required_fields' THEN
        v_invalid := v_invalid || jsonb_build_array(
            jsonb_build_object(
                'field', 'required_fields',
                'value', p_plan->'required_fields',
                'reason', 'array_required'
            )
        );
    END IF;

    /* top_k 검사 */
    IF p_plan ? 'top_k'
       AND jsonb_typeof(p_plan->'top_k') = 'number' THEN
        BEGIN
            v_top_k := (p_plan->>'top_k')::numeric;

            IF v_top_k <> trunc(v_top_k)
               OR v_top_k < 1
               OR v_top_k > 100 THEN
                v_invalid := v_invalid || jsonb_build_array(
                    jsonb_build_object(
                        'field', 'top_k',
                        'value', p_plan->'top_k',
                        'reason', 'integer_between_1_and_100_required'
                    )
                );
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', 'top_k',
                    'value', p_plan->'top_k',
                    'reason', 'invalid_number'
                )
            );
        END;
    ELSIF p_plan ? 'top_k' THEN
        v_invalid := v_invalid || jsonb_build_array(
            jsonb_build_object(
                'field', 'top_k',
                'value', p_plan->'top_k',
                'reason', 'number_required'
            )
        );
    END IF;

    /* 필터 필드와 연산자 검사 */
    IF v_dataset_supported THEN
        FOR v_field, v_rule IN
            SELECT
                lower(btrim(filter_item.key)),
                filter_item.value
            FROM jsonb_each(v_filters) AS filter_item
        LOOP
            SELECT mapping.*
            INTO v_mapping
            FROM meta.retrieval_field_mapping AS mapping
            WHERE mapping.contract_version = p_contract_version
              AND mapping.contract_dataset IN ('all', v_dataset)
              AND mapping.contract_field = v_field
              AND mapping.is_active = true
            ORDER BY
                (mapping.contract_dataset = v_dataset) DESC
            LIMIT 1;

            IF NOT FOUND THEN
                v_unsupported := v_unsupported || to_jsonb(v_field);

            ELSIF NOT v_mapping.is_filterable THEN
                v_unsupported := v_unsupported || to_jsonb(v_field);

            ELSIF upper(v_mapping.unit_status) = 'PENDING' THEN
                v_unsupported := v_unsupported || to_jsonb(v_field);

            ELSIF jsonb_typeof(v_rule) <> 'object'
                  OR NOT (v_rule ? 'operator')
                  OR NOT (v_rule ? 'value') THEN
                v_invalid := v_invalid || jsonb_build_array(
                    jsonb_build_object(
                        'field', v_field,
                        'value', v_rule,
                        'reason', 'operator_and_value_required'
                    )
                );

            ELSIF jsonb_typeof(v_rule->'operator') <> 'string' THEN
                v_invalid := v_invalid || jsonb_build_array(
                    jsonb_build_object(
                        'field', v_field,
                        'value', v_rule->'operator',
                        'reason', 'operator_must_be_string'
                    )
                );

            ELSE
                v_operator :=
                    lower(btrim(v_rule->>'operator'));
                v_filter_value := v_rule->'value';

                IF NOT EXISTS (
                    SELECT 1
                    FROM unnest(
                        v_mapping.allowed_operators
                    ) AS allowed(operator_name)
                    WHERE lower(allowed.operator_name)
                          = v_operator
                ) THEN
                    v_invalid := v_invalid || jsonb_build_array(
                        jsonb_build_object(
                            'field', v_field,
                            'value', v_operator,
                            'reason', 'operator_not_allowed'
                        )
                    );

                ELSIF v_operator IN (
                    'in',
                    'not_in',
                    'between'
                )
                AND jsonb_typeof(v_filter_value) <> 'array' THEN
                    v_invalid := v_invalid || jsonb_build_array(
                        jsonb_build_object(
                            'field', v_field,
                            'value', v_filter_value,
                            'reason', 'array_value_required'
                        )
                    );

                ELSIF v_operator NOT IN (
                    'in',
                    'not_in',
                    'between'
                )
                AND lower(v_mapping.data_type) IN (
                    'numeric',
                    'number',
                    'integer',
                    'bigint',
                    'decimal'
                )
                AND jsonb_typeof(v_filter_value) <> 'number' THEN
                    v_invalid := v_invalid || jsonb_build_array(
                        jsonb_build_object(
                            'field', v_field,
                            'value', v_filter_value,
                            'reason', 'numeric_value_required'
                        )
                    );
                END IF;
            END IF;
        END LOOP;

        /* 반환을 요구한 필드 검사 */
        IF NOT EXISTS (
            SELECT 1
            FROM jsonb_array_elements(v_required)
                AS required_item(value)
            WHERE jsonb_typeof(required_item.value)
                  <> 'string'
        ) THEN
            FOR v_field IN
                SELECT lower(
                    btrim(required_item.value #>> '{}')
                )
                FROM jsonb_array_elements(v_required)
                    AS required_item(value)
            LOOP
                SELECT mapping.*
                INTO v_mapping
                FROM meta.retrieval_field_mapping AS mapping
                WHERE mapping.contract_version =
                      p_contract_version
                  AND mapping.contract_dataset IN (
                      'all',
                      v_dataset
                  )
                  AND mapping.contract_field = v_field
                  AND mapping.is_active = true
                ORDER BY
                    (mapping.contract_dataset = v_dataset)
                    DESC
                LIMIT 1;

                IF NOT FOUND
                   OR upper(v_mapping.unit_status) =
                      'PENDING' THEN
                    v_unsupported :=
                        v_unsupported || to_jsonb(v_field);
                END IF;
            END LOOP;
        END IF;
    END IF;

    /* sort 검사 */
    IF p_plan ? 'sort'
       AND p_plan->'sort' <> 'null'::jsonb THEN

        IF jsonb_typeof(p_plan->'sort') <> 'object' THEN
            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', 'sort',
                    'value', p_plan->'sort',
                    'reason', 'object_or_null_required'
                )
            );
        ELSE
            v_sort := p_plan->'sort';
            v_field :=
                lower(btrim(v_sort->>'field'));

            IF v_field IS NULL
               OR v_field = ''
               OR lower(btrim(v_sort->>'direction'))
                  NOT IN ('asc', 'desc') THEN
                v_invalid := v_invalid || jsonb_build_array(
                    jsonb_build_object(
                        'field', 'sort',
                        'value', v_sort,
                        'reason',
                        'field_and_asc_or_desc_required'
                    )
                );
            ELSIF v_dataset_supported THEN
                SELECT mapping.*
                INTO v_mapping
                FROM meta.retrieval_field_mapping AS mapping
                WHERE mapping.contract_version =
                      p_contract_version
                  AND mapping.contract_dataset IN (
                      'all',
                      v_dataset
                  )
                  AND mapping.contract_field = v_field
                  AND mapping.is_active = true
                ORDER BY
                    (mapping.contract_dataset = v_dataset)
                    DESC
                LIMIT 1;

                IF NOT FOUND
                   OR NOT v_mapping.is_sortable
                   OR upper(v_mapping.unit_status) =
                      'PENDING' THEN
                    v_unsupported :=
                        v_unsupported || to_jsonb(v_field);
                END IF;
            END IF;
        END IF;
    END IF;

    /* semantic_query 검사 */
    IF p_plan ? 'semantic_query'
       AND p_plan->'semantic_query' <> 'null'::jsonb THEN

        IF jsonb_typeof(p_plan->'semantic_query')
           <> 'string'
           OR btrim(p_plan->>'semantic_query') = '' THEN
            v_invalid := v_invalid || jsonb_build_array(
                jsonb_build_object(
                    'field', 'semantic_query',
                    'value', p_plan->'semantic_query',
                    'reason', 'non_empty_string_or_null_required'
                )
            );
        ELSE
            v_semantic_requested := true;
        END IF;
    END IF;

    IF v_semantic_requested
       AND v_dataset_supported
       AND NOT EXISTS (
           SELECT 1
           FROM meta.retrieval_field_mapping AS mapping
           WHERE mapping.contract_version =
                 p_contract_version
             AND mapping.contract_dataset IN (
                 'all',
                 v_dataset
             )
             AND mapping.is_semantic = true
             AND mapping.is_active = true
       ) THEN
        v_unsupported :=
            v_unsupported || to_jsonb('semantic_query'::text);
    END IF;

    /* 중복된 미지원 필드 제거 */
    SELECT coalesce(
        jsonb_agg(
            to_jsonb(deduplicated.field_name)
            ORDER BY deduplicated.field_name
        ),
        '[]'::jsonb
    )
    INTO v_unsupported
    FROM (
        SELECT DISTINCT
            unsupported_item.value #>> '{}' AS field_name
        FROM jsonb_array_elements(v_unsupported)
            AS unsupported_item(value)
    ) AS deduplicated;

    /* 대표 사유 코드 결정 */
    IF v_out_of_scope THEN
        v_reason_hint := 'OUT_OF_SCOPE';
    ELSIF jsonb_array_length(v_invalid) > 0 THEN
        v_reason_hint := 'INVALID_VALUE';
    ELSIF jsonb_array_length(v_unsupported) > 0 THEN
        v_reason_hint := 'FIELD_NOT_SUPPORTED';
    ELSE
        v_reason_hint := null;
    END IF;

    RETURN jsonb_build_object(
        'valid',
            NOT v_out_of_scope
            AND jsonb_array_length(v_invalid) = 0
            AND jsonb_array_length(v_unsupported) = 0,
        'contract_version', p_contract_version,
        'dataset', v_dataset,
        'unsupported_fields', v_unsupported,
        'invalid_values', v_invalid,
        'reason_hint', v_reason_hint
    );
END;
$_$;


--
-- Name: validation_issue; Type: TABLE; Schema: audit; Owner: -
--

CREATE TABLE audit.validation_issue (
    issue_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    raw_row_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_key text,
    source_sheet text NOT NULL,
    source_row_number integer NOT NULL,
    severity text NOT NULL,
    issue_code text NOT NULL,
    column_name text,
    observed_value text,
    message text NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT validation_issue_severity_check CHECK ((severity = ANY (ARRAY['ERROR'::text, 'WARNING'::text])))
);


--
-- Name: validation_issue_issue_id_seq; Type: SEQUENCE; Schema: audit; Owner: -
--

ALTER TABLE audit.validation_issue ALTER COLUMN issue_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME audit.validation_issue_issue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accepted_source_row; Type: VIEW; Schema: core; Owner: -
--

CREATE VIEW core.accepted_source_row AS
 SELECT raw_row_id,
    snapshot_id,
    dataset_code,
    source_sheet,
    source_row_number,
    source_key,
    payload,
    row_hash,
    validation_errors,
    loaded_at,
    (EXISTS ( SELECT 1
           FROM audit.validation_issue warning
          WHERE ((warning.raw_row_id = r.raw_row_id) AND (warning.severity = 'WARNING'::text)))) AS has_warning
   FROM raw.source_row r
  WHERE (NOT (EXISTS ( SELECT 1
           FROM audit.validation_issue error
          WHERE ((error.raw_row_id = r.raw_row_id) AND (error.severity = 'ERROR'::text)))));


--
-- Name: domestic_bond_quote; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.domestic_bond_quote (
    bond_quote_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    raw_row_id bigint NOT NULL,
    pd_no text NOT NULL,
    pd_exg_mkt text NOT NULL,
    info_seq integer NOT NULL,
    info_base_date date NOT NULL,
    product_name text NOT NULL,
    short_name text,
    issuer_name text,
    currency_code text,
    bond_type text,
    major_class text,
    minor_class text,
    interest_rate_type text,
    interest_payment_type text,
    offer_type text,
    credit_grade text,
    credit_grade_date date,
    issue_date date,
    maturity_date date,
    remaining_days integer,
    coupon_rate_pct numeric,
    applied_yield_pct numeric,
    evaluated_price numeric,
    dirty_price numeric,
    duration_years numeric,
    issue_amount numeric,
    outstanding_amount numeric,
    risk_grade_code text,
    risk_name text,
    pension_eligible boolean,
    has_warning boolean DEFAULT false NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: domestic_bond_quote_bond_quote_id_seq; Type: SEQUENCE; Schema: core; Owner: -
--

ALTER TABLE core.domestic_bond_quote ALTER COLUMN bond_quote_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME core.domestic_bond_quote_bond_quote_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product_product_id_seq; Type: SEQUENCE; Schema: core; Owner: -
--

ALTER TABLE core.product ALTER COLUMN product_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME core.product_product_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: allowed_values; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.allowed_values (
    allowed_value_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_table text NOT NULL,
    normalized_field text NOT NULL,
    allowed_value text NOT NULL,
    normalized_value text NOT NULL,
    aliases text[] DEFAULT '{}'::text[] NOT NULL,
    record_count bigint DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: allowed_values_allowed_value_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.allowed_values ALTER COLUMN allowed_value_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.allowed_values_allowed_value_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: answer_status_code; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.answer_status_code (
    status_code text NOT NULL,
    description text NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: coverage_profile; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.coverage_profile (
    coverage_id bigint NOT NULL,
    data_version_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_table text NOT NULL,
    normalized_field text NOT NULL,
    total_rows bigint NOT NULL,
    non_null_rows bigint NOT NULL,
    missing_rows bigint NOT NULL,
    coverage_pct numeric(7,2) NOT NULL,
    distinct_count bigint NOT NULL,
    calculated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: coverage_profile_coverage_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.coverage_profile ALTER COLUMN coverage_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.coverage_profile_coverage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_version; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.data_version (
    data_version_id bigint NOT NULL,
    version_name text NOT NULL,
    status text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    activated_at timestamp with time zone,
    CONSTRAINT data_version_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'ACTIVE'::text, 'RETIRED'::text])))
);


--
-- Name: data_version_data_version_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.data_version ALTER COLUMN data_version_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.data_version_data_version_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_version_snapshot; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.data_version_snapshot (
    data_version_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    dataset_code text NOT NULL
);


--
-- Name: schema_migration; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.schema_migration (
    version text NOT NULL,
    description text NOT NULL,
    checksum_sha256 text NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    applied_by text DEFAULT CURRENT_USER NOT NULL
);


--
-- Name: dataset_snapshot_snapshot_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.dataset_snapshot ALTER COLUMN snapshot_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.dataset_snapshot_snapshot_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: field_catalog; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.field_catalog (
    field_id bigint NOT NULL,
    dataset_code text NOT NULL,
    source_table text NOT NULL,
    normalized_field text NOT NULL,
    source_fields jsonb DEFAULT '{}'::jsonb NOT NULL,
    display_name_ko text NOT NULL,
    description text DEFAULT '설명 미정'::text NOT NULL,
    data_type text NOT NULL,
    unit text,
    search_method text DEFAULT 'UNCONFIGURED'::text NOT NULL,
    aliases text[] DEFAULT ARRAY[]::text[] NOT NULL,
    is_filterable boolean DEFAULT false NOT NULL,
    is_sortable boolean DEFAULT false NOT NULL,
    is_semantic_searchable boolean DEFAULT false NOT NULL,
    is_exposed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT field_catalog_data_type_check CHECK ((data_type = ANY (ARRAY['TEXT'::text, 'NUMERIC'::text, 'DATE'::text, 'BOOLEAN'::text, 'JSON'::text, 'OTHER'::text]))),
    CONSTRAINT field_catalog_search_method_check CHECK ((search_method = ANY (ARRAY['UNCONFIGURED'::text, 'EXACT'::text, 'EXACT_PARTIAL'::text, 'CATEGORY'::text, 'RANGE_SORT'::text, 'SEMANTIC'::text])))
);


--
-- Name: field_catalog_field_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.field_catalog ALTER COLUMN field_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.field_catalog_field_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: retrieval_contract; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.retrieval_contract (
    contract_version text NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    frozen_at timestamp with time zone,
    CONSTRAINT retrieval_contract_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'FROZEN'::text, 'RETIRED'::text])))
);


--
-- Name: retrieval_dataset; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.retrieval_dataset (
    contract_version text NOT NULL,
    contract_dataset text NOT NULL,
    internal_dataset_code text NOT NULL,
    product_type text NOT NULL,
    core_table text NOT NULL,
    is_selectable boolean DEFAULT true NOT NULL,
    semantic_supported boolean DEFAULT false NOT NULL
);


--
-- Name: retrieval_field_mapping; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.retrieval_field_mapping (
    mapping_id bigint NOT NULL,
    contract_version text NOT NULL,
    contract_dataset text NOT NULL,
    contract_field text NOT NULL,
    internal_table text NOT NULL,
    internal_column text NOT NULL,
    data_type text NOT NULL,
    unit text,
    unit_status text DEFAULT 'NOT_APPLICABLE'::text NOT NULL,
    allowed_operators text[] NOT NULL,
    is_filterable boolean DEFAULT true NOT NULL,
    is_sortable boolean DEFAULT false NOT NULL,
    is_semantic boolean DEFAULT false NOT NULL,
    evidence_supported boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT retrieval_field_mapping_data_type_check CHECK ((data_type = ANY (ARRAY['TEXT'::text, 'CATEGORY'::text, 'NUMERIC'::text, 'DATE'::text, 'SEMANTIC_TEXT'::text]))),
    CONSTRAINT retrieval_field_mapping_unit_status_check CHECK ((unit_status = ANY (ARRAY['NOT_APPLICABLE'::text, 'VERIFIED'::text, 'PENDING'::text])))
);


--
-- Name: retrieval_field_mapping_mapping_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.retrieval_field_mapping ALTER COLUMN mapping_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.retrieval_field_mapping_mapping_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: retrieval_reason_code; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.retrieval_reason_code (
    reason_code text NOT NULL,
    suggested_status text,
    description text NOT NULL,
    decided_by text DEFAULT 'B'::text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    CONSTRAINT retrieval_reason_code_decided_by_check CHECK ((decided_by = ANY (ARRAY['A'::text, 'B'::text, 'A_AND_B'::text])))
);


--
-- Name: value_provenance; Type: TABLE; Schema: meta; Owner: -
--

CREATE TABLE meta.value_provenance (
    provenance_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    raw_row_id bigint NOT NULL,
    field_name text NOT NULL,
    was_missing boolean NOT NULL,
    fill_type text NOT NULL,
    fill_source text,
    fill_confidence numeric,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    source_reference text,
    source_as_of_date date,
    verified_by text,
    verified_at timestamp with time zone,
    verification_note text,
    evidence_eligible boolean GENERATED ALWAYS AS (
CASE
    WHEN (fill_type = 'original'::text) THEN true
    WHEN (fill_type = 'official_fill'::text) THEN ((source_reference IS NOT NULL) AND (source_as_of_date IS NOT NULL))
    WHEN (fill_type = 'manual_verified'::text) THEN ((source_reference IS NOT NULL) AND (source_as_of_date IS NOT NULL) AND (verified_by IS NOT NULL) AND (verified_at IS NOT NULL))
    ELSE false
END) STORED,
    CONSTRAINT ck_manual_verified_source CHECK (((fill_type <> 'manual_verified'::text) OR ((source_reference IS NOT NULL) AND (source_as_of_date IS NOT NULL) AND (verified_by IS NOT NULL) AND (verified_at IS NOT NULL)))),
    CONSTRAINT ck_official_fill_source CHECK (((fill_type <> 'official_fill'::text) OR ((source_reference IS NOT NULL) AND (source_as_of_date IS NOT NULL)))),
    CONSTRAINT ck_value_provenance_fill_type_v1 CHECK ((fill_type = ANY (ARRAY['original'::text, 'official_fill'::text, 'manual_verified'::text, 'estimated'::text]))),
    CONSTRAINT value_provenance_fill_confidence_check CHECK (((fill_confidence IS NULL) OR ((fill_confidence >= (0)::numeric) AND (fill_confidence <= (1)::numeric))))
);


--
-- Name: value_provenance_provenance_id_seq; Type: SEQUENCE; Schema: meta; Owner: -
--

ALTER TABLE meta.value_provenance ALTER COLUMN provenance_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME meta.value_provenance_provenance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: source_row_raw_row_id_seq; Type: SEQUENCE; Schema: raw; Owner: -
--

ALTER TABLE raw.source_row ALTER COLUMN raw_row_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME raw.source_row_raw_row_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: overseas_etf_strategy; Type: TABLE; Schema: search; Owner: -
--

CREATE TABLE search.overseas_etf_strategy (
    document_id bigint NOT NULL,
    product_id bigint NOT NULL,
    snapshot_id bigint NOT NULL,
    raw_row_id bigint NOT NULL,
    source_field text DEFAULT 'cu_strtegy'::text NOT NULL,
    raw_text text NOT NULL,
    normalized_text text NOT NULL,
    text_hash character(32) NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, COALESCE(normalized_text, ''::text))) STORED,
    has_warning boolean DEFAULT false NOT NULL,
    embedding_model text,
    embedded_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    embedding public.vector(1024)
);


--
-- Name: overseas_etf_strategy_document_id_seq; Type: SEQUENCE; Schema: search; Owner: -
--

ALTER TABLE search.overseas_etf_strategy ALTER COLUMN document_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME search.overseas_etf_strategy_document_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: product_alias; Type: TABLE; Schema: search; Owner: -
--

CREATE TABLE search.product_alias (
    alias_id bigint NOT NULL,
    product_id bigint NOT NULL,
    alias_type text NOT NULL,
    alias_value text NOT NULL,
    normalized_alias text NOT NULL,
    alias_source text DEFAULT 'CORE'::text NOT NULL,
    priority smallint DEFAULT 50 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT product_alias_alias_type_check CHECK ((alias_type = ANY (ARRAY['CANONICAL_NAME'::text, 'SHORT_NAME'::text, 'SOURCE_KEY'::text, 'TICKER'::text, 'ISIN'::text])))
);


--
-- Name: product_alias_alias_id_seq; Type: SEQUENCE; Schema: search; Owner: -
--

ALTER TABLE search.product_alias ALTER COLUMN alias_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME search.product_alias_alias_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: validation_issue validation_issue_pkey; Type: CONSTRAINT; Schema: audit; Owner: -
--

ALTER TABLE ONLY audit.validation_issue
    ADD CONSTRAINT validation_issue_pkey PRIMARY KEY (issue_id);


--
-- Name: validation_issue validation_issue_raw_row_id_issue_code_key; Type: CONSTRAINT; Schema: audit; Owner: -
--

ALTER TABLE ONLY audit.validation_issue
    ADD CONSTRAINT validation_issue_raw_row_id_issue_code_key UNIQUE (raw_row_id, issue_code);


--
-- Name: domestic_bond_quote domestic_bond_quote_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_bond_quote
    ADD CONSTRAINT domestic_bond_quote_pkey PRIMARY KEY (bond_quote_id);


--
-- Name: domestic_bond_quote domestic_bond_quote_raw_row_id_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_bond_quote
    ADD CONSTRAINT domestic_bond_quote_raw_row_id_key UNIQUE (raw_row_id);


--
-- Name: domestic_bond_quote domestic_bond_quote_snapshot_id_pd_no_pd_exg_mkt_info_seq_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_bond_quote
    ADD CONSTRAINT domestic_bond_quote_snapshot_id_pd_no_pd_exg_mkt_info_seq_key UNIQUE (snapshot_id, pd_no, pd_exg_mkt, info_seq);


--
-- Name: domestic_etp domestic_etp_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_etp
    ADD CONSTRAINT domestic_etp_pkey PRIMARY KEY (product_id);


--
-- Name: fund_class fund_class_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.fund_class
    ADD CONSTRAINT fund_class_pkey PRIMARY KEY (product_id);


--
-- Name: overseas_etp overseas_etp_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.overseas_etp
    ADD CONSTRAINT overseas_etp_pkey PRIMARY KEY (product_id);


--
-- Name: product product_pkey; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.product
    ADD CONSTRAINT product_pkey PRIMARY KEY (product_id);


--
-- Name: product product_raw_row_id_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.product
    ADD CONSTRAINT product_raw_row_id_key UNIQUE (raw_row_id);


--
-- Name: product product_snapshot_id_product_type_source_product_key_key; Type: CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.product
    ADD CONSTRAINT product_snapshot_id_product_type_source_product_key_key UNIQUE (snapshot_id, product_type, source_product_key);


--
-- Name: allowed_values allowed_values_dataset_code_source_table_normalized_field_n_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.allowed_values
    ADD CONSTRAINT allowed_values_dataset_code_source_table_normalized_field_n_key UNIQUE (dataset_code, source_table, normalized_field, normalized_value);


--
-- Name: allowed_values allowed_values_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.allowed_values
    ADD CONSTRAINT allowed_values_pkey PRIMARY KEY (allowed_value_id);


--
-- Name: answer_status_code answer_status_code_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.answer_status_code
    ADD CONSTRAINT answer_status_code_pkey PRIMARY KEY (status_code);


--
-- Name: coverage_profile coverage_profile_data_version_id_dataset_code_source_table__key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.coverage_profile
    ADD CONSTRAINT coverage_profile_data_version_id_dataset_code_source_table__key UNIQUE (data_version_id, dataset_code, source_table, normalized_field);


--
-- Name: coverage_profile coverage_profile_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.coverage_profile
    ADD CONSTRAINT coverage_profile_pkey PRIMARY KEY (coverage_id);


--
-- Name: data_version data_version_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version
    ADD CONSTRAINT data_version_pkey PRIMARY KEY (data_version_id);


--
-- Name: data_version_snapshot data_version_snapshot_data_version_id_snapshot_id_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version_snapshot
    ADD CONSTRAINT data_version_snapshot_data_version_id_snapshot_id_key UNIQUE (data_version_id, snapshot_id);


--
-- Name: data_version_snapshot data_version_snapshot_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version_snapshot
    ADD CONSTRAINT data_version_snapshot_pkey PRIMARY KEY (data_version_id, dataset_code);


--
-- Name: data_version data_version_version_name_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version
    ADD CONSTRAINT data_version_version_name_key UNIQUE (version_name);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (version);


--
-- Name: dataset_snapshot dataset_snapshot_dataset_code_file_sha256_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.dataset_snapshot
    ADD CONSTRAINT dataset_snapshot_dataset_code_file_sha256_key UNIQUE (dataset_code, file_sha256);


--
-- Name: dataset_snapshot dataset_snapshot_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.dataset_snapshot
    ADD CONSTRAINT dataset_snapshot_pkey PRIMARY KEY (snapshot_id);


--
-- Name: field_catalog field_catalog_dataset_code_source_table_normalized_field_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.field_catalog
    ADD CONSTRAINT field_catalog_dataset_code_source_table_normalized_field_key UNIQUE (dataset_code, source_table, normalized_field);


--
-- Name: field_catalog field_catalog_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.field_catalog
    ADD CONSTRAINT field_catalog_pkey PRIMARY KEY (field_id);


--
-- Name: retrieval_contract retrieval_contract_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_contract
    ADD CONSTRAINT retrieval_contract_pkey PRIMARY KEY (contract_version);


--
-- Name: retrieval_dataset retrieval_dataset_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_dataset
    ADD CONSTRAINT retrieval_dataset_pkey PRIMARY KEY (contract_version, contract_dataset);


--
-- Name: retrieval_field_mapping retrieval_field_mapping_contract_version_contract_dataset_c_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_field_mapping
    ADD CONSTRAINT retrieval_field_mapping_contract_version_contract_dataset_c_key UNIQUE (contract_version, contract_dataset, contract_field);


--
-- Name: retrieval_field_mapping retrieval_field_mapping_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_field_mapping
    ADD CONSTRAINT retrieval_field_mapping_pkey PRIMARY KEY (mapping_id);


--
-- Name: retrieval_reason_code retrieval_reason_code_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_reason_code
    ADD CONSTRAINT retrieval_reason_code_pkey PRIMARY KEY (reason_code);


--
-- Name: value_provenance value_provenance_pkey; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.value_provenance
    ADD CONSTRAINT value_provenance_pkey PRIMARY KEY (provenance_id);


--
-- Name: value_provenance value_provenance_raw_row_id_field_name_key; Type: CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.value_provenance
    ADD CONSTRAINT value_provenance_raw_row_id_field_name_key UNIQUE (raw_row_id, field_name);


--
-- Name: source_row source_row_pkey; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.source_row
    ADD CONSTRAINT source_row_pkey PRIMARY KEY (raw_row_id);


--
-- Name: source_row source_row_snapshot_id_source_sheet_source_row_number_key; Type: CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.source_row
    ADD CONSTRAINT source_row_snapshot_id_source_sheet_source_row_number_key UNIQUE (snapshot_id, source_sheet, source_row_number);


--
-- Name: overseas_etf_strategy overseas_etf_strategy_pkey; Type: CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.overseas_etf_strategy
    ADD CONSTRAINT overseas_etf_strategy_pkey PRIMARY KEY (document_id);


--
-- Name: overseas_etf_strategy overseas_etf_strategy_product_id_snapshot_id_text_hash_key; Type: CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.overseas_etf_strategy
    ADD CONSTRAINT overseas_etf_strategy_product_id_snapshot_id_text_hash_key UNIQUE (product_id, snapshot_id, text_hash);


--
-- Name: product_alias product_alias_pkey; Type: CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.product_alias
    ADD CONSTRAINT product_alias_pkey PRIMARY KEY (alias_id);


--
-- Name: product_alias product_alias_product_id_alias_type_normalized_alias_key; Type: CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.product_alias
    ADD CONSTRAINT product_alias_product_id_alias_type_normalized_alias_key UNIQUE (product_id, alias_type, normalized_alias);


--
-- Name: ix_validation_dataset; Type: INDEX; Schema: audit; Owner: -
--

CREATE INDEX ix_validation_dataset ON audit.validation_issue USING btree (dataset_code, issue_code);


--
-- Name: ix_validation_snapshot; Type: INDEX; Schema: audit; Owner: -
--

CREATE INDEX ix_validation_snapshot ON audit.validation_issue USING btree (snapshot_id, severity);


--
-- Name: ix_bond_applied_yield; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_bond_applied_yield ON core.domestic_bond_quote USING btree (applied_yield_pct);


--
-- Name: ix_bond_base_date; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_bond_base_date ON core.domestic_bond_quote USING btree (info_base_date);


--
-- Name: ix_bond_credit_grade; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_bond_credit_grade ON core.domestic_bond_quote USING btree (credit_grade);


--
-- Name: ix_bond_maturity; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_bond_maturity ON core.domestic_bond_quote USING btree (maturity_date);


--
-- Name: ix_bond_pd_no; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_bond_pd_no ON core.domestic_bond_quote USING btree (pd_no);


--
-- Name: ix_domestic_etp_aum; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_domestic_etp_aum ON core.domestic_etp USING btree (aum DESC) WHERE (aum IS NOT NULL);


--
-- Name: ix_domestic_etp_listing_date; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_domestic_etp_listing_date ON core.domestic_etp USING btree (listing_date);


--
-- Name: ix_fund_class_net_asset; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_fund_class_net_asset ON core.fund_class USING btree (net_asset_amount DESC) WHERE (net_asset_amount IS NOT NULL);


--
-- Name: ix_fund_class_return_1y; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_fund_class_return_1y ON core.fund_class USING btree (return_1y_pct DESC) WHERE (return_1y_pct IS NOT NULL);


--
-- Name: ix_fund_class_risk_grade; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_fund_class_risk_grade ON core.fund_class USING btree (risk_grade);


--
-- Name: ix_overseas_etp_aum; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_overseas_etp_aum ON core.overseas_etp USING btree (aum DESC) WHERE (aum IS NOT NULL);


--
-- Name: ix_overseas_etp_listing_date; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_overseas_etp_listing_date ON core.overseas_etp USING btree (listing_date);


--
-- Name: ix_product_isin; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_product_isin ON core.product USING btree (isin) WHERE (isin IS NOT NULL);


--
-- Name: ix_product_name; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_product_name ON core.product USING btree (canonical_name);


--
-- Name: ix_product_ticker; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_product_ticker ON core.product USING btree (ticker) WHERE (ticker IS NOT NULL);


--
-- Name: ix_product_type; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_product_type ON core.product USING btree (product_type);


--
-- Name: ix_allowed_values_lookup; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_allowed_values_lookup ON meta.allowed_values USING btree (dataset_code, normalized_field, normalized_value);


--
-- Name: ix_coverage_profile_lookup; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_coverage_profile_lookup ON meta.coverage_profile USING btree (data_version_id, dataset_code, normalized_field);


--
-- Name: ux_data_version_single_active; Type: INDEX; Schema: meta; Owner: -
--

CREATE UNIQUE INDEX ux_data_version_single_active ON meta.data_version (status) WHERE (status = 'ACTIVE'::text);


--
-- Name: ix_field_catalog_dataset; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_field_catalog_dataset ON meta.field_catalog USING btree (dataset_code);


--
-- Name: ix_field_catalog_exposed; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_field_catalog_exposed ON meta.field_catalog USING btree (dataset_code, is_exposed) WHERE (is_exposed = true);


--
-- Name: ix_provenance_fill_type; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_provenance_fill_type ON meta.value_provenance USING btree (fill_type);


--
-- Name: ix_provenance_raw_row; Type: INDEX; Schema: meta; Owner: -
--

CREATE INDEX ix_provenance_raw_row ON meta.value_provenance USING btree (raw_row_id);


--
-- Name: ix_raw_dataset_snapshot; Type: INDEX; Schema: raw; Owner: -
--

CREATE INDEX ix_raw_dataset_snapshot ON raw.source_row USING btree (dataset_code, snapshot_id);


--
-- Name: uq_raw_source_key; Type: INDEX; Schema: raw; Owner: -
--

CREATE UNIQUE INDEX uq_raw_source_key ON raw.source_row USING btree (snapshot_id, source_key) WHERE (source_key IS NOT NULL);


--
-- Name: ix_overseas_strategy_fts; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_overseas_strategy_fts ON search.overseas_etf_strategy USING gin (fts);


--
-- Name: ix_overseas_strategy_product; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_overseas_strategy_product ON search.overseas_etf_strategy USING btree (product_id);


--
-- Name: ix_overseas_strategy_trgm; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_overseas_strategy_trgm ON search.overseas_etf_strategy USING gin (normalized_text public.gin_trgm_ops);


--
-- Name: ix_product_alias_exact; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_product_alias_exact ON search.product_alias USING btree (normalized_alias);


--
-- Name: ix_product_alias_product; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_product_alias_product ON search.product_alias USING btree (product_id);


--
-- Name: ix_product_alias_trgm; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_product_alias_trgm ON search.product_alias USING gin (normalized_alias public.gin_trgm_ops);


--
-- Name: ix_product_alias_type_exact; Type: INDEX; Schema: search; Owner: -
--

CREATE INDEX ix_product_alias_type_exact ON search.product_alias USING btree (alias_type, normalized_alias);


--
-- Name: validation_issue validation_issue_raw_row_id_fkey; Type: FK CONSTRAINT; Schema: audit; Owner: -
--

ALTER TABLE ONLY audit.validation_issue
    ADD CONSTRAINT validation_issue_raw_row_id_fkey FOREIGN KEY (raw_row_id) REFERENCES raw.source_row(raw_row_id);


--
-- Name: validation_issue validation_issue_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: audit; Owner: -
--

ALTER TABLE ONLY audit.validation_issue
    ADD CONSTRAINT validation_issue_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: domestic_bond_quote domestic_bond_quote_raw_row_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_bond_quote
    ADD CONSTRAINT domestic_bond_quote_raw_row_id_fkey FOREIGN KEY (raw_row_id) REFERENCES raw.source_row(raw_row_id);


--
-- Name: domestic_bond_quote domestic_bond_quote_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_bond_quote
    ADD CONSTRAINT domestic_bond_quote_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: domestic_etp domestic_etp_product_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.domestic_etp
    ADD CONSTRAINT domestic_etp_product_id_fkey FOREIGN KEY (product_id) REFERENCES core.product(product_id) ON DELETE CASCADE;


--
-- Name: fund_class fund_class_product_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.fund_class
    ADD CONSTRAINT fund_class_product_id_fkey FOREIGN KEY (product_id) REFERENCES core.product(product_id) ON DELETE CASCADE;


--
-- Name: overseas_etp overseas_etp_product_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.overseas_etp
    ADD CONSTRAINT overseas_etp_product_id_fkey FOREIGN KEY (product_id) REFERENCES core.product(product_id) ON DELETE CASCADE;


--
-- Name: product product_raw_row_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.product
    ADD CONSTRAINT product_raw_row_id_fkey FOREIGN KEY (raw_row_id) REFERENCES raw.source_row(raw_row_id);


--
-- Name: product product_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: core; Owner: -
--

ALTER TABLE ONLY core.product
    ADD CONSTRAINT product_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: coverage_profile coverage_profile_data_version_id_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.coverage_profile
    ADD CONSTRAINT coverage_profile_data_version_id_fkey FOREIGN KEY (data_version_id) REFERENCES meta.data_version(data_version_id);


--
-- Name: data_version_snapshot data_version_snapshot_data_version_id_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version_snapshot
    ADD CONSTRAINT data_version_snapshot_data_version_id_fkey FOREIGN KEY (data_version_id) REFERENCES meta.data_version(data_version_id);


--
-- Name: data_version_snapshot data_version_snapshot_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.data_version_snapshot
    ADD CONSTRAINT data_version_snapshot_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: retrieval_dataset retrieval_dataset_contract_version_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_dataset
    ADD CONSTRAINT retrieval_dataset_contract_version_fkey FOREIGN KEY (contract_version) REFERENCES meta.retrieval_contract(contract_version);


--
-- Name: retrieval_field_mapping retrieval_field_mapping_contract_version_contract_dataset_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_field_mapping
    ADD CONSTRAINT retrieval_field_mapping_contract_version_contract_dataset_fkey FOREIGN KEY (contract_version, contract_dataset) REFERENCES meta.retrieval_dataset(contract_version, contract_dataset);


--
-- Name: retrieval_reason_code retrieval_reason_code_suggested_status_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.retrieval_reason_code
    ADD CONSTRAINT retrieval_reason_code_suggested_status_fkey FOREIGN KEY (suggested_status) REFERENCES meta.answer_status_code(status_code);


--
-- Name: value_provenance value_provenance_raw_row_id_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.value_provenance
    ADD CONSTRAINT value_provenance_raw_row_id_fkey FOREIGN KEY (raw_row_id) REFERENCES raw.source_row(raw_row_id);


--
-- Name: value_provenance value_provenance_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: meta; Owner: -
--

ALTER TABLE ONLY meta.value_provenance
    ADD CONSTRAINT value_provenance_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: source_row source_row_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: raw; Owner: -
--

ALTER TABLE ONLY raw.source_row
    ADD CONSTRAINT source_row_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: overseas_etf_strategy overseas_etf_strategy_product_id_fkey; Type: FK CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.overseas_etf_strategy
    ADD CONSTRAINT overseas_etf_strategy_product_id_fkey FOREIGN KEY (product_id) REFERENCES core.product(product_id) ON DELETE CASCADE;


--
-- Name: overseas_etf_strategy overseas_etf_strategy_raw_row_id_fkey; Type: FK CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.overseas_etf_strategy
    ADD CONSTRAINT overseas_etf_strategy_raw_row_id_fkey FOREIGN KEY (raw_row_id) REFERENCES raw.source_row(raw_row_id);


--
-- Name: overseas_etf_strategy overseas_etf_strategy_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.overseas_etf_strategy
    ADD CONSTRAINT overseas_etf_strategy_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES meta.dataset_snapshot(snapshot_id);


--
-- Name: product_alias product_alias_product_id_fkey; Type: FK CONSTRAINT; Schema: search; Owner: -
--

ALTER TABLE ONLY search.product_alias
    ADD CONSTRAINT product_alias_product_id_fkey FOREIGN KEY (product_id) REFERENCES core.product(product_id) ON DELETE CASCADE;


--
-- Name: organization_relation; Type: TABLE; Schema: core; Owner: -
--

CREATE TABLE core.organization_relation (
    relation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_id bigint NOT NULL REFERENCES meta.dataset_snapshot(snapshot_id),
    raw_row_id bigint NOT NULL REFERENCES raw.source_row(raw_row_id),
    predicate text NOT NULL,
    source_entity_id text,
    source_entity_name text NOT NULL,
    target_entity_id text,
    target_entity_name text NOT NULL,
    source_is_listed boolean,
    target_is_listed boolean,
    relation_as_of_date date NOT NULL,
    source_ref text NOT NULL,
    confidence numeric(6,5) NOT NULL,
    has_warning boolean DEFAULT false NOT NULL,
    source_normalized_name text GENERATED ALWAYS AS (
        lower(regexp_replace(btrim(source_entity_name), '\s+', '', 'g'))
    ) STORED,
    target_normalized_name text GENERATED ALWAYS AS (
        lower(regexp_replace(btrim(target_entity_name), '\s+', '', 'g'))
    ) STORED,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT organization_relation_predicate_check CHECK (
        predicate = ANY (ARRAY['subsidiaryOf'::text, 'affiliateOf'::text])
    ),
    CONSTRAINT organization_relation_source_name_check CHECK (
        char_length(btrim(source_entity_name)) > 0
    ),
    CONSTRAINT organization_relation_target_name_check CHECK (
        char_length(btrim(target_entity_name)) > 0
    ),
    CONSTRAINT organization_relation_source_ref_check CHECK (
        char_length(btrim(source_ref)) > 0
    ),
    CONSTRAINT organization_relation_confidence_check CHECK (
        confidence >= 0 AND confidence <= 1
    ),
    CONSTRAINT organization_relation_unique_source UNIQUE (
        snapshot_id,
        predicate,
        source_entity_name,
        target_entity_name,
        source_ref
    )
);


--
-- Name: ix_organization_relation_snapshot; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_organization_relation_snapshot
    ON core.organization_relation (snapshot_id);


--
-- Name: ix_organization_relation_source_name; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_organization_relation_source_name
    ON core.organization_relation (source_normalized_name, predicate);


--
-- Name: ix_organization_relation_target_name; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_organization_relation_target_name
    ON core.organization_relation (target_normalized_name, predicate);


--
-- Name: ix_organization_relation_source_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_organization_relation_source_id
    ON core.organization_relation (lower(btrim(source_entity_id)), predicate)
    WHERE source_entity_id IS NOT NULL;


--
-- Name: ix_organization_relation_target_id; Type: INDEX; Schema: core; Owner: -
--

CREATE INDEX ix_organization_relation_target_id
    ON core.organization_relation (lower(btrim(target_entity_id)), predicate)
    WHERE target_entity_id IS NOT NULL;


--
-- Name: find_organization_relations(text, text[], text, text, boolean, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE FUNCTION search.find_organization_relations(
    p_anchor text,
    p_predicates text[] DEFAULT NULL::text[],
    p_anchor_role text DEFAULT 'either'::text,
    p_result_role text DEFAULT 'opposite'::text,
    p_result_listed boolean DEFAULT NULL::boolean,
    p_limit integer DEFAULT 50
) RETURNS TABLE(
    relation_id bigint,
    predicate text,
    source_entity_id text,
    source_entity_name text,
    target_entity_id text,
    target_entity_name text,
    source_is_listed boolean,
    target_is_listed boolean,
    relation_as_of_date date,
    source_ref text,
    confidence numeric,
    has_warning boolean,
    snapshot_id bigint,
    raw_row_id bigint,
    source_file_name text,
    source_sheet text,
    source_row_number integer,
    data_as_of_date date,
    provenance_status text,
    total_hits bigint
)
    LANGUAGE sql STABLE
    AS $$

WITH query_input AS (
    SELECT
        nullif(
            lower(regexp_replace(btrim(p_anchor), '\s+', '', 'g')),
            ''
        ) AS normalized_anchor,
        nullif(lower(btrim(p_anchor)), '') AS normalized_anchor_id
),

active_relations AS (
    SELECT
        relation.*,
        source.source_sheet,
        source.source_row_number,
        snapshot.source_file_name,
        snapshot.data_as_of_date,
        snapshot.provenance_status,
        (
            coalesce(
                lower(btrim(relation.source_entity_id)) =
                    query_input.normalized_anchor_id,
                false
            )
            OR relation.source_normalized_name = query_input.normalized_anchor
        ) AS source_matches,
        (
            coalesce(
                lower(btrim(relation.target_entity_id)) =
                    query_input.normalized_anchor_id,
                false
            )
            OR relation.target_normalized_name = query_input.normalized_anchor
        ) AS target_matches
    FROM core.organization_relation relation
    JOIN raw.source_row source
      ON source.raw_row_id = relation.raw_row_id
    JOIN meta.dataset_snapshot snapshot
      ON snapshot.snapshot_id = relation.snapshot_id
    CROSS JOIN query_input
    WHERE query_input.normalized_anchor IS NOT NULL
      AND (
          p_predicates IS NULL
          OR relation.predicate = ANY(p_predicates)
      )
      AND EXISTS (
          SELECT 1
          FROM meta.data_version_snapshot mapping
          JOIN meta.data_version version
            ON version.data_version_id = mapping.data_version_id
          WHERE mapping.snapshot_id = relation.snapshot_id
            AND version.status = 'ACTIVE'
      )
),

eligible AS (
    SELECT
        active_relations.*,
        count(*) OVER () AS total_hits
    FROM active_relations
    WHERE CASE p_anchor_role
        WHEN 'source' THEN source_matches
        WHEN 'target' THEN target_matches
        WHEN 'either' THEN source_matches OR target_matches
        ELSE false
    END
      AND (
          p_result_role <> 'opposite'
          OR source_matches <> target_matches
      )
      AND (
          p_result_listed IS NULL
          OR CASE p_result_role
              WHEN 'source' THEN source_is_listed
              WHEN 'target' THEN target_is_listed
              WHEN 'opposite' THEN CASE
                  WHEN source_matches AND NOT target_matches
                      THEN target_is_listed
                  WHEN target_matches AND NOT source_matches
                      THEN source_is_listed
                  ELSE NULL
              END
              ELSE NULL
          END = p_result_listed
      )
)

SELECT
    eligible.relation_id,
    eligible.predicate,
    eligible.source_entity_id,
    eligible.source_entity_name,
    eligible.target_entity_id,
    eligible.target_entity_name,
    eligible.source_is_listed,
    eligible.target_is_listed,
    eligible.relation_as_of_date,
    eligible.source_ref,
    eligible.confidence,
    eligible.has_warning,
    eligible.snapshot_id,
    eligible.raw_row_id,
    eligible.source_file_name,
    eligible.source_sheet,
    eligible.source_row_number,
    eligible.data_as_of_date,
    eligible.provenance_status,
    eligible.total_hits
FROM eligible
ORDER BY
    eligible.relation_as_of_date DESC,
    eligible.relation_id
LIMIT least(greatest(coalesce(p_limit, 50), 1), 100);

$$;


--
-- PostgreSQL database dump complete
--

\unrestrict iFuAHVBScgZnn8b7e03dpUmbIlxovdPd7xQAxb110ava0LcS5ZBfmy9XadvL6qi


-- Master-status contract supplied by A for issue #46. No order/account checks.
CREATE OR REPLACE VIEW core.v_product_sale_availability AS
WITH src AS (
    SELECT p.product_id, p.product_type, p.source_product_key,
           p.snapshot_id, p.raw_row_id,
           r.dataset_code, r.source_sheet, r.source_row_number, r.source_key,
           NULLIF(btrim(r.payload ->> 'pd_sale_yn'), '') AS pd_sale_yn_raw,
           NULLIF(btrim(r.payload ->> 'pd_tr_yn'), '') AS pd_tr_yn_raw,
           NULLIF(btrim(r.payload ->> 'sale_yn'), '') AS sale_yn_raw,
           NULLIF(btrim(r.payload ->> 'thco_sale_yn'), '') AS thco_sale_yn_raw,
           NULLIF(btrim(r.payload ->> 'prvo_pbff_desc'), '') AS prvo_pbff_desc_raw,
           NULLIF(btrim(r.payload ->> 'sale_status'), '') AS payload_sale_status,
           NULLIF(btrim(f.sale_status), '') AS stored_sale_status,
           COALESCE(
               CASE WHEN p.product_type IN ('DOMESTIC_ETP', 'OVERSEAS_ETP')
                    THEN NULLIF(btrim(r.payload ->> 'du_upt_dt'), '')
                    WHEN p.product_type = 'PUBLIC_FUND_CLASS'
                    THEN NULLIF(btrim(r.payload ->> 'fd_daily_bas_dt'), '') END,
               s.data_as_of_date::text
           ) AS status_basis_date
    FROM core.product p
    JOIN raw.source_row r ON r.raw_row_id = p.raw_row_id
                         AND r.snapshot_id = p.snapshot_id
    JOIN meta.dataset_snapshot s ON s.snapshot_id = p.snapshot_id
    LEFT JOIN core.fund_class f ON f.product_id = p.product_id
), norm AS (
    SELECT src.*,
           CASE pd_sale_yn_raw WHEN '1' THEN true WHEN '1.0' THEN true
                              WHEN '0' THEN false WHEN '0.0' THEN false END AS etf_saleable,
           CASE pd_tr_yn_raw WHEN '1' THEN true WHEN '1.0' THEN true
                            WHEN '0' THEN false WHEN '0.0' THEN false END AS etf_trading_suspended,
           CASE WHEN prvo_pbff_desc_raw = '공모' THEN true
                WHEN prvo_pbff_desc_raw LIKE '%사모%' THEN false END AS fund_is_public,
           CASE sale_yn_raw WHEN '판매중' THEN true WHEN '판매완료' THEN false END AS fund_sale_open,
           CASE WHEN thco_sale_yn_raw = 'Y' THEN true END AS fund_provider_saleable,
           COALESCE((sale_yn_raw IS NOT NULL AND (
               (payload_sale_status IS NOT NULL AND payload_sale_status <> sale_yn_raw)
               OR (stored_sale_status IS NOT NULL AND stored_sale_status <> sale_yn_raw)
           )), false) AS sale_status_conflict,
           (pd_sale_yn_raw IS NOT NULL AND pd_sale_yn_raw NOT IN ('0', '0.0', '1', '1.0'))
           OR (pd_tr_yn_raw IS NOT NULL AND pd_tr_yn_raw NOT IN ('0', '0.0', '1', '1.0'))
               AS etf_unknown_code,
           CASE product_type
               WHEN 'DOMESTIC_ETP' THEN dataset_code IN ('DOMESTIC_ETP', 'PREF01N001')
               WHEN 'OVERSEAS_ETP' THEN dataset_code IN ('OVERSEAS_ETP', 'PREF02N001')
               WHEN 'PUBLIC_FUND_CLASS' THEN dataset_code IN ('PUBLIC_FUND', 'PRFD01N001')
               ELSE false
           END AS source_type_matches,
           EXISTS (
               SELECT 1 FROM meta.value_provenance vp
               WHERE vp.raw_row_id = src.raw_row_id
                 AND vp.field_name IN (
                     'pd_sale_yn', 'pd_tr_yn', 'sale_yn', 'thco_sale_yn',
                     'prvo_pbff_desc', 'sale_status', 'sale_available'
                 )
                 AND (vp.evidence_eligible IS NOT TRUE OR vp.fill_type = 'estimated')
           ) AS source_ineligible
    FROM src
), decision AS (
    SELECT norm.*,
           CASE
               WHEN NOT source_type_matches THEN 'SOURCE_TYPE_MISMATCH'
               WHEN source_ineligible THEN 'SOURCE_INELIGIBLE'
               WHEN product_type IN ('DOMESTIC_ETP', 'OVERSEAS_ETP') THEN
                   CASE WHEN etf_unknown_code THEN 'UNKNOWN_OR_MISSING'
                        WHEN etf_saleable IS FALSE THEN 'SALE_FLAG_OFF'
                        WHEN etf_trading_suspended IS TRUE THEN 'TRADING_SUSPENDED'
                        WHEN etf_saleable IS TRUE AND etf_trading_suspended IS FALSE
                            THEN 'MASTER_AVAILABLE'
                        ELSE 'UNKNOWN_OR_MISSING' END
               WHEN product_type = 'PUBLIC_FUND_CLASS' THEN
                   CASE WHEN sale_status_conflict THEN 'SALE_STATUS_CONFLICT'
                        WHEN fund_is_public IS FALSE THEN 'NOT_PUBLIC_OFFERING'
                        WHEN fund_sale_open IS FALSE THEN 'SALE_CLOSED'
                        WHEN fund_is_public IS TRUE AND fund_sale_open IS TRUE
                             AND fund_provider_saleable IS TRUE THEN 'MASTER_AVAILABLE'
                        ELSE 'UNKNOWN_OR_MISSING' END
               ELSE 'NOT_APPLICABLE'
           END AS sale_available_reason
    FROM norm
)
SELECT decision.*,
       CASE WHEN sale_available_reason = 'MASTER_AVAILABLE' THEN true
            WHEN sale_available_reason IN (
                'SALE_FLAG_OFF', 'TRADING_SUSPENDED', 'NOT_PUBLIC_OFFERING', 'SALE_CLOSED'
            ) THEN false END AS sale_available,
       'MASTER_STATUS_ONLY'::text AS sale_available_scope
FROM decision;

COMMENT ON VIEW core.v_product_sale_availability IS
'Master snapshot sale status only; account, channel and intraday order availability are not guaranteed. Unknown and conflicting data remain NULL.';
