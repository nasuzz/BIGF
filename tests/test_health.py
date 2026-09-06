import os
import unittest
from unittest.mock import call, patch

from fastapi.testclient import TestClient
from psycopg2 import OperationalError
from psycopg2.errors import QueryCanceled

from api import db
from api.main import app

client = TestClient(app)


class HealthEndpointTest(unittest.TestCase):
    @patch("api.main.check_connection", return_value=True)
    def test_health_ok_when_database_connected(self, mock_check):
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "connected")
        mock_check.assert_called_once()

    @patch("api.main.check_connection", return_value=False)
    def test_health_degraded_when_database_disconnected(self, mock_check):
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"], "disconnected")

    @patch("api.main.check_connection", return_value=True)
    def test_health_reports_hcx_ready_only_with_complete_valid_environment(self, mock_check):
        env = {
            "CLOVA_API_KEY": "nv-key",
            "CLOVA_ENDPOINT": "https://clovastudio.stream.ntruss.com",
            "MODEL_NAME": "HCX-005",
        }

        with patch.dict(os.environ, env, clear=True):
            response = client.get("/health")

        self.assertEqual(response.json()["llm"], "ready")

    @patch("api.main.check_connection", return_value=True)
    def test_health_reports_hcx_not_ready_for_partial_environment(self, mock_check):
        with patch.dict(os.environ, {"CLOVA_API_KEY": "nv-key"}, clear=True):
            response = client.get("/health")

        self.assertEqual(response.json()["llm"], "not_ready")


class DbCheckConnectionTest(unittest.TestCase):
    def setUp(self):
        db._pool = None

    def tearDown(self):
        db._pool = None

    def test_returns_false_when_database_url_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            self.assertFalse(db.check_connection())

    @patch("api.db.ThreadedConnectionPool")
    def test_returns_false_and_resets_pool_on_operational_error(self, mock_pool_cls):
        mock_pool = mock_pool_cls.return_value
        mock_pool.getconn.side_effect = OperationalError("connection refused")

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}):
            result = db.check_connection()

        self.assertFalse(result)
        mock_pool.closeall.assert_called_once()
        self.assertIsNone(db._pool)

    @patch("api.db.ThreadedConnectionPool")
    def test_returns_true_on_successful_select(self, mock_pool_cls):
        mock_pool = mock_pool_cls.return_value
        mock_conn = mock_pool.getconn.return_value
        mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
        mock_cursor.fetchone.return_value = (1,)

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}):
            result = db.check_connection()

        self.assertTrue(result)
        self.assertEqual(
            mock_cursor.execute.call_args_list,
            [
                call("SET statement_timeout = 2000"),
                call("SELECT 1"),
            ],
        )
        mock_cursor.fetchone.assert_called_once_with()
        mock_pool.putconn.assert_called_once_with(mock_conn)

    @patch("api.db.ThreadedConnectionPool")
    def test_retrieval_connection_uses_pool_and_five_second_timeout(self, mock_pool_cls):
        mock_pool = mock_pool_cls.return_value
        mock_conn = mock_pool.getconn.return_value
        mock_cursor = mock_conn.cursor.return_value.__enter__.return_value

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}):
            with db.connection() as borrowed:
                self.assertIs(borrowed, mock_conn)

        mock_cursor.execute.assert_called_once_with(
            "SELECT set_config('statement_timeout', %s, true)",
            ("5000",),
        )
        mock_pool.putconn.assert_called_once_with(mock_conn)

    @patch("api.db.ThreadedConnectionPool")
    def test_retrieval_connection_propagates_safe_database_failure(self, mock_pool_cls):
        mock_pool = mock_pool_cls.return_value
        mock_pool.getconn.side_effect = OperationalError(
            "postgresql://user:password@private-db/funds"
        )

        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}),
            self.assertRaisesRegex(db.DatabaseUnavailableError, "database operation failed"),
        ):
            with db.connection():
                pass

        mock_pool.closeall.assert_called_once()
        self.assertIsNone(db._pool)

    @patch("api.db.ThreadedConnectionPool")
    def test_statement_timeout_does_not_close_shared_pool(self, mock_pool_cls):
        mock_pool = mock_pool_cls.return_value
        mock_conn = mock_pool.getconn.return_value
        mock_cursor = mock_conn.cursor.return_value.__enter__.return_value
        mock_cursor.execute.side_effect = QueryCanceled("statement timeout")

        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgresql://u:p@localhost/db"}),
            self.assertRaisesRegex(
                db.DatabaseUnavailableError, "database operation timed out"
            ),
        ):
            with db.connection():
                pass

        mock_pool.closeall.assert_not_called()
        self.assertIs(db._pool, mock_pool)
        mock_pool.putconn.assert_called_once_with(mock_conn)


if __name__ == "__main__":
    unittest.main()
