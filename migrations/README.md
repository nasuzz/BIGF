# Existing database migrations

`init/01_schema.sql` creates a **new, empty** database and is the canonical
schema source. Files in this directory only move an **existing** database
forward. Never run the full initialization dump against an existing NCP
database.

Migration files use `VYYYYMMDD_NNN__description.sql`. Once a migration has run
in any environment it is immutable: add a higher version instead of editing it.

**Exception:** a migration that runs `CREATE INDEX CONCURRENTLY` (for example
`V20260906_003__add_ext_holding_search_indexes.sql`) cannot go through
`scripts/apply_migrations.py` at all, because the runner always wraps the
whole batch in one `BEGIN`/`COMMIT`, and `CONCURRENTLY` is rejected inside a
transaction block. Apply that file by hand with plain `psql` (no
`BEGIN`/`COMMIT`) and insert its `meta.schema_migration` row yourself; see the
comment at the top of that file for the exact command.

The runner stores the SHA-256 checksum, timestamp, and database role in
`meta.schema_migration` and refuses changed history.

```bash
# Uses host psql plus PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD.
# It deliberately does not load .env or DATABASE_URL automatically.
psql -X -v ON_ERROR_STOP=1 -c 'SELECT current_database(), current_user;'
python scripts/apply_migrations.py --dry-run
python scripts/apply_migrations.py

# Or pass a libpq connection string. Keep credentials out of shell history.
python scripts/apply_migrations.py --database "$DATABASE_URL"
```

Run this on a host or private network that can already reach PostgreSQL. Keeping
the database port unexposed publicly is compatible with the runner; on NCP, run
it from the database server and use `PGHOST=127.0.0.1`, or use the database's
certificate-valid DNS hostname from an authorized internal host. Remote runs
must set `PGSSLMODE=verify-full` and `PGSSLROOTCERT` to an approved CA bundle;
`sslmode=require` without server identity validation is not sufficient.

Before production, take a backup and run the same migration against staging.
Afterward, verify both the history and the active-version behavior:

```sql
TABLE meta.schema_migration;

SELECT count(*)
FROM search.find_products('test', NULL, 20);
```

For rollback, restore the pre-migration backup. Schema migrations are designed
for forward fixes and do not contain automatic destructive down migrations.
