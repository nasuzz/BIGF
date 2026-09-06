"""PostgreSQL connection pool and health-check helper used by the API.

Kept intentionally small: a single lazily-created connection pool shared by
`/health` and the DataGateway implementation instead of opening a fresh
connection per request.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from psycopg2 import OperationalError
from psycopg2.errors import QueryCanceled
from psycopg2.pool import ThreadedConnectionPool

from b_agent.safe_logging import log_failure

logger = logging.getLogger("product_finder_api")

_DB_CONNECT_TIMEOUT_SEC = 2
_DB_STATEMENT_TIMEOUT_MS = 2000
_RETRIEVAL_STATEMENT_TIMEOUT_MS = 5000

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


class DatabaseUnavailableError(RuntimeError):
    """Safe, credential-free error raised when a pooled DB operation fails."""


def _get_pool() -> ThreadedConnectionPool | None:
    global _pool
    if _pool is not None:
        return _pool

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.error("DATABASE_URL is not set; cannot initialize database pool")
        return None

    with _pool_lock:
        if _pool is not None:
            return _pool

        try:
            _pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                dsn=dsn,
                connect_timeout=_DB_CONNECT_TIMEOUT_SEC,
            )
        except Exception as exc:  # noqa: BLE001 - any construction failure means no pool
            log_failure(logger, exc, stage="db.pool.create")
            _pool = None

    return _pool


def _reset_pool(expected_pool: ThreadedConnectionPool) -> None:
    """Drop the current pool so the next check attempts a fresh connection.

    Without this, a pool created while the DB was reachable would keep
    handing out (or failing on) stale connections after an outage, even
    once the DB recovers.

    Only resets `_pool` if it is still `expected_pool` - another thread may
    have already reset and recreated the pool since this caller's failure,
    and closing that unrelated replacement would drop live connections.
    """
    global _pool
    with _pool_lock:
        if _pool is not expected_pool:
            return
        try:
            _pool.closeall()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            log_failure(logger, exc, stage="db.pool.close")
        _pool = None


def check_connection() -> bool:
    """Run a short-timeout `SELECT 1` against the pooled connection.

    Returns True if the database answered, False otherwise. Never raises -
    a database outage must not take the API process down with it.
    """
    db_pool = _get_pool()
    if db_pool is None:
        return False

    conn = None
    try:
        conn = db_pool.getconn()
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {_DB_STATEMENT_TIMEOUT_MS}")
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except OperationalError as exc:
        log_failure(logger, exc, stage="db.health", level=logging.WARNING)
        _reset_pool(db_pool)
        return False
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        log_failure(logger, exc, stage="db.health")
        return False
    finally:
        if conn is not None:
            try:
                db_pool.putconn(conn)
            except Exception:  # noqa: BLE001 - pool may already be closed/reset
                pass


@contextmanager
def connection(
    statement_timeout_ms: int = _RETRIEVAL_STATEMENT_TIMEOUT_MS,
) -> Iterator[object]:
    """Borrow one pooled connection for a retrieval operation.

    Unlike ``check_connection``, retrieval failures must propagate to the B
    executor so they become ``StepOutcome.FAILED`` rather than a false 0-row
    search result. Only the driver error category is logged; a credential-free
    public exception suppresses the original driver message and traceback chain.
    """
    db_pool = _get_pool()
    if db_pool is None:
        raise DatabaseUnavailableError("database pool is unavailable")

    conn = None
    try:
        conn = db_pool.getconn()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(max(1, int(statement_timeout_ms))),),
                )
            yield conn
    except QueryCanceled as exc:
        # 문장 시간 초과는 해당 트랜잭션만 롤백하면 된다. 공유 풀을
        # 닫으면 정상 처리 중인 다른 요청의 연결까지 끊길 수 있다.
        log_failure(logger, exc, stage="db.retrieve")
        raise DatabaseUnavailableError("database operation timed out") from None
    except OperationalError as exc:
        _reset_pool(db_pool)
        log_failure(logger, exc, stage="db.retrieve")
        raise DatabaseUnavailableError("database operation failed") from None
    finally:
        if conn is not None:
            try:
                db_pool.putconn(conn)
            except Exception:  # noqa: BLE001 - pool may already be closed/reset
                pass
