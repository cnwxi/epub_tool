import os
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO, TextIOWrapper
from pathlib import Path

from python_backend import task_runner
from python_backend.epub_metadata import (
    TOOL_META_CONTENT,
    TOOL_META_NAME,
    add_tool_meta_to_opf,
    mark_epub_generated_by_tool,
)
from python_backend.protocol import TaskEvent, TaskRequest
from python_backend.task_runner import JsonLineEmitter


def build_minimal_epub(epub_path, opf_text):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        epub.writestr("OEBPS/content.opf", opf_text)


def read_opf(epub_path):
    with zipfile.ZipFile(epub_path) as epub:
        return epub.read("OEBPS/content.opf").decode("utf-8")


class EpubMetadataTest(unittest.TestCase):
    def test_add_tool_meta_to_existing_metadata(self):
        opf, changed = add_tool_meta_to_opf(
            """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata>
    <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Book</dc:title>
  </metadata>
  <manifest/>
  <spine/>
</package>"""
        )

        self.assertTrue(changed)
        self.assertIn(
            f'<meta name="{TOOL_META_NAME}" content="{TOOL_META_CONTENT}" />',
            opf,
        )
        self.assertLess(opf.index("<meta"), opf.index("</metadata>"))

    def test_add_tool_meta_creates_metadata_when_missing(self):
        opf, changed = add_tool_meta_to_opf(
            """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <manifest/>
  <spine/>
</package>"""
        )

        self.assertTrue(changed)
        self.assertIn("<metadata>", opf)
        self.assertIn(
            f'<meta name="{TOOL_META_NAME}" content="{TOOL_META_CONTENT}" />',
            opf,
        )

    def test_mark_epub_generated_by_tool_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_minimal_epub(
                epub_path,
                """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata/>
  <manifest/>
  <spine/>
</package>""",
            )

            self.assertTrue(mark_epub_generated_by_tool(epub_path))
            self.assertFalse(mark_epub_generated_by_tool(epub_path))
            opf = read_opf(epub_path)

            self.assertEqual(opf.count(f'name="{TOOL_META_NAME}"'), 1)
            with zipfile.ZipFile(epub_path) as epub:
                self.assertEqual(epub.namelist().count("OEBPS/content.opf"), 1)

    def test_task_runner_marks_successful_output_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "book.epub"
            output_path = Path(temp_dir) / "book_reformat.epub"
            build_minimal_epub(
                input_path,
                """<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata>
    <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Book</dc:title>
  </metadata>
  <manifest/>
  <spine/>
</package>""",
            )

            original_execute_task = task_runner.execute_task

            def fake_execute_task(task_type, input_file, output_dir, options):
                with zipfile.ZipFile(input_file) as source:
                    with zipfile.ZipFile(output_path, "w") as target:
                        for info in source.infolist():
                            target.writestr(info, source.read(info.filename))
                return 0

            task_runner.execute_task = fake_execute_task
            try:
                result = task_runner.run_task(
                    TaskRequest(
                        task_id="test-task",
                        task_type="reformat",
                        input_files=[str(input_path)],
                        output_dir=temp_dir,
                    )
                )
            finally:
                task_runner.execute_task = original_execute_task

            self.assertTrue(result.ok)
            self.assertEqual(result.outputs, [str(output_path)])
            self.assertIn(
                f'<meta name="{TOOL_META_NAME}" content="{TOOL_META_CONTENT}" />',
                read_opf(output_path),
            )

    def test_json_line_emitter_handles_non_utf8_stdout(self):
        original_stdout = sys.stdout
        buffer = BytesIO()
        cp1252_stdout = TextIOWrapper(buffer, encoding="cp1252")
        sys.stdout = cp1252_stdout
        try:
            JsonLineEmitter().emit(
                TaskEvent(
                    event="task.started",
                    task_id="test-task",
                    status="started",
                    progress=0,
                    message="开始执行 格式化",
                )
            )
            cp1252_stdout.flush()
        finally:
            sys.stdout = original_stdout
            cp1252_stdout.detach()

        payload = buffer.getvalue().decode("cp1252")
        self.assertIn(r"\u5f00\u59cb\u6267\u884c", payload)


if __name__ == "__main__":
    unittest.main()
