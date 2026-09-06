"""Capture rendered logs and records at every application error boundary (#35)."""

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from psycopg2 import OperationalError
from psycopg2.errors import QueryCanceled

from api import db
from api.main import app
from agent.ext_retrievers import PostgresHoldingRetriever
from b_agent.models import Capability, StepOutcome
from b_agent.pipeline import BAgentPipeline
from b_agent.retrievers import RetrieverRegistry


SECRETS = (
    "issue35-password-marker", "issue35-token-marker", "issue35-private-host",
)
ERROR_TEXT = (
    "postgresql://db_user:issue35-password-marker@issue35-private-host/funds "
    "NCP_DB_PASSWORD=issue35-password-marker Authorization: Bearer issue35-token-marker"
)


class SafeErrorLoggingTest(unittest.TestCase):
    def _assert_safe(self, captured, *diagnostics):
        rendered = "\n".join(captured.output)
        for secret in SECRETS:
            self.assertNotIn(secret, rendered)
        self.assertNotIn("Traceback", rendered)
        for value in diagnostics:
            self.assertIn(value, rendered)
        for record in captured.records:
            self.assertFalse(record.exc_info)
            self.assertIsNone(record.exc_text)
            self.assertIsNone(record.stack_info)
            for secret in SECRETS:
                self.assertNotIn(secret, repr(record.__dict__))

    def test_api_exception_keeps_request_identifiers_without_exception_details(self):
        with (
            self.assertLogs("product_finder_api", level="ERROR") as captured,
            patch("api.main.answer_question", side_effect=RuntimeError(ERROR_TEXT)),
        ):
            response = TestClient(app).get(
                "/answer", params={"question_id": "Q-safe-api", "question": "국내 ETF"},
            )
        self.assertEqual(response.status_code, 200)
        self._assert_safe(captured, "request_id=", "question_id=Q-safe-api", "stage=api", "RuntimeError")
        for secret in SECRETS:
            self.assertNotIn(secret, response.text)

    def test_pipeline_route_and_retrieval_exceptions_keep_stage_and_question_id(self):
        for stage in ("route", "retrieve"):
            with self.subTest(stage=stage):
                pipeline = BAgentPipeline()
                error = RuntimeError("outer error")
                error.__cause__ = ConnectionError(ERROR_TEXT)
                target = pipeline if stage == "route" else pipeline.executor
                method = "plan" if stage == "route" else "execute"
                with (
                    self.assertLogs("b_agent.pipeline", level="ERROR") as captured,
                    patch.object(target, method, side_effect=error),
                ):
                    result = pipeline.execute("Q-safe-pipeline", "순자산 높은 국내 ETF")
                self.assertEqual(result.error_stage, stage)
                self._assert_safe(captured, "question_id=Q-safe-pipeline", f"stage={stage}", "RuntimeError")

    def test_failed_retrieval_message_and_logs_do_not_store_driver_details(self):
        registry = RetrieverRegistry()
        retriever = MagicMock()
        retriever.retrieve.side_effect = ConnectionError(ERROR_TEXT)
        registry.register(Capability.STRUCTURED_SEARCH, retriever)
        with self.assertLogs("b_agent.executor", level="ERROR") as captured:
            result = BAgentPipeline(registry=registry).execute("Q-safe-step", "순자산 높은 국내 ETF")
            step = result.report.step_results[0]
            self.assertEqual(step.outcome, StepOutcome.FAILED)
            self.assertIn("ConnectionError", step.message)
            for secret in SECRETS:
                self.assertNotIn(secret, step.message)
        self._assert_safe(captured, "stage=retrieve", "step_id=structured_search", "ConnectionError")

    def test_pool_creation_and_cleanup_errors_are_safe(self):
        with (
            patch.object(db, "_pool", None),
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://test.invalid/test"}),
            patch.object(db, "ThreadedConnectionPool", side_effect=OperationalError(ERROR_TEXT)),
            self.assertLogs("product_finder_api", level="ERROR") as captured,
        ):
            self.assertIsNone(db._get_pool())
        self._assert_safe(captured, "stage=db.pool.create", "OperationalError")
        pool = MagicMock()
        pool.closeall.side_effect = RuntimeError(ERROR_TEXT)
        with patch.object(db, "_pool", pool), self.assertLogs("product_finder_api", level="ERROR") as captured:
            db._reset_pool(pool)
            self.assertIsNone(db._pool)
        self._assert_safe(captured, "stage=db.pool.close", "RuntimeError")

    def test_external_holding_fetcher_failure_uses_the_safe_executor_boundary(self):
        fetcher = MagicMock()
        fetcher.fetch_all.side_effect = OperationalError(ERROR_TEXT)
        registry = RetrieverRegistry()
        registry.register(Capability.HOLDING_SEARCH, PostgresHoldingRetriever(fetcher))
        with self.assertLogs("b_agent.executor", level="ERROR") as captured:
            result = BAgentPipeline(registry=registry).execute(
                "Q-safe-holding", "삼성전자를 편입한 국내 ETF"
            )
        fetcher.fetch_all.assert_called_once()
        step = result.report.step_results[0]
        self.assertEqual(step.outcome, StepOutcome.FAILED)
        self.assertIn("OperationalError", step.message)
        for secret in SECRETS:
            self.assertNotIn(secret, step.message)
        self._assert_safe(captured, "stage=retrieve", "step_id=holding_search")

    def test_health_operational_and_unexpected_errors_are_safe(self):
        for error_type in (OperationalError, RuntimeError):
            with self.subTest(error_type=error_type.__name__):
                pool = MagicMock()
                pool.getconn.side_effect = error_type(ERROR_TEXT)
                with (
                    patch.object(db, "_pool", pool),
                    self.assertLogs("product_finder_api", level="WARNING") as captured,
                ):
                    self.assertFalse(db.check_connection())
                self._assert_safe(captured, "stage=db.health", error_type.__name__)

    def test_retrieval_database_failures_suppress_raw_exception_chains(self):
        for error_type in (OperationalError, QueryCanceled):
            with self.subTest(error_type=error_type.__name__):
                pool = MagicMock()
                pool.getconn.side_effect = error_type(ERROR_TEXT)
                with (
                    patch.object(db, "_pool", pool),
                    self.assertLogs("product_finder_api", level="ERROR") as captured,
                    self.assertRaises(db.DatabaseUnavailableError) as raised,
                ):
                    with db.connection():
                        self.fail("unavailable DB must not yield a connection")
                self.assertIsNone(raised.exception.__cause__)
                self.assertTrue(raised.exception.__suppress_context__)
                self._assert_safe(captured, "stage=db.retrieve", error_type.__name__)

    def test_logging_does_not_call_exception_string_or_repr(self):
        class UnprintableError(RuntimeError):
            def __str__(self):
                raise AssertionError("exception must not be formatted")

            def __repr__(self):
                raise AssertionError("exception must not be represented")

        pipeline = BAgentPipeline()
        with (
            self.assertLogs("b_agent.pipeline", level="ERROR") as captured,
            patch.object(pipeline, "plan", side_effect=UnprintableError(ERROR_TEXT)),
        ):
            result = pipeline.execute("Q-unprintable", "국내 ETF")
        self.assertEqual(result.error_stage, "route")
        self._assert_safe(captured, "UnprintableError", "question_id=Q-unprintable")


if __name__ == "__main__":
    unittest.main()
