import io
import json
import sys
import unittest
from unittest.mock import patch

from python_backend import cli
from python_backend.protocol import TaskResult
from python_backend.services import decrypt_font


class WorkerProtocolTest(unittest.TestCase):
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
            with patch.object(
                cli,
                "run_task",
                return_value=TaskResult(
                    ok=True,
                    status="success",
                    summary={"total": 0, "success": 0, "failed": 0, "skipped": 0},
                ),
            ):
                self.assertEqual(cli.cmd_serve(None), 0)
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
