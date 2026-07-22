import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from python_backend import task_runner


class TaskRunnerLoadingTest(unittest.TestCase):
    def setUp(self):
        self.original_modules = task_runner._LOADED_MODULES.copy()
        task_runner._LOADED_MODULES.clear()

    def tearDown(self):
        task_runner._LOADED_MODULES.clear()
        task_runner._LOADED_MODULES.update(self.original_modules)

    def test_load_module_imports_only_requested_service_and_caches_it(self):
        module = SimpleNamespace(logger=object())
        with patch.object(task_runner, "import_module", return_value=module) as importer:
            self.assertIs(task_runner.load_module("reformat_epub"), module)
            self.assertIs(task_runner.load_module("reformat_epub"), module)

        importer.assert_called_once_with("python_backend.services.reformat_epub")

    def test_patched_logger_only_touches_requested_module(self):
        original_logger = object()
        module = SimpleNamespace(logger=original_logger)
        logger = object()
        with patch.object(task_runner, "load_module", return_value=module) as loader:
            with task_runner.patched_logger("encrypt_epub", logger):
                self.assertIs(module.logger, logger)

        self.assertIs(module.logger, original_logger)
        loader.assert_called_once_with("encrypt_epub")

    def test_execute_task_uses_the_standard_run_entry_point(self):
        run = Mock(return_value=0)
        module = SimpleNamespace(run=run)

        with patch.object(task_runner, "load_module", return_value=module):
            result = task_runner.execute_task("reformat_epub", "book.epub", "output", {})

        self.assertEqual(result, 0)
        run.assert_called_once_with("book.epub", "output")

    def test_batch_font_targets_keeps_per_file_errors(self):
        def fake_list_font_targets(path):
            if path.endswith("broken.epub"):
                raise ValueError("损坏的 EPUB")
            return {"ok": True, "input_file": path, "font_families": ["Target"]}

        with patch.object(task_runner, "list_font_targets", side_effect=fake_list_font_targets):
            events = list(task_runner.iter_font_targets(["good.epub", "broken.epub"]))

        self.assertEqual(events[0]["event"], "font-targets.progress")
        self.assertEqual(events[0]["current_index"], 1)
        self.assertEqual(events[0]["total_files"], 2)
        self.assertEqual(events[0]["result"]["font_families"], ["Target"])
        self.assertFalse(events[1]["result"]["ok"])
        self.assertEqual(events[1]["result"]["input_file"], os.path.normpath("broken.epub"))
        self.assertIn("损坏", events[1]["result"]["error"])


if __name__ == "__main__":
    unittest.main()
