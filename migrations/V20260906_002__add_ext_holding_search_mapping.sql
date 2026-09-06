-- Stable API contract over the verified, date-stamped ext source tables.
-- This migration does not modify core/raw/meta rows and does not duplicate SEC holdings.
--
-- Runs inside scripts/apply_migrations.py's own BEGIN/advisory-lock/COMMIT
-- batch, so this file intentionally has no BEGIN/COMMIT or advisory lock of
-- its own (nesting them would commit the runner's outer transaction early).
-- Applied on NCP by hand on 2026-09-06 before this migration file existed;
-- recorded here per issue #34 so GitHub main matches the deployed database.

DO $preflight$
DECLARE
    missing_objects text[];
BEGIN
    SELECT array_agg(required_name ORDER BY required_name)
      INTO missing_objects
    FROM (
        VALUES
            ('core.product'),
            ('ext.krx_etf_holding_20260824'),
            ('ext.sec_etf_holding_1y'),
            ('ext.opendart_company_20260824'),
            ('ext.opendart_disclosure_20260824'),
            ('ext.krx_etf_product_map'),
            ('ext.sec_etf_product_map')
    ) AS required(required_name)
    WHERE to_regclass(required_name) IS NULL;

    IF missing_objects IS NOT NULL THEN
        RAISE EXCEPTION 'Required objects are missing: %', missing_objects;
    END IF;

    IF EXISTS (SELECT 1 FROM ext.krx_etf_product_map WHERE product_id IS NULL)
       OR EXISTS (SELECT 1 FROM ext.sec_etf_product_map WHERE product_id IS NULL) THEN
        RAISE EXCEPTION 'Verified ETF mappings contain an unresolved product_id.';
    END IF;
END
$preflight$;

CREATE OR REPLACE FUNCTION ext.normalize_organization_name(p_value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURNS NULL ON NULL INPUT
AS $function$
    SELECT nullif(
        regexp_replace(
            upper(btrim(p_value)),
            '(주식회사|\(주\)|㈜|자산운용|운용|[^0-9A-Z가-힣])',
            '',
            'g'
        ),
        ''
    )
$function$;

CREATE TABLE IF NOT EXISTS ext.external_mapping_refresh_log (
    refresh_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    refresh_version text NOT NULL,
    refreshed_at timestamptz NOT NULL DEFAULT now(),
    etf_mapping_count integer NOT NULL,
    product_dart_mapping_count integer NOT NULL,
    company_count integer NOT NULL,
    disclosure_count integer NOT NULL,
    notes text
);

CREATE TABLE IF NOT EXISTS ext.etf_product_mapping (
    source_system text NOT NULL,
    source_fund_key text NOT NULL,
    source_fund_isin text,
    source_fund_ticker text,
    product_id bigint NOT NULL REFERENCES core.product(product_id),
    match_method text NOT NULL,
    match_confidence numeric(5,4) NOT NULL,
    source_table text NOT NULL,
    source_as_of_date date,
    is_active boolean NOT NULL DEFAULT true,
    first_mapped_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source_system, source_fund_key),
    CHECK (source_system IN ('KRX', 'SEC_NPORT')),
    CHECK (match_method IN ('TICKER_EXACT', 'ISIN_EXACT', 'TICKER_FALLBACK')),
    CHECK (match_confidence BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS ix_etf_product_mapping_product
    ON ext.etf_product_mapping(product_id)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS ix_etf_product_mapping_ticker
    ON ext.etf_product_mapping(upper(source_fund_ticker))
    WHERE is_active AND source_fund_ticker IS NOT NULL;

WITH krx_source AS (
    SELECT
        'KRX'::text AS source_system,
        m.etf_code::text AS source_fund_key,
        nullif(m.isin, '')::text AS source_fund_isin,
        m.etf_code::text AS source_fund_ticker,
        m.product_id,
        'TICKER_EXACT'::text AS match_method,
        1.0000::numeric AS match_confidence,
        'ext.krx_etf_holding_20260824'::text AS source_table,
        max(h.base_date) AS source_as_of_date
    FROM ext.krx_etf_product_map m
    JOIN ext.krx_etf_holding_20260824 h USING (etf_code)
    WHERE m.product_id IS NOT NULL
    GROUP BY m.etf_code, m.isin, m.product_id
)
INSERT INTO ext.etf_product_mapping (
    source_system,
    source_fund_key,
    source_fund_isin,
    source_fund_ticker,
    product_id,
    match_method,
    match_confidence,
    source_table,
    source_as_of_date
)
SELECT * FROM krx_source
ON CONFLICT (source_system, source_fund_key) DO UPDATE
SET source_fund_isin = EXCLUDED.source_fund_isin,
    source_fund_ticker = EXCLUDED.source_fund_ticker,
    product_id = EXCLUDED.product_id,
    match_method = EXCLUDED.match_method,
    match_confidence = EXCLUDED.match_confidence,
    source_table = EXCLUDED.source_table,
    source_as_of_date = EXCLUDED.source_as_of_date,
    is_active = true,
    updated_at = now();

WITH sec_ranked AS (
    SELECT
        'SEC_NPORT'::text AS source_system,
        'ISIN:' || coalesce(upper(nullif(btrim(m.etf_isin), '')), '')
        || '|TICKER:' || coalesce(upper(nullif(btrim(m.etf_ticker), '')), '')
        AS source_fund_key,
        nullif(upper(btrim(m.etf_isin)), '') AS source_fund_isin,
        nullif(upper(btrim(m.etf_ticker)), '') AS source_fund_ticker,
        m.product_id,
        CASE
            WHEN nullif(btrim(m.etf_isin), '') IS NOT NULL THEN 'ISIN_EXACT'
            ELSE 'TICKER_FALLBACK'
        END::text AS match_method,
        CASE
            WHEN nullif(btrim(m.etf_isin), '') IS NOT NULL THEN 1.0000
            ELSE 0.9500
        END::numeric AS match_confidence,
        'ext.sec_etf_holding_1y'::text AS source_table,
        CASE
            WHEN nullif(btrim(m.etf_isin), '') IS NOT NULL THEN (
                SELECT max(h.report_date)
                FROM ext.sec_etf_holding_1y h
                WHERE h.etf_isin = m.etf_isin
                  AND h.etf_ticker IS NOT DISTINCT FROM m.etf_ticker
            )
            ELSE (
                SELECT max(h.report_date)
                FROM ext.sec_etf_holding_1y h
                WHERE h.etf_ticker = m.etf_ticker
            )
        END AS source_as_of_date,
        row_number() OVER (
            PARTITION BY
                'ISIN:' || coalesce(upper(nullif(btrim(m.etf_isin), '')), '')
                || '|TICKER:' || coalesce(upper(nullif(btrim(m.etf_ticker), '')), '')
            ORDER BY m.snapshot_id DESC, m.product_id DESC
        ) AS pick_rank
    FROM ext.sec_etf_product_map m
    WHERE m.product_id IS NOT NULL
), sec_source AS (
    SELECT
        source_system,
        source_fund_key,
        source_fund_isin,
        source_fund_ticker,
        product_id,
        match_method,
        match_confidence,
        source_table,
        source_as_of_date
    FROM sec_ranked
    WHERE pick_rank = 1
)
INSERT INTO ext.etf_product_mapping (
    source_system,
    source_fund_key,
    source_fund_isin,
    source_fund_ticker,
    product_id,
    match_method,
    match_confidence,
    source_table,
    source_as_of_date
)
SELECT * FROM sec_source
ON CONFLICT (source_system, source_fund_key) DO UPDATE
SET source_fund_isin = EXCLUDED.source_fund_isin,
    source_fund_ticker = EXCLUDED.source_fund_ticker,
    product_id = EXCLUDED.product_id,
    match_method = EXCLUDED.match_method,
    match_confidence = EXCLUDED.match_confidence,
    source_table = EXCLUDED.source_table,
    source_as_of_date = EXCLUDED.source_as_of_date,
    is_active = true,
    updated_at = now();

CREATE TABLE IF NOT EXISTS ext.opendart_company (
    corp_code varchar(8) PRIMARY KEY,
    requested_name text NOT NULL,
    corp_name text NOT NULL,
    corp_name_eng text,
    stock_name text,
    stock_code varchar(6),
    ceo_nm text,
    corp_cls varchar(1),
    jurir_no varchar(13),
    bizr_no varchar(10),
    adres text,
    hm_url text,
    ir_url text,
    phn_no text,
    fax_no text,
    induty_code text,
    est_dt date,
    acc_mt integer,
    source_batch text NOT NULL,
    source_collected_at timestamptz,
    first_loaded_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO ext.opendart_company (
    corp_code,
    requested_name,
    corp_name,
    corp_name_eng,
    stock_name,
    stock_code,
    ceo_nm,
    corp_cls,
    jurir_no,
    bizr_no,
    adres,
    hm_url,
    ir_url,
    phn_no,
    fax_no,
    induty_code,
    est_dt,
    acc_mt,
    source_batch,
    source_collected_at
)
SELECT
    corp_code,
    requested_name,
    corp_name,
    corp_name_eng,
    stock_name,
    stock_code,
    ceo_nm,
    corp_cls,
    jurir_no,
    bizr_no,
    adres,
    hm_url,
    ir_url,
    phn_no,
    fax_no,
    induty_code,
    est_dt,
    acc_mt,
    'OpenDART_DS001_20260824',
    collected_at_utc
FROM ext.opendart_company_20260824
ON CONFLICT (corp_code) DO UPDATE
SET requested_name = EXCLUDED.requested_name,
    corp_name = EXCLUDED.corp_name,
    corp_name_eng = EXCLUDED.corp_name_eng,
    stock_name = EXCLUDED.stock_name,
    stock_code = EXCLUDED.stock_code,
    ceo_nm = EXCLUDED.ceo_nm,
    corp_cls = EXCLUDED.corp_cls,
    jurir_no = EXCLUDED.jurir_no,
    bizr_no = EXCLUDED.bizr_no,
    adres = EXCLUDED.adres,
    hm_url = EXCLUDED.hm_url,
    ir_url = EXCLUDED.ir_url,
    phn_no = EXCLUDED.phn_no,
    fax_no = EXCLUDED.fax_no,
    induty_code = EXCLUDED.induty_code,
    est_dt = EXCLUDED.est_dt,
    acc_mt = EXCLUDED.acc_mt,
    source_batch = EXCLUDED.source_batch,
    source_collected_at = EXCLUDED.source_collected_at,
    updated_at = now();

CREATE TABLE IF NOT EXISTS ext.opendart_disclosure (
    rcept_no varchar(14) PRIMARY KEY,
    corp_code varchar(8) NOT NULL REFERENCES ext.opendart_company(corp_code),
    corp_name text NOT NULL,
    stock_code varchar(6),
    corp_cls varchar(1),
    report_nm text NOT NULL,
    flr_nm text,
    rcept_dt date NOT NULL,
    rm text,
    requested_name text NOT NULL,
    query_disclosure_type varchar(1) NOT NULL,
    source_url text NOT NULL,
    source_batch text NOT NULL,
    source_collected_at timestamptz,
    first_loaded_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO ext.opendart_disclosure (
    rcept_no,
    corp_code,
    corp_name,
    stock_code,
    corp_cls,
    report_nm,
    flr_nm,
    rcept_dt,
    rm,
    requested_name,
    query_disclosure_type,
    source_url,
    source_batch,
    source_collected_at
)
SELECT
    rcept_no,
    corp_code,
    corp_name,
    stock_code,
    corp_cls,
    report_nm,
    flr_nm,
    rcept_dt,
    rm,
    requested_name,
    query_disclosure_type,
    source_url,
    'OpenDART_DS001_20260824',
    collected_at_utc
FROM ext.opendart_disclosure_20260824
ON CONFLICT (rcept_no) DO UPDATE
SET corp_code = EXCLUDED.corp_code,
    corp_name = EXCLUDED.corp_name,
    stock_code = EXCLUDED.stock_code,
    corp_cls = EXCLUDED.corp_cls,
    report_nm = EXCLUDED.report_nm,
    flr_nm = EXCLUDED.flr_nm,
    rcept_dt = EXCLUDED.rcept_dt,
    rm = EXCLUDED.rm,
    requested_name = EXCLUDED.requested_name,
    query_disclosure_type = EXCLUDED.query_disclosure_type,
    source_url = EXCLUDED.source_url,
    source_batch = EXCLUDED.source_batch,
    source_collected_at = EXCLUDED.source_collected_at,
    last_seen_at = now();

CREATE INDEX IF NOT EXISTS ix_opendart_company_stock_code
    ON ext.opendart_company(stock_code)
    WHERE stock_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_opendart_company_requested_name_trgm
    ON ext.opendart_company USING gin (lower(requested_name) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_opendart_disclosure_corp_date
    ON ext.opendart_disclosure(corp_code, rcept_dt DESC);

CREATE INDEX IF NOT EXISTS ix_opendart_disclosure_date
    ON ext.opendart_disclosure(rcept_dt DESC);

CREATE INDEX IF NOT EXISTS ix_opendart_disclosure_report_name_trgm
    ON ext.opendart_disclosure USING gin (lower(report_nm) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS ext.product_opendart_company_mapping (
    product_id bigint PRIMARY KEY REFERENCES core.product(product_id),
    corp_code varchar(8) NOT NULL REFERENCES ext.opendart_company(corp_code),
    manager_name text NOT NULL,
    normalized_manager_name text NOT NULL,
    requested_name text NOT NULL,
    corp_name text NOT NULL,
    match_method text NOT NULL,
    match_confidence numeric(5,4) NOT NULL,
    mapping_status text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    first_mapped_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (match_method IN ('NORMALIZED_MANAGER_EXACT', 'MANUAL_VERIFIED')),
    CHECK (match_confidence BETWEEN 0 AND 1),
    CHECK (mapping_status IN ('AUTO_APPROVED', 'MANUAL_APPROVED', 'REVIEW_REQUIRED'))
);

CREATE INDEX IF NOT EXISTS ix_product_opendart_company_corp
    ON ext.product_opendart_company_mapping(corp_code)
    WHERE is_active;

DO $ambiguity$
DECLARE
    ambiguous_count integer;
BEGIN
    SELECT count(*)
      INTO ambiguous_count
    FROM (
        SELECT p.product_id
        FROM core.product p
        JOIN ext.opendart_company c
          ON ext.normalize_organization_name(p.manager_name)
           = ext.normalize_organization_name(c.requested_name)
        WHERE p.manager_name IS NOT NULL
        GROUP BY p.product_id
        HAVING count(DISTINCT c.corp_code) > 1
    ) AS ambiguous;

    IF ambiguous_count > 0 THEN
        RAISE EXCEPTION 'Ambiguous product-to-OpenDART company mappings: %', ambiguous_count;
    END IF;
END
$ambiguity$;

INSERT INTO ext.product_opendart_company_mapping (
    product_id,
    corp_code,
    manager_name,
    normalized_manager_name,
    requested_name,
    corp_name,
    match_method,
    match_confidence,
    mapping_status
)
SELECT
    p.product_id,
    c.corp_code,
    p.manager_name,
    ext.normalize_organization_name(p.manager_name),
    c.requested_name,
    c.corp_name,
    'NORMALIZED_MANAGER_EXACT',
    0.9500,
    'AUTO_APPROVED'
FROM core.product p
JOIN ext.opendart_company c
  ON ext.normalize_organization_name(p.manager_name)
   = ext.normalize_organization_name(c.requested_name)
WHERE p.manager_name IS NOT NULL
ON CONFLICT (product_id) DO UPDATE
SET corp_code = EXCLUDED.corp_code,
    manager_name = EXCLUDED.manager_name,
    normalized_manager_name = EXCLUDED.normalized_manager_name,
    requested_name = EXCLUDED.requested_name,
    corp_name = EXCLUDED.corp_name,
    match_method = EXCLUDED.match_method,
    match_confidence = EXCLUDED.match_confidence,
    mapping_status = EXCLUDED.mapping_status,
    is_active = true,
    updated_at = now();

CREATE OR REPLACE FUNCTION ext.refresh_product_opendart_company_mapping(
    p_refresh_version text DEFAULT 'opendart_mapping_refresh'
)
RETURNS jsonb
LANGUAGE plpgsql
AS $function$
DECLARE
    ambiguous_count integer;
    affected_rows integer;
    active_rows integer;
BEGIN
    SELECT count(*)
      INTO ambiguous_count
    FROM (
        SELECT p.product_id
        FROM core.product p
        JOIN ext.opendart_company c
          ON ext.normalize_organization_name(p.manager_name)
           = ext.normalize_organization_name(c.requested_name)
        WHERE p.manager_name IS NOT NULL
        GROUP BY p.product_id
        HAVING count(DISTINCT c.corp_code) > 1
    ) AS ambiguous;

    IF ambiguous_count > 0 THEN
        RAISE EXCEPTION 'Ambiguous product-to-OpenDART company mappings: %', ambiguous_count;
    END IF;

    INSERT INTO ext.product_opendart_company_mapping (
        product_id,
        corp_code,
        manager_name,
        normalized_manager_name,
        requested_name,
        corp_name,
        match_method,
        match_confidence,
        mapping_status
    )
    SELECT
        p.product_id,
        c.corp_code,
        p.manager_name,
        ext.normalize_organization_name(p.manager_name),
        c.requested_name,
        c.corp_name,
        'NORMALIZED_MANAGER_EXACT',
        0.9500,
        'AUTO_APPROVED'
    FROM core.product p
    JOIN ext.opendart_company c
      ON ext.normalize_organization_name(p.manager_name)
       = ext.normalize_organization_name(c.requested_name)
    WHERE p.manager_name IS NOT NULL
    ON CONFLICT (product_id) DO UPDATE
    SET corp_code = EXCLUDED.corp_code,
        manager_name = EXCLUDED.manager_name,
        normalized_manager_name = EXCLUDED.normalized_manager_name,
        requested_name = EXCLUDED.requested_name,
        corp_name = EXCLUDED.corp_name,
        match_method = EXCLUDED.match_method,
        match_confidence = EXCLUDED.match_confidence,
        mapping_status = EXCLUDED.mapping_status,
        is_active = true,
        updated_at = now()
    WHERE ext.product_opendart_company_mapping.match_method <> 'MANUAL_VERIFIED';

    GET DIAGNOSTICS affected_rows = ROW_COUNT;

    SELECT count(*)
      INTO active_rows
    FROM ext.product_opendart_company_mapping
    WHERE is_active;

    INSERT INTO ext.external_mapping_refresh_log (
        refresh_version,
        etf_mapping_count,
        product_dart_mapping_count,
        company_count,
        disclosure_count,
        notes
    )
    SELECT
        coalesce(nullif(btrim(p_refresh_version), ''), 'opendart_mapping_refresh'),
        (SELECT count(*) FROM ext.etf_product_mapping WHERE is_active),
        active_rows,
        (SELECT count(*) FROM ext.opendart_company),
        (SELECT count(*) FROM ext.opendart_disclosure),
        format('OpenDART mapping refresh; affected_rows=%s', affected_rows);

    RETURN jsonb_build_object(
        'status', 'ok',
        'affected_rows', affected_rows,
        'active_mapping_rows', active_rows,
        'ambiguous_rows', ambiguous_count
    );
END
$function$;

CREATE OR REPLACE VIEW ext.api_etf_holding AS
SELECT
    m.product_id,
    'DOMESTIC_ETP'::text AS product_type,
    h.base_date AS as_of_date,
    h.base_date = m.source_as_of_date AS is_latest,
    h.holding_code::text AS holding_key,
    h.holding_code::text AS holding_ticker,
    null::text AS holding_isin,
    h.holding_name,
    h.asset_type,
    h.quantity,
    coalesce(h.evaluation_amount, h.market_cap) AS market_value,
    h.weight_pct,
    'KRX'::text AS source_name,
    m.source_fund_key,
    concat('KRX:', h.base_date, ':', h.etf_code, ':', h.holding_code) AS source_record_key
FROM ext.krx_etf_holding_20260824 h
JOIN ext.etf_product_mapping m
  ON m.source_system = 'KRX'
 AND m.source_fund_key = h.etf_code
 AND m.is_active
UNION ALL
SELECT
    m.product_id,
    'OVERSEAS_ETP'::text AS product_type,
    coalesce(h.report_date, h.report_ending_period) AS as_of_date,
    coalesce(h.report_date, h.report_ending_period) = m.source_as_of_date AS is_latest,
    coalesce(
        nullif(h.holding_isin, ''),
        nullif(h.holding_cusip, ''),
        nullif(h.holding_ticker, ''),
        h.holding_id
    ) AS holding_key,
    nullif(upper(h.holding_ticker), '') AS holding_ticker,
    nullif(upper(h.holding_isin), '') AS holding_isin,
    h.holding_name,
    h.asset_category AS asset_type,
    h.balance AS quantity,
    h.market_value,
    h.weight_pct,
    'SEC Form N-PORT'::text AS source_name,
    m.source_fund_key,
    concat('SEC:', h.accession_number, ':', h.holding_id) AS source_record_key
FROM ext.sec_etf_holding_1y h
JOIN ext.etf_product_mapping m
  ON m.source_system = 'SEC_NPORT'
 AND m.source_fund_key =
        'ISIN:' || coalesce(upper(nullif(btrim(h.etf_isin), '')), '')
        || '|TICKER:' || coalesce(upper(nullif(btrim(h.etf_ticker), '')), '')
 AND m.is_active;

CREATE OR REPLACE VIEW ext.api_product_opendart_disclosure AS
SELECT
    m.product_id,
    m.manager_name,
    m.corp_code,
    m.corp_name,
    d.rcept_no,
    d.report_nm,
    d.flr_nm,
    d.rcept_dt,
    d.query_disclosure_type,
    d.source_url,
    d.source_batch
FROM ext.product_opendart_company_mapping m
JOIN ext.opendart_disclosure d USING (corp_code)
WHERE m.is_active;

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
          AND upper(h.holding_ticker) = upper(q.raw_query)

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

CREATE OR REPLACE FUNCTION ext.search_opendart_events(
    p_company_query text DEFAULT NULL,
    p_event_query text DEFAULT NULL,
    p_product_ids bigint[] DEFAULT NULL,
    p_start_date date DEFAULT NULL,
    p_end_date date DEFAULT NULL,
    p_limit integer DEFAULT 50
)
RETURNS TABLE (
    product_id bigint,
    product_name text,
    manager_name text,
    corp_code text,
    corp_name text,
    rcept_no text,
    report_nm text,
    filer_name text,
    rcept_dt date,
    disclosure_type text,
    source_url text,
    source_batch text
)
LANGUAGE sql
STABLE
AS $function$
    WITH query_input AS (
        SELECT
            replace(replace(replace(lower(nullif(btrim(p_company_query), '')), '\', '\\'), '%', '\%'), '_', '\_') AS company_query,
            replace(replace(replace(lower(nullif(btrim(p_event_query), '')), '\', '\\'), '%', '\%'), '_', '\_') AS event_query
    )
    SELECT
        m.product_id,
        p.canonical_name AS product_name,
        m.manager_name,
        m.corp_code::text,
        m.corp_name,
        d.rcept_no::text,
        d.report_nm,
        d.flr_nm,
        d.rcept_dt,
        d.query_disclosure_type::text,
        d.source_url,
        d.source_batch
    FROM ext.product_opendart_company_mapping m
    JOIN core.product p USING (product_id)
    JOIN ext.opendart_disclosure d USING (corp_code)
    CROSS JOIN query_input q
    WHERE m.is_active
      AND (p_product_ids IS NULL OR m.product_id = ANY (p_product_ids))
      AND (p_start_date IS NULL OR d.rcept_dt >= p_start_date)
      AND (p_end_date IS NULL OR d.rcept_dt <= p_end_date)
      AND (
          q.company_query IS NULL
          OR lower(m.manager_name) LIKE '%' || q.company_query || '%' ESCAPE '\'
          OR lower(m.requested_name) LIKE '%' || q.company_query || '%' ESCAPE '\'
          OR lower(m.corp_name) LIKE '%' || q.company_query || '%' ESCAPE '\'
      )
      AND (
          q.event_query IS NULL
          OR lower(d.report_nm) LIKE '%' || q.event_query || '%' ESCAPE '\'
      )
    ORDER BY d.rcept_dt DESC, d.rcept_no DESC, m.product_id
    LIMIT least(greatest(coalesce(p_limit, 50), 1), 500)
$function$;

INSERT INTO ext.external_mapping_refresh_log (
    refresh_version,
    etf_mapping_count,
    product_dart_mapping_count,
    company_count,
    disclosure_count,
    notes
)
SELECT
    'ext_api_contract_v1',
    (SELECT count(*) FROM ext.etf_product_mapping WHERE is_active),
    (SELECT count(*) FROM ext.product_opendart_company_mapping WHERE is_active),
    (SELECT count(*) FROM ext.opendart_company),
    (SELECT count(*) FROM ext.opendart_disclosure),
    'Initial load from the independently verified 2026-08-24 ext dataset';

DO $postcheck$
DECLARE
    expected_etf integer;
    actual_etf integer;
    expected_dart_products integer;
    actual_dart_products integer;
BEGIN
    SELECT
        (SELECT count(*) FROM ext.krx_etf_product_map WHERE product_id IS NOT NULL)
        + (SELECT count(*) FROM ext.sec_etf_product_map WHERE product_id IS NOT NULL)
      INTO expected_etf;

    SELECT count(*)
      INTO actual_etf
    FROM ext.etf_product_mapping
    WHERE is_active;

    expected_dart_products := 1228;

    SELECT count(*)
      INTO actual_dart_products
    FROM ext.product_opendart_company_mapping
    WHERE is_active;

    IF actual_etf <> expected_etf THEN
        RAISE EXCEPTION 'ETF mapping count mismatch: expected %, actual %', expected_etf, actual_etf;
    END IF;

    IF actual_dart_products <> expected_dart_products THEN
        RAISE EXCEPTION 'OpenDART product mapping count mismatch: expected %, actual %', expected_dart_products, actual_dart_products;
    END IF;

    IF (SELECT count(*) FROM ext.opendart_company) <> 64
       OR (SELECT count(*) FROM ext.opendart_disclosure) <> 92900 THEN
        RAISE EXCEPTION 'Initial OpenDART canonical row counts do not match the verified source.';
    END IF;
END
$postcheck$;

SELECT source_system,
       count(*) AS mapping_rows,
       count(DISTINCT product_id) AS distinct_products,
       min(source_as_of_date) AS min_latest_date,
       max(source_as_of_date) AS max_latest_date
FROM ext.etf_product_mapping
WHERE is_active
GROUP BY source_system
ORDER BY source_system;

SELECT
    (SELECT count(*) FROM ext.product_opendart_company_mapping WHERE is_active) AS product_dart_mappings,
    (SELECT count(DISTINCT corp_code) FROM ext.product_opendart_company_mapping WHERE is_active) AS mapped_dart_companies,
    (SELECT count(*) FROM ext.opendart_company) AS canonical_companies,
    (SELECT count(*) FROM ext.opendart_disclosure) AS canonical_disclosures;
