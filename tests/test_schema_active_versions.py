from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY_ROOT / "init" / "01_schema.sql"


def _function_definition(schema: str, function_name: str) -> str:
    signature = f"CREATE FUNCTION search.{function_name}("
    start = schema.index(signature)
    end = schema.index("\n$$;", start)
    return schema[start:end]


class ActiveDataVersionSearchTest(unittest.TestCase):
    def test_canonical_search_functions_only_read_active_snapshots(self):
        functions = {
            "find_organization_relations": "relation.snapshot_id",
            "find_overseas_strategies": "strategy.snapshot_id",
            "find_products": "product.snapshot_id",
        }

        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        for function_name, snapshot_reference in functions.items():
            with self.subTest(function=function_name):
                definition = _function_definition(schema, function_name)
                self.assertIn("FROM meta.data_version_snapshot mapping", definition)
                self.assertIn("JOIN meta.data_version version", definition)
                self.assertIn(
                    f"WHERE mapping.snapshot_id = {snapshot_reference}", definition
                )
                self.assertIn("AND version.status = 'ACTIVE'", definition)


if __name__ == "__main__":
    unittest.main()
