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
