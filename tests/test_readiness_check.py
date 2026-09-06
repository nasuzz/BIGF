import io
import unittest
from unittest.mock import Mock

from api.readiness_check import check_readiness, main, response_is_ready


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def _response(body: str) -> _Response:
    return _Response(body.encode("utf-8"))


class ReadinessCheckTest(unittest.TestCase):
    def test_connected_database_is_ready(self):
        self.assertTrue(
            response_is_ready({"status": "ok", "database": "connected"})
        )

    def test_degraded_or_disconnected_database_is_not_ready(self):
        self.assertFalse(
            response_is_ready({"status": "degraded", "database": "disconnected"})
        )
        self.assertFalse(response_is_ready({"status": "ok"}))

    def test_probe_returns_false_for_invalid_json_and_connection_errors(self):
        self.assertFalse(check_readiness(opener=lambda *_args, **_kwargs: _response("{")))

        failing_opener = Mock(side_effect=OSError("connection refused"))
        self.assertFalse(check_readiness(opener=failing_opener))

    def test_probe_changes_back_to_ready_after_database_recovers(self):
        opener = Mock(
            side_effect=[
                _response('{"status":"degraded","database":"disconnected"}'),
                _response('{"status":"ok","database":"connected"}'),
            ]
        )

        self.assertFalse(check_readiness(opener=opener))
        self.assertTrue(check_readiness(opener=opener))

    def test_cli_exit_code_matches_readiness(self):
        with unittest.mock.patch("api.readiness_check.check_readiness", return_value=True):
            self.assertEqual(main(["http://api/health"]), 0)
        with unittest.mock.patch("api.readiness_check.check_readiness", return_value=False):
            self.assertEqual(main(["http://api/health"]), 1)


if __name__ == "__main__":
    unittest.main()
