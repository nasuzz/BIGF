from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import apply_migrations, schema_artifacts


class SchemaSourceOfTruthTest(unittest.TestCase):
    def test_handoff_entrypoint_and_migrations_do_not_drift(self):
        self.assertEqual([], schema_artifacts.validate())

    def test_plain_render_is_the_canonical_schema(self):
        canonical = schema_artifacts.CANONICAL_SCHEMA.read_text(encoding="utf-8")
        self.assertEqual(canonical, schema_artifacts.render(owner=None))
        self.assertIn("CREATE TABLE meta.schema_migration", canonical)

    def test_owner_is_only_added_to_a_generated_artifact(self):
        canonical = schema_artifacts.CANONICAL_SCHEMA.read_text(encoding="utf-8")
        generated = schema_artifacts.render(owner="ncp_app")
        self.assertEqual(canonical, schema_artifacts.CANONICAL_SCHEMA.read_text(encoding="utf-8"))
        self.assertIn("\\set schema_owner 'ncp_app'", generated)
        self.assertIn("ALTER FUNCTION %I.%I(%s) OWNER TO %I", generated)

    def test_invalid_owner_is_rejected(self):
        with self.assertRaises(ValueError):
            schema_artifacts.render(owner="owner; DROP DATABASE postgres")

    def test_migrations_are_ordered_and_checksummed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "V20260905_002__second.sql").write_text("SELECT 2;\n")
            (root / "V20260905_001__first.sql").write_text("SELECT 1;\n")
            migrations = apply_migrations.discover_migrations(root)

        self.assertEqual(
            ["20260905_001", "20260905_002"],
            [migration.version for migration in migrations],
        )
        self.assertEqual(64, len(migrations[0].checksum))
        batch = apply_migrations.build_batch(migrations)
        self.assertIn("pg_advisory_xact_lock", batch)
        self.assertIn("CREATE TABLE IF NOT EXISTS meta.schema_migration", batch)
        self.assertIn("INSERT INTO meta.schema_migration", batch)
        self.assertIn("migration_checksum_guard", batch)
        self.assertIn(r"\if :migration_pending", batch)
        self.assertLess(
            batch.index("pg_advisory_xact_lock"),
            batch.index("WHERE version = '20260905_001'"),
        )
        self.assertLess(
            batch.index("WHERE version = '20260905_001'"),
            batch.index("SELECT 1;"),
        )
        self.assertTrue(batch.rstrip().endswith("COMMIT;"))

    def test_dry_run_uses_the_same_lock_but_does_not_execute_migration_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "V20260905_001__first.sql"
            path.write_text("SELECT 'migration body';\n")
            migration = apply_migrations.discover_migrations(Path(directory))[0]

        batch = apply_migrations.build_batch([migration], dry_run=True)

        self.assertIn("pg_advisory_xact_lock", batch)
        self.assertIn(r"\echo pending V20260905_001__first.sql", batch)
        self.assertNotIn("SELECT 'migration body';", batch)
        self.assertTrue(batch.rstrip().endswith("ROLLBACK;"))

    def test_main_rechecks_and_applies_in_one_psql_session(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "V20260905_001__first.sql"
            path.write_text("SELECT 1;\n")
            migration = apply_migrations.discover_migrations(Path(directory))[0]

        with (
            patch.object(sys, "argv", ["apply_migrations.py"]),
            patch.object(
                apply_migrations, "discover_migrations", return_value=[migration]
            ),
            patch.object(apply_migrations, "run_psql", return_value="") as run,
        ):
            result = apply_migrations.main()

        self.assertEqual(0, result)
        run.assert_called_once()
        sql = run.call_args.args[1]
        self.assertLess(
            sql.index("pg_advisory_xact_lock"),
            sql.index("WHERE version = '20260905_001'"),
        )
        self.assertTrue(run.call_args.kwargs["capture"])

    def test_target_still_validates_checksums_for_all_discovered_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_path = root / "V20260905_001__first.sql"
            second_path = root / "V20260905_002__second.sql"
            first_path.write_text("SELECT 'first migration';\n")
            second_path.write_text("SELECT 'second migration';\n")
            first, second = apply_migrations.discover_migrations(root)

        batch = apply_migrations.build_batch(
            [first], checksum_migrations=[first, second]
        )

        self.assertIn(second.checksum, batch)
        self.assertNotIn("SELECT 'second migration';", batch)
        self.assertNotIn(second.path.name, batch)


if __name__ == "__main__":
    unittest.main()
