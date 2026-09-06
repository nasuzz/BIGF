from __future__ import annotations

import math
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol


DEFAULT_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_DIMENSION = 1024
DEFAULT_MAX_SEQ_LENGTH = 512
DEFAULT_QUERY_INSTRUCTION = (
    "Given a user question about overseas ETF investment strategy, "
    "retrieve the most relevant ETF strategy descriptions."
)


class QueryEmbedder(Protocol):
    model_name: str
    dimension: int

    def encode_query(self, text: str) -> list[float]: ...


class EmbeddingModelLoadError(RuntimeError):
    """Raised when the configured query embedding model cannot be loaded."""


class EmbeddingGenerationError(RuntimeError):
    """Raised when a loaded model cannot encode a query."""


class EmbeddingDimensionError(RuntimeError):
    """Raised when an embedding does not match the pgvector column dimension."""


@dataclass(frozen=True)
class EmbeddingSettings:
    """Shared document/query embedding settings for the fixed DB vector column."""

    model_name: str
    dimension: int
    max_seq_length: int


ModelFactory = Callable[[str, int], Any]


class SentenceTransformerQueryEmbedder:
    """Lazy, process-reused query embedder matching A's ingestion handoff.

    Model construction and encoding are guarded independently. Construction is
    performed at most once for this object, and the default object is cached for
    the life of the API process.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        dimension: int = DEFAULT_DIMENSION,
        max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH,
        instruction: str = DEFAULT_QUERY_INSTRUCTION,
        model_factory: ModelFactory | None = None,
    ) -> None:
        if dimension <= 0:
            raise ValueError("embedding dimension must be positive")
        if max_seq_length <= 0:
            raise ValueError("embedding max sequence length must be positive")
        self.model_name = str(model_name).strip() or DEFAULT_MODEL_NAME
        self.dimension = int(dimension)
        self.max_seq_length = int(max_seq_length)
        self.instruction = str(instruction).strip()
        self._model_factory = model_factory or _sentence_transformer_factory
        self._model: Any | None = None
        self._load_lock = threading.Lock()
        self._encode_lock = threading.Lock()

    def encode_query(self, text: str) -> list[float]:
        query = str(text or "").strip()
        if not query:
            raise EmbeddingGenerationError("query text is empty")

        model = self._get_model()
        encoded_text = f"{self.instruction} {query}" if self.instruction else query
        try:
            # SentenceTransformer/PyTorch inference is shared by concurrent API
            # worker threads; serializing encode on one model avoids mutable model
            # state races while still keeping the model loaded once.
            with self._encode_lock:
                encoded = model.encode(encoded_text, normalize_embeddings=True)
        except Exception as exc:  # noqa: BLE001 - third-party model boundary
            raise EmbeddingGenerationError("query embedding generation failed") from exc

        return validated_embedding_vector(encoded, self.dimension, label="query")

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                model = self._model_factory(self.model_name, self.dimension)
                model.max_seq_length = self.max_seq_length
            except Exception as exc:  # noqa: BLE001 - third-party model boundary
                raise EmbeddingModelLoadError(
                    "query embedding model could not be loaded"
                ) from exc
            self._model = model
            return model


def _sentence_transformer_factory(model_name: str, dimension: int) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, truncate_dim=dimension)


def _as_float_vector(value: Any) -> list[float]:
    raw = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise EmbeddingGenerationError("query embedding is not a one-dimensional vector")
    try:
        return [float(item) for item in raw]
    except (TypeError, ValueError) as exc:
        raise EmbeddingGenerationError(
            "query embedding is not a one-dimensional numeric vector"
        ) from exc


def validated_embedding_vector(
    value: Any,
    dimension: int,
    *,
    label: str = "embedding",
) -> list[float]:
    """Return a finite one-dimensional vector matching the configured dimension."""

    values = _as_float_vector(value)
    if len(values) != dimension:
        raise EmbeddingDimensionError(
            f"{label} embedding dimension mismatch: expected {dimension}, "
            f"received {len(values)}"
        )
    if not all(math.isfinite(item) for item in values):
        raise EmbeddingGenerationError(f"{label} embedding contains a non-finite value")
    return values


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def embedding_settings_from_env() -> EmbeddingSettings:
    """Load the one embedding contract shared by ingestion and query paths."""

    settings = EmbeddingSettings(
        model_name=(os.getenv("EMBEDDING_MODEL") or DEFAULT_MODEL_NAME).strip()
        or DEFAULT_MODEL_NAME,
        dimension=_positive_int_env("EMBEDDING_DIMENSION", DEFAULT_DIMENSION),
        max_seq_length=_positive_int_env(
            "EMBEDDING_MAX_SEQ_LENGTH", DEFAULT_MAX_SEQ_LENGTH
        ),
    )
    if settings.dimension != DEFAULT_DIMENSION:
        raise EmbeddingDimensionError(
            "configured embedding dimension mismatch: "
            f"database expects {DEFAULT_DIMENSION}, configured {settings.dimension}"
        )
    return settings


@lru_cache(maxsize=1)
def default_query_embedder() -> SentenceTransformerQueryEmbedder:
    """Return the single query embedder shared by the API process."""

    settings = embedding_settings_from_env()
    return SentenceTransformerQueryEmbedder(
        model_name=settings.model_name,
        dimension=settings.dimension,
        max_seq_length=settings.max_seq_length,
        instruction=os.getenv("EMBEDDING_QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION),
    )
