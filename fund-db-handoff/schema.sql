-- Do not add schema definitions here.
-- init/01_schema.sql is the repository's canonical schema source.
-- psql resolves \ir relative to this file, so this entrypoint works from any cwd.
\set ON_ERROR_STOP on
\ir ../init/01_schema.sql
