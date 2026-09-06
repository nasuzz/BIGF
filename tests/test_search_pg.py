from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "fund-db-handoff" / "search_pg.py"
SPEC = importlib.util.spec_from_file_location("search_pg", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
search_pg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search_pg)


class FakeEmbedder:
    model_name = "model-b"

    def encode_query(self, text):
        self.query = text
        return [0.1, 0.2, 0.3]


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return [(7, 12, "strategy", 0.91)]


class SearchPgTest(unittest.TestCase):
    def test_search_uses_embedder_model_for_query_and_row_filter(self):
        cursor = FakeCursor()
        embedder = FakeEmbedder()

        rows = search_pg.search(cursor, embedder, "배당 ETF", limit=3)

        self.assertEqual(embedder.query, "배당 ETF")
        self.assertEqual(rows, [(7, 12, "strategy", 0.91)])
        sql, params = cursor.calls[0]
        self.assertIn("embedding_model = %s", sql)
        self.assertEqual(
            params,
            ("[0.1,0.2,0.3]", "model-b", "[0.1,0.2,0.3]", 3),
        )


if __name__ == "__main__":
    unittest.main()
