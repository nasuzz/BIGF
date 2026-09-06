from __future__ import annotations

import unittest
from unittest.mock import patch

from b_agent.embeddings import (
    EmbeddingDimensionError,
    EmbeddingGenerationError,
    EmbeddingModelLoadError,
    SentenceTransformerQueryEmbedder,
    embedding_settings_from_env,
)


class FakeModel:
    def __init__(self, vector):
        self.vector = vector
        self.calls = []
        self.max_seq_length = None

    def encode(self, text, normalize_embeddings=False):
        self.calls.append((text, normalize_embeddings))
        if isinstance(self.vector, Exception):
            raise self.vector
        return list(self.vector)


class SentenceTransformerQueryEmbedderTest(unittest.TestCase):
    def test_model_is_loaded_once_and_query_contract_matches_handoff(self):
        model = FakeModel([0.1, 0.2, 0.3])
        factory_calls = []

        def factory(name, dimension):
            factory_calls.append((name, dimension))
            return model

        embedder = SentenceTransformerQueryEmbedder(
            model_name="test-model",
            dimension=3,
            max_seq_length=512,
            instruction="retrieve overseas ETF strategies.",
            model_factory=factory,
        )

        first = embedder.encode_query("반도체 ETF")
        second = embedder.encode_query("배당 ETF")

        self.assertEqual(first, [0.1, 0.2, 0.3])
        self.assertEqual(second, [0.1, 0.2, 0.3])
        self.assertEqual(factory_calls, [("test-model", 3)])
        self.assertEqual(model.max_seq_length, 512)
        self.assertEqual(
            model.calls,
            [
                ("retrieve overseas ETF strategies. 반도체 ETF", True),
                ("retrieve overseas ETF strategies. 배당 ETF", True),
            ],
        )

    def test_model_load_failure_has_distinct_error_type(self):
        def broken_factory(name, dimension):
            del name, dimension
            raise OSError("private model path")

        embedder = SentenceTransformerQueryEmbedder(
            dimension=3,
            model_factory=broken_factory,
        )

        with self.assertRaisesRegex(
            EmbeddingModelLoadError, "model could not be loaded"
        ):
            embedder.encode_query("반도체 ETF")

    def test_encode_failure_has_distinct_error_type(self):
        model = FakeModel(RuntimeError("GPU unavailable"))
        embedder = SentenceTransformerQueryEmbedder(
            dimension=3,
            model_factory=lambda name, dimension: model,
        )

        with self.assertRaisesRegex(
            EmbeddingGenerationError, "embedding generation failed"
        ):
            embedder.encode_query("반도체 ETF")

    def test_dimension_mismatch_has_distinct_error_type(self):
        model = FakeModel([0.1, 0.2])
        embedder = SentenceTransformerQueryEmbedder(
            dimension=3,
            model_factory=lambda name, dimension: model,
        )

        with self.assertRaisesRegex(
            EmbeddingDimensionError, "expected 3, received 2"
        ):
            embedder.encode_query("반도체 ETF")

    def test_non_finite_embedding_is_rejected(self):
        model = FakeModel([0.1, float("nan"), 0.3])
        embedder = SentenceTransformerQueryEmbedder(
            dimension=3,
            model_factory=lambda name, dimension: model,
        )

        with self.assertRaisesRegex(EmbeddingGenerationError, "non-finite"):
            embedder.encode_query("반도체 ETF")

    def test_shared_settings_read_model_dimension_and_max_length(self):
        with patch.dict(
            "os.environ",
            {
                "EMBEDDING_MODEL": "replacement-model",
                "EMBEDDING_DIMENSION": "1024",
                "EMBEDDING_MAX_SEQ_LENGTH": "384",
            },
            clear=False,
        ):
            settings = embedding_settings_from_env()

        self.assertEqual(settings.model_name, "replacement-model")
        self.assertEqual(settings.dimension, 1024)
        self.assertEqual(settings.max_seq_length, 384)

    def test_shared_settings_reject_dimension_incompatible_with_database(self):
        with patch.dict(
            "os.environ", {"EMBEDDING_DIMENSION": "768"}, clear=False
        ):
            with self.assertRaisesRegex(
                EmbeddingDimensionError,
                "database expects 1024, configured 768",
            ):
                embedding_settings_from_env()


if __name__ == "__main__":
    unittest.main()
