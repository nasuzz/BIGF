#!/usr/bin/env python3
"""Apply immutable, checksummed PostgreSQL migrations with psql."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPOSITORY_ROOT / "migrations"
MIGRATION_NAME = re.compile(
    r"^V(?P<version>\d{8}_\d{3})__(?P<description>[a-z0-9_]+)\.sql$"
)
HISTORY_SETUP = """
CREATE SCHEMA IF NOT EXISTS meta;
CREATE TABLE IF NOT EXISTS meta.schema_migration (
    version text PRIMARY KEY,
    description text NOT NULL,
    checksum_sha256 text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    applied_by text NOT NULL DEFAULT current_user
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    path: Path
    sql: str
    checksum: str


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load valid migration files in version order and calculate checksums."""
    migrations: list[Migration] = []
    for path in sorted(directory.glob("V*.sql")):
        match = MIGRATION_NAME.fullmatch(path.name)
        if not match:
            raise ValueError(f"invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=match.group("version"),
                description=match.group("description").replace("_", " "),
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    versions = [migration.version for migration in migrations]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate migration version")
    return migrations


def sql_literal(value: str) -> str:
    """Return a PostgreSQL string literal for trusted runner metadata."""
    return "'" + value.replace("'", "''") + "'"


def run_psql(database: str | None, sql: str, capture: bool = False) -> str:
    """Execute one SQL batch through the host's psql client."""
    command = [
        "psql",
        "-X",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--set=ON_ERROR_STOP=1",
    ]
    if database:
        command.extend(["--dbname", database])
    result = subprocess.run(
        command,
        input=sql,
        text=True,
        check=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def build_batch(
    migrations: list[Migration],
    *,
    checksum_migrations: list[Migration] | None = None,
    dry_run: bool = False,
) -> str:
    """Build one locked transaction that rechecks state before every migration.

    The history lookup deliberately lives inside the same psql session and
    transaction as the advisory lock. A second runner therefore waits, then
    observes migrations committed by the first runner instead of replaying a
    stale pending list.
    """
    statements = [
        r"\set ON_ERROR_STOP on",
        "BEGIN;",
        "SELECT pg_advisory_xact_lock(hashtext('miraeasset_schema_migrations'));",
        HISTORY_SETUP.strip(),
    ]
    checksum_scope = migrations if checksum_migrations is None else checksum_migrations
    expected_rows = ",\n        ".join(
        f"({sql_literal(item.version)}, {sql_literal(item.checksum)})"
        for item in checksum_scope
    )
    if expected_rows:
        statements.append(
            """
DO $migration_checksum_guard$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (VALUES
        {expected_rows}
        ) AS expected(version, checksum_sha256)
        JOIN meta.schema_migration applied USING (version)
        WHERE applied.checksum_sha256 <> expected.checksum_sha256
    ) THEN
        RAISE EXCEPTION
            'checksum mismatch for applied migration; never edit migration history';
    END IF;
END
$migration_checksum_guard$;
""".strip().format(expected_rows=expected_rows)
        )
    for migration in migrations:
        statements.append(
            r"""
SELECT NOT EXISTS (
    SELECT 1
    FROM meta.schema_migration
    WHERE version = {version}
) AS migration_pending
\gset
\if :migration_pending
\echo {state} {filename}
{migration_sql}
{history_insert}
\else
\echo current {filename}
\endif
""".strip().format(
                version=sql_literal(migration.version),
                state="pending" if dry_run else "applying",
                filename=migration.path.name,
                migration_sql="" if dry_run else migration.sql.rstrip(),
                history_insert=(
                    ""
                    if dry_run
                    else (
                        "INSERT INTO meta.schema_migration "
                        "(version, description, checksum_sha256) VALUES ("
                        f"{sql_literal(migration.version)}, "
                        f"{sql_literal(migration.description)}, "
                        f"{sql_literal(migration.checksum)});"
                    )
                ),
            )
        )
    statements.append("ROLLBACK;" if dry_run else "COMMIT;")
    return "\n\n".join(statements) + "\n"


def main() -> int:
    """Parse CLI arguments and run the locked migration batch."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        help="psql connection string; omitted to use standard PG* environment variables",
    )
    parser.add_argument("--target", help="stop after this migration version")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        discovered_migrations = discover_migrations()
    except ValueError as error:
        parser.error(str(error))
    if args.target and args.target not in {
        item.version for item in discovered_migrations
    }:
        parser.error(f"unknown target version: {args.target}")
    migrations = discovered_migrations
    if args.target:
        migrations = [item for item in migrations if item.version <= args.target]

    try:
        output = run_psql(
            args.database,
            build_batch(
                migrations,
                checksum_migrations=discovered_migrations,
                dry_run=args.dry_run,
            ),
            capture=True,
        )
    except FileNotFoundError:
        print("psql is required to apply migrations", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    if output.strip():
        print(output.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
