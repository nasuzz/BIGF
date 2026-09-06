-- 2번 담당자 인계 전/후 DB 상태 확인용 SQL

SELECT current_database() AS database_name, now() AS checked_at;

SELECT dataset_code, count(*) AS raw_rows
FROM raw.source_row
GROUP BY dataset_code
ORDER BY dataset_code;

SELECT product_type, count(*) AS product_rows
FROM core.product
GROUP BY product_type
ORDER BY product_type;

SELECT 'core.domestic_etp' AS table_name, count(*) AS row_count FROM core.domestic_etp
UNION ALL
SELECT 'core.overseas_etp', count(*) FROM core.overseas_etp
UNION ALL
SELECT 'core.fund_class', count(*) FROM core.fund_class
UNION ALL
SELECT 'core.domestic_bond_quote', count(*) FROM core.domestic_bond_quote
ORDER BY table_name;

SELECT
    count(*) AS strategy_documents,
    count(embedding) AS embedded_documents,
    count(*) FILTER (WHERE embedding IS NULL) AS missing_embeddings,
    count(DISTINCT embedding_model) FILTER (WHERE embedding IS NOT NULL) AS embedding_model_count,
    min(embedding_model) FILTER (WHERE embedding IS NOT NULL) AS embedding_model
FROM search.overseas_etf_strategy;

SELECT
    coalesce(embedding_model, '<missing>') AS embedding_model,
    count(*) AS strategy_documents
FROM search.overseas_etf_strategy
GROUP BY embedding_model
ORDER BY embedding_model NULLS FIRST;

SELECT version_name, status
FROM meta.data_version
ORDER BY data_version_id;

SELECT contract_version, status
FROM meta.retrieval_contract
ORDER BY created_at;

SELECT extname, extversion
FROM pg_extension
WHERE extname IN ('vector', 'pg_trgm')
ORDER BY extname;

SELECT routine_name
FROM information_schema.routines
WHERE routine_schema = 'search'
ORDER BY routine_name;
