from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from b_agent.embeddings import EmbeddingSettings


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "fund-db-handoff" / "embed_overseas.py"
)
SPEC = importlib.util.spec_from_file_location("embed_overseas", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
embed_overseas = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(embed_overseas)


class FakeModel:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def encode(self, texts, batch_size, normalize_embeddings):
        self.calls.append((texts, batch_size, normalize_embeddings))
        return self.vectors


class FakeCursor:
    def __init__(self, rows, status):
        self.rows = rows
        self.status = status
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.status

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class EmbedOverseasTest(unittest.TestCase):
    def setUp(self):
        self.settings = EmbeddingSettings("model-b", 3, 256)

    def test_model_change_selects_non_null_embeddings_for_regeneration(self):
        cursor = FakeCursor([], (0, 0, 0, 0))

        embed_overseas.fetch_embedding_targets(cursor, "model-b")

        sql, params = cursor.calls[0]
        self.assertIn("embedding IS NULL", sql)
        self.assertIn("embedding_model IS DISTINCT FROM %s", sql)
        self.assertEqual(params, ("model-b",))

    def test_changed_model_rows_are_reembedded_and_verified(self):
        cursor = FakeCursor(
            rows=[(11, "first"), (12, "second")],
            status=(2, 0, 2, 0),
        )
        connection = FakeConnection(cursor)
        model = FakeModel([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

        status = embed_overseas.run(
            connection,
            self.settings,
            batch_size=4,
            model_factory=lambda settings: model,
        )

        self.assertEqual(status["mismatched_model_embeddings"], 0)
        self.assertEqual(model.calls, [(["first", "second"], 4, True)])
        updates = [
            params
            for sql, params in cursor.calls
            if "UPDATE search.overseas_etf_strategy" in sql
        ]
        self.assertEqual(
            updates,
            [
                ("[0.1,0.2,0.3]", "model-b", 11),
                ("[0.4,0.5,0.6]", "model-b", 12),
            ],
        )
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(cursor.closed)

    def test_verification_failure_rolls_back(self):
        cursor = FakeCursor(rows=[], status=(2, 0, 1, 1))
        connection = FakeConnection(cursor)

        with self.assertRaisesRegex(RuntimeError, "model_mismatch=1"):
            embed_overseas.run(connection, self.settings, batch_size=4)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    def test_document_dimension_mismatch_happens_before_update(self):
        cursor = FakeCursor(rows=[(11, "first")], status=(1, 0, 1, 0))
        connection = FakeConnection(cursor)
        model = FakeModel([[0.1, 0.2]])

        with self.assertRaisesRegex(
            RuntimeError, "document embedding dimension mismatch"
        ):
            embed_overseas.run(
                connection,
                self.settings,
                batch_size=4,
                model_factory=lambda settings: model,
            )

        self.assertFalse(
            any(
                "UPDATE search.overseas_etf_strategy" in sql
                for sql, _ in cursor.calls
            )
        )
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
