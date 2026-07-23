import io
import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

from python_backend import cli
from python_backend.json_output import dumps_json_line
from python_backend.protocol import TaskResult
from python_backend.services.font import decrypt_font


class WorkerProtocolTest(unittest.TestCase):
    def test_configure_stdio_uses_utf8_for_worker_protocol(self):
        stdin = Mock()
        stdout = Mock()
        stderr = Mock()

        with (
            patch.object(cli.sys, "stdin", stdin),
            patch.object(cli.sys, "stdout", stdout),
            patch.object(cli.sys, "stderr", stderr),
        ):
            cli.configure_stdio()

        stdin.reconfigure.assert_called_once_with(encoding="utf-8", errors="strict")
        stdout.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")
        stderr.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_json_line_output_replaces_lone_surrogates(self):
        payload = {
            "message": "broken-\ud83d-value",
            "valid_pair": "emoji-\ud83d\ude00",
        }

        encoded = dumps_json_line(payload)
        decoded = json.loads(encoded)

        self.assertEqual(decoded["message"], "broken-\ufffd-value")
        self.assertEqual(decoded["valid_pair"], "emoji-😀")
        self.assertNotIn("\\ud83d-value", encoded)

    def test_parent_liveness_monitor_exits_when_rust_closes_socket(self):
        class FakeConnection:
            sent = b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def sendall(self, payload):
                self.sent = payload

            def settimeout(self, timeout):
                self.timeout = timeout

            def recv(self, _size):
                return b""

        connection = FakeConnection()
        with (
            patch.object(cli.socket, "create_connection", return_value=connection) as create_connection,
            patch.object(cli.os, "_exit") as exit_process,
        ):
            cli.monitor_parent_liveness("127.0.0.1:1234", "test-token")

        create_connection.assert_called_once_with(("127.0.0.1", 1234), timeout=10)
        self.assertEqual(connection.sent, b"test-token\n")
        self.assertIsNone(connection.timeout)
        exit_process.assert_called_once_with(0)

    def test_start_parent_monitor_requires_complete_liveness_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli.threading, "Thread") as thread:
            cli.start_parent_monitor()
        thread.assert_not_called()

    def test_start_parent_monitor_uses_liveness_configuration(self):
        environment = {
            cli.PARENT_LIVENESS_ADDR_ENV: "127.0.0.1:1234",
            cli.PARENT_LIVENESS_TOKEN_ENV: "test-token",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(
            cli.threading, "Thread"
        ) as thread:
            cli.start_parent_monitor()

        thread.assert_called_once_with(
            target=cli.monitor_parent_liveness,
            args=("127.0.0.1:1234", "test-token"),
            name="epub-tool-parent-monitor",
            daemon=True,
        )
        thread.return_value.start.assert_called_once()

    def test_serve_returns_result_for_run_request(self):
        original_stdin = sys.stdin
        original_stdout = sys.stdout
        sys.stdin = io.StringIO(
            json.dumps(
                {
                    "request_id": "run-1",
                    "command": "run",
                    "request": {
                        "task_id": "task-1",
                        "task_type": "reformat_epub",
                        "input_files": [],
                        "output_dir": None,
                        "options": {},
                    },
                }
            )
            + "\n"
        )
        output = io.StringIO()
        sys.stdout = output
        try:
            with (
                patch.object(
                    cli,
                    "run_task",
                    return_value=TaskResult(
                        ok=True,
                        status="success",
                        summary={"total": 0, "success": 0, "failed": 0, "skipped": 0},
                    ),
                ),
                patch.object(cli, "start_parent_monitor") as start_parent_monitor,
            ):
                self.assertEqual(cli.cmd_serve(None), 0)
                start_parent_monitor.assert_called_once()
        finally:
            sys.stdin = original_stdin
            sys.stdout = original_stdout

        response = json.loads(output.getvalue())
        self.assertEqual(response["event"], "worker.response")
        self.assertEqual(response["request_id"], "run-1")
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["status"], "success")

    def test_ocr_backend_is_reused_for_same_model_configuration(self):
        decrypt_font._OCR_BACKEND_CACHE.clear()
        options = {"onnx_max_image_width": 640}
        backend = object()
        with (
            patch.object(decrypt_font, "resolve_onnx_ocr_model_dir", return_value="/model"),
            patch.object(
                decrypt_font,
                "resolve_onnx_ocr_config_path",
                return_value="/model/inference.yml",
            ),
            patch.object(decrypt_font, "OnnxGlyphOcrBackend", return_value=backend) as factory,
        ):
            self.assertIs(decrypt_font.create_ocr_backend(options), backend)
            self.assertIs(decrypt_font.create_ocr_backend(options), backend)

        factory.assert_called_once_with(options)
        decrypt_font._OCR_BACKEND_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
