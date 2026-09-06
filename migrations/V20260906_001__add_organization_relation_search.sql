-- Add the provenance-bearing organization relation source used by relation_search.
-- Keep this migration immutable after it has been applied anywhere.

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

CREATE INDEX ix_organization_relation_snapshot
    ON core.organization_relation (snapshot_id);
CREATE INDEX ix_organization_relation_source_name
    ON core.organization_relation (source_normalized_name, predicate);
CREATE INDEX ix_organization_relation_target_name
    ON core.organization_relation (target_normalized_name, predicate);
CREATE INDEX ix_organization_relation_source_id
    ON core.organization_relation (lower(btrim(source_entity_id)), predicate)
    WHERE source_entity_id IS NOT NULL;
CREATE INDEX ix_organization_relation_target_id
    ON core.organization_relation (lower(btrim(target_entity_id)), predicate)
    WHERE target_entity_id IS NOT NULL;

CREATE OR REPLACE FUNCTION search.find_organization_relations(
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
