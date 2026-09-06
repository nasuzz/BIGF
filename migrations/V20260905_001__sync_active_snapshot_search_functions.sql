-- Existing-database migration generated from init/01_schema.sql.
-- Keep this migration immutable after it has been applied anywhere.


--
-- Name: find_overseas_strategies(text, integer); Type: FUNCTION; Schema: search; Owner: -
--

CREATE OR REPLACE FUNCTION search.find_overseas_strategies(p_query text, p_limit integer DEFAULT 10) RETURNS TABLE(document_id bigint, product_id bigint, source_product_key text, canonical_name text, ticker text, base_index text, expense_ratio_pct numeric, aum numeric, strategy_text text, evidence_excerpt text, match_type text, keyword_score numeric, similarity_score numeric, combined_score numeric, has_warning boolean, snapshot_id bigint, source_file_name text, source_sheet text, source_row_number integer, data_as_of_date date, provenance_status text)
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

CREATE OR REPLACE FUNCTION search.find_products(p_query text, p_product_type text DEFAULT NULL::text, p_limit integer DEFAULT 20) RETURNS TABLE(product_id bigint, product_type text, source_product_key text, canonical_name text, short_name text, ticker text, isin text, currency_code text, manager_name text, matched_alias_type text, matched_alias_value text, match_type text, match_score numeric, has_warning boolean, snapshot_id bigint, source_file_name text, source_sheet text, source_row_number integer, data_as_of_date date, provenance_status text)
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


