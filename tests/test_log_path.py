import os
import sys
import tempfile
import unittest
from pathlib import Path

from python_backend.services.log import logwriter


class LogWriterPathTest(unittest.TestCase):
    def test_default_log_path_uses_current_working_directory(self):
        original_argv = sys.argv[:]
        original_cwd = os.getcwd()
        original_env = os.environ.pop("EPUB_TOOL_LOG_PATH", None)

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    sys.argv[0] = "/usr/local/bin/pytest"
                    os.chdir(temp_dir)

                    writer = logwriter()
                    writer.write("hello")

                    expected_path = Path(temp_dir) / "log.txt"
                    self.assertEqual(Path(writer.path).resolve(), expected_path.resolve())
                    self.assertIn("hello", expected_path.read_text(encoding="utf-8"))
                finally:
                    os.chdir(original_cwd)
        finally:
            os.chdir(original_cwd)
            sys.argv[:] = original_argv
            if original_env is not None:
                os.environ["EPUB_TOOL_LOG_PATH"] = original_env


if __name__ == "__main__":
    unittest.main()
