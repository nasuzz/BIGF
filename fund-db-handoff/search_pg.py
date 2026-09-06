from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol


# Keep this script runnable as `python fund-db-handoff/search_pg.py`.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from b_agent.embeddings import (  # noqa: E402
    DEFAULT_QUERY_INSTRUCTION,
    QueryEmbedder,
    SentenceTransformerQueryEmbedder,
    embedding_settings_from_env,
)


DEFAULT_QUERIES = (
    "미국 기술주 중심의 성장형 ETF 전략을 찾고 있어",
    "배당을 많이 주는 안정적인 ETF",
    "반도체 관련 ETF 추천해줘",
)

SEARCH_SQL = """
    SELECT document_id, raw_row_id, raw_text,
           1 - (embedding <=> %s::vector) AS cosine_sim
    FROM search.overseas_etf_strategy
    WHERE embedding IS NOT NULL
      AND embedding_model = %s
    ORDER BY embedding <=> %s::vector
    LIMIT %s
"""


class CursorLike(Protocol):
    def execute(self, query: str, params: Sequence[Any] | None = None) -> None: ...

    def fetchall(self) -> list[Any]: ...


def search(
    cursor: CursorLike,
    embedder: QueryEmbedder,
    query: str,
    *,
    limit: int = 5,
) -> list[Any]:
    vector = embedder.encode_query(query)
    vector_value = "[" + ",".join(map(str, vector)) + "]"
    cursor.execute(
        SEARCH_SQL,
        (vector_value, embedder.model_name, vector_value, limit),
    )
    return list(cursor.fetchall())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="현재 API 임베딩 설정으로 해외 ETF 전략문을 검색합니다."
    )
    parser.add_argument("queries", nargs="*", help="검색할 자연어 질의")
    parser.add_argument("--limit", type=int, default=5, help="질의별 최대 결과 수")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        parser.error("--limit must be positive")

    # Validate the fixed vector(1024) contract before opening the database.
    settings = embedding_settings_from_env()
    embedder = SentenceTransformerQueryEmbedder(
        model_name=settings.model_name,
        dimension=settings.dimension,
        max_seq_length=settings.max_seq_length,
        instruction=os.getenv(
            "EMBEDDING_QUERY_INSTRUCTION", DEFAULT_QUERY_INSTRUCTION
        ),
    )

    import psycopg2

    from db_config import psycopg2_connection_kwargs

    connection = psycopg2.connect(**psycopg2_connection_kwargs())
    cursor = connection.cursor()
    try:
        for query in args.queries or DEFAULT_QUERIES:
            print(f"\n=== 질문: {query} ===")
            for row in search(cursor, embedder, query, limit=args.limit):
                print(f"score={row[3]:.4f} | raw_row_id={row[1]} | {row[2][:80]}")
    finally:
        cursor.close()
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
