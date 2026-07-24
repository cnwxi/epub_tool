# -*- coding: utf-8 -*-
#!/usr/bin/env python
# 源码: sigil吧ID: 遥遥心航

import re
from os import path
from typing import Any

from python_backend.services.epub.task_base import (
    EpubTaskBase,
    get_bookpath,
    split_file_reference as _split_file_reference,
)
from python_backend.services.epub.rewrite_engine import (
    EpubRewriteEngine,
    EpubTaskPolicy,
    RewritePolicy,
    run_epub_task,
)
from python_backend.services.utils.log import logwriter

logger = logwriter()


def _rewrite_reformat_toc(
    task: Any, match: re.Match[str], check_link: Any
) -> str:
    href, fragment = _split_file_reference(match.group(2))
    book_path = check_link(
        task.tocpath, get_bookpath(href, task.tocpath), href, fragment
    )
    if not book_path:
        return match.group()
    return f'src="Text/{path.basename(book_path)}{fragment}"'


def _rewrite_reformat_reference(_task: Any, match: re.Match[str]) -> str:
    href, fragment = _split_file_reference(match.group(3))
    if path.basename(href).endswith(".ncx"):
        return match.group()
    return (
        match.group(1) + "../Text/" + path.basename(href) + fragment + match.group(4)
    )


REFORMAT_REWRITE_POLICY = RewritePolicy(
    mapped_css_imports=False,
    permissive_css_import_whitespace=False,
    normalize_css_import_to_quotes=True,
    write_toc_after_resources=False,
    rewrite_toc=_rewrite_reformat_toc,
    rewrite_opf_reference=_rewrite_reformat_reference,
)

REFORMAT_TASK_POLICY = EpubTaskPolicy(
    action_name="重构",
    already_processed_message="警告: 该文件已经重排，无需再次处理！",
)


class EpubTool(EpubTaskBase):
    output_suffix = "_reformat_epub.epub"

    def __init__(self, epub_src: str) -> None:
        super().__init__(epub_src, logger)

    def _configure_resources(self) -> None:
        def create_target_href(_id: str, href: str, _mime: str) -> str:
            """格式化不改变资源名，保留与加解密任务一致的资源字段。"""
            return href

        self._classify_manifest_resources(create_target_href)
        self._check_manifest_and_spine()

    # 重构
    def restructure(self):
        EpubRewriteEngine(self, REFORMAT_REWRITE_POLICY).run()


def run(epub_src: str, output_path: str | None = None):
    return run_epub_task(epub_src, output_path, EpubTool, logger, REFORMAT_TASK_POLICY)
