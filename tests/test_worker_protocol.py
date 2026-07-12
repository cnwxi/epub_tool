import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from python_backend import cli
from python_backend.protocol import TaskResult
from python_backend.services import decrypt_font


class WorkerProtocolTest(unittest.TestCase):
    def test_get_parent_pid_accepts_only_positive_integer_values(self):
        with patch.dict(os.environ, {cli.PARENT_PID_ENV: "123"}):
            self.assertEqual(cli.get_parent_pid(), 123)
        with patch.dict(os.environ, {cli.PARENT_PID_ENV: "0"}):
            self.assertIsNone(cli.get_parent_pid())
        with patch.dict(os.environ, {cli.PARENT_PID_ENV: "not-a-pid"}):
            self.assertIsNone(cli.get_parent_pid())

    def test_parent_process_liveness_requires_expected_parent_pid(self):
        with patch.object(cli.os, "getppid", return_value=123):
            self.assertTrue(cli.parent_process_is_alive(123))
            self.assertFalse(cli.parent_process_is_alive(456))

    def test_parent_monitor_uses_process_handle_on_windows(self):
        with (
            patch.object(cli.os, "name", "nt"),
            patch.object(cli, "monitor_windows_parent_process") as monitor_windows,
            patch.object(cli.os, "_exit") as exit_process,
        ):
            cli.monitor_parent_process(123)

        monitor_windows.assert_called_once_with(123)
        exit_process.assert_called_once_with(0)

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
                        "task_type": "reformat",
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
