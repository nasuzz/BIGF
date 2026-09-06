-- These indexes accelerate reverse holding lookup on ext.krx_etf_holding_20260824
-- (75,880 rows) and ext.sec_etf_holding_1y (9.35M rows).
--
-- IMPORTANT: CREATE INDEX CONCURRENTLY cannot run inside a transaction block,
-- so this file CANNOT go through scripts/apply_migrations.py (which wraps
-- every migration in one BEGIN/COMMIT batch). Apply it by hand:
--
--   psql "$DATABASE_URL" -f migrations/V20260906_003__add_ext_holding_search_indexes.sql
--
-- then manually record it, e.g.:
--
--   INSERT INTO meta.schema_migration (version, description, checksum_sha256)
--   VALUES ('20260906_003', 'add_ext_holding_search_indexes', '<sha256 of this file>');
--
-- Check free disk space immediately before and after -- these indexes added
-- roughly 500MB on NCP. Run outside a transaction (plain psql, no BEGIN/COMMIT).

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_krx_holding_name_trgm
    ON ext.krx_etf_holding_20260824
    USING gin (lower(holding_name) gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sec_holding_ticker_upper
    ON ext.sec_etf_holding_1y (upper(holding_ticker))
    WHERE holding_ticker IS NOT NULL AND btrim(holding_ticker) <> '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sec_holding_fund_key_date
    ON ext.sec_etf_holding_1y (
        (
            'ISIN:' || coalesce(upper(nullif(btrim(etf_isin), '')), '')
            || '|TICKER:' || coalesce(upper(nullif(btrim(etf_ticker), '')), '')
        ),
        report_date DESC
    );

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sec_holding_name_trgm
    ON ext.sec_etf_holding_1y
    USING gin (lower(holding_name) gin_trgm_ops);

ANALYZE ext.krx_etf_holding_20260824;
ANALYZE ext.sec_etf_holding_1y;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'ext'
  AND indexname IN (
      'ix_krx_holding_name_trgm',
      'ix_sec_holding_fund_key_date',
      'ix_sec_holding_ticker_upper',
      'ix_sec_holding_name_trgm'
  )
ORDER BY indexname;
