-- Issue #32: keep the exact ticker matching branch for all input shapes, but
-- make it a parameterized lookup instead of a hash join over all SEC holdings.
-- The index definition below must show the same expression and predicate used
-- by the revised ticker branch.
SELECT pg_get_indexdef('ext.ix_sec_holding_ticker_upper'::regclass)
    AS ticker_index_definition;

CREATE OR REPLACE FUNCTION ext.search_etf_holdings(
    p_holding_query text DEFAULT NULL,
    p_product_ids bigint[] DEFAULT NULL,
    p_product_types text[] DEFAULT NULL,
    p_latest_only boolean DEFAULT true,
    p_limit integer DEFAULT 100
)
RETURNS TABLE (
    product_id bigint,
    product_name text,
    product_type text,
    as_of_date date,
    holding_key text,
    holding_ticker text,
    holding_isin text,
    holding_name text,
    asset_type text,
    quantity numeric,
    market_value numeric,
    weight_pct numeric,
    source_name text,
    source_record_key text
)
LANGUAGE sql
STABLE
AS $function$
    WITH query_input AS (
        SELECT
            nullif(btrim(p_holding_query), '') AS raw_query,
            replace(
                replace(
                    replace(lower(nullif(btrim(p_holding_query), '')), '\', '\\'),
                    '%', '\%'
                ),
                '_', '\_'
            ) AS escaped_query
    ), matched_krx AS (
        SELECT m.product_id, h.*
        FROM ext.krx_etf_holding_20260824 h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'KRX'
         AND m.source_fund_key = h.etf_code
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'DOMESTIC_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR h.base_date = m.source_as_of_date)

        UNION

        SELECT m.product_id, h.*
        FROM ext.krx_etf_holding_20260824 h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'KRX'
         AND m.source_fund_key = h.etf_code
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NOT NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'DOMESTIC_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR h.base_date = m.source_as_of_date)
          AND lower(h.holding_name) LIKE '%' || q.escaped_query || '%' ESCAPE '\'

        UNION

        SELECT m.product_id, h.*
        FROM ext.krx_etf_holding_20260824 h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'KRX'
         AND m.source_fund_key = h.etf_code
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NOT NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'DOMESTIC_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR h.base_date = m.source_as_of_date)
          AND h.holding_code = upper(q.raw_query)
    ), matched_sec AS (
        SELECT m.product_id, h.*
        FROM ext.sec_etf_holding_1y h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'SEC_NPORT'
         AND m.source_fund_key =
                'ISIN:' || coalesce(upper(nullif(btrim(h.etf_isin), '')), '')
                || '|TICKER:' || coalesce(upper(nullif(btrim(h.etf_ticker), '')), '')
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'OVERSEAS_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR coalesce(h.report_date, h.report_ending_period) = m.source_as_of_date)

        UNION

        SELECT m.product_id, h.*
        FROM ext.sec_etf_holding_1y h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'SEC_NPORT'
         AND m.source_fund_key =
                'ISIN:' || coalesce(upper(nullif(btrim(h.etf_isin), '')), '')
                || '|TICKER:' || coalesce(upper(nullif(btrim(h.etf_ticker), '')), '')
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NOT NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'OVERSEAS_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR coalesce(h.report_date, h.report_ending_period) = m.source_as_of_date)
          AND lower(h.holding_name) LIKE '%' || q.escaped_query || '%' ESCAPE '\'

        UNION

        -- Do not CROSS JOIN query_input here. The direct parameter comparison
        -- lets PostgreSQL build a parameterized index scan. The two predicate
        -- clauses intentionally match ix_sec_holding_ticker_upper exactly.
        SELECT m.product_id, h.*
        FROM ext.sec_etf_holding_1y h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'SEC_NPORT'
         AND m.source_fund_key =
                'ISIN:' || coalesce(upper(nullif(btrim(h.etf_isin), '')), '')
                || '|TICKER:' || coalesce(upper(nullif(btrim(h.etf_ticker), '')), '')
         AND m.is_active
        WHERE nullif(btrim(p_holding_query), '') IS NOT NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'OVERSEAS_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR coalesce(h.report_date, h.report_ending_period) = m.source_as_of_date)
          AND h.holding_ticker IS NOT NULL
          AND btrim(h.holding_ticker) <> ''
          AND upper(h.holding_ticker) = upper(btrim(p_holding_query))

        UNION

        SELECT m.product_id, h.*
        FROM ext.sec_etf_holding_1y h
        JOIN ext.etf_product_mapping m
          ON m.source_system = 'SEC_NPORT'
         AND m.source_fund_key =
                'ISIN:' || coalesce(upper(nullif(btrim(h.etf_isin), '')), '')
                || '|TICKER:' || coalesce(upper(nullif(btrim(h.etf_ticker), '')), '')
         AND m.is_active
        CROSS JOIN query_input q
        WHERE q.raw_query IS NOT NULL
          AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
          AND (p_product_types IS NULL OR 'OVERSEAS_ETP' = ANY (p_product_types))
          AND (NOT p_latest_only OR coalesce(h.report_date, h.report_ending_period) = m.source_as_of_date)
          AND h.holding_isin = upper(q.raw_query)
    ), candidates AS (
        SELECT
            h.product_id,
            p.canonical_name AS product_name,
            'DOMESTIC_ETP'::text AS product_type,
            h.base_date AS as_of_date,
            h.holding_code::text AS holding_key,
            h.holding_code::text AS holding_ticker,
            null::text AS holding_isin,
            h.holding_name,
            h.asset_type,
            h.quantity,
            coalesce(h.evaluation_amount, h.market_cap) AS market_value,
            h.weight_pct,
            'KRX'::text AS source_name,
            concat('KRX:', h.base_date, ':', h.etf_code, ':', h.holding_code) AS source_record_key
        FROM matched_krx h
        JOIN core.product p USING (product_id)

        UNION ALL

        SELECT
            h.product_id,
            p.canonical_name AS product_name,
            'OVERSEAS_ETP'::text AS product_type,
            coalesce(h.report_date, h.report_ending_period) AS as_of_date,
            coalesce(nullif(h.holding_isin, ''), nullif(h.holding_cusip, ''), nullif(h.holding_ticker, ''), h.holding_id) AS holding_key,
            nullif(upper(h.holding_ticker), '') AS holding_ticker,
            nullif(upper(h.holding_isin), '') AS holding_isin,
            h.holding_name,
            h.asset_category AS asset_type,
            h.balance AS quantity,
            h.market_value,
            h.weight_pct,
            'SEC Form N-PORT'::text AS source_name,
            concat('SEC:', h.accession_number, ':', h.holding_id) AS source_record_key
        FROM matched_sec h
        JOIN core.product p USING (product_id)
    )
    SELECT
        c.product_id,
        c.product_name,
        c.product_type,
        c.as_of_date,
        c.holding_key,
        c.holding_ticker,
        c.holding_isin,
        c.holding_name,
        c.asset_type,
        c.quantity,
        c.market_value,
        c.weight_pct,
        c.source_name,
        c.source_record_key
    FROM candidates c
    ORDER BY c.weight_pct DESC NULLS LAST, c.product_name, c.holding_name
    LIMIT least(greatest(coalesce(p_limit, 100), 1), 500)
$function$;
