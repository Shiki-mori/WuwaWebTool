import contextlib
import io
import socket
import unittest
from unittest.mock import MagicMock, patch

from core.startup_check import _is_wuwa_service, check_startup_port


class StartupCheckTests(unittest.TestCase):
    def test_free_port_allows_startup(self):
        self.assertIsNone(check_startup_port(0))

    def test_existing_wuwa_returns_success_without_starting_another_instance(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch("core.startup_check._is_wuwa_service", return_value=True), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(check_startup_port(port), 0)
            self.assertIn(f"Already running on port {port}", output.getvalue())
            self.assertIn(f"http://localhost:{port}", output.getvalue())
            self.assertEqual(listener.getsockname()[1], port)

    def test_foreign_listener_reports_conflict_without_killing_it(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch("core.startup_check._is_wuwa_service", return_value=False), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(check_startup_port(port), 1)
            self.assertIn("another program", output.getvalue())
            self.assertEqual(listener.getsockname()[1], port)

    def test_health_check_requires_expected_service_and_status(self):
        for body, expected in ((b'{"service":"wuwa-web","status":"ok"}', True),
                               (b'{"service":"other","status":"ok"}', False),
                               (b'{"service":"wuwa-web","status":"down"}', False),
                               (b'[]', False), (b'not-json', False)):
            with self.subTest(body=body):
                opener = MagicMock()
                opener.open.return_value.__enter__.return_value.read.return_value = body
                with patch("core.startup_check.build_opener", return_value=opener):
                    self.assertEqual(_is_wuwa_service(8765), expected)
                opener.open.assert_called_once_with("http://127.0.0.1:8765/api/health", timeout=2)

    def test_unresponsive_listener_is_not_treated_as_wuwa(self):
        opener = MagicMock()
        opener.open.side_effect = TimeoutError()
        with patch("core.startup_check.build_opener", return_value=opener):
            self.assertFalse(_is_wuwa_service(8765))


if __name__ == "__main__":
    unittest.main()
