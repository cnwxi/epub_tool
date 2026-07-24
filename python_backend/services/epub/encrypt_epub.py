# -*- coding: utf-8 -*-
# !/usr/bin/env python
# 源码: sigil吧ID: 遥遥心航
# 二改: cnwxi

import re
from os import path
from typing import Any
from hashlib import md5 as hashlibmd5

from python_backend.services.epub.task_base import (
    EpubTaskBase,
    get_bookpath,
    split_slim_href,
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


def _rewrite_hashed_toc(
    task: Any, match: re.Match[str], _check_link: Any
) -> str:
    href, fragment = _split_file_reference(match.group(2))
    href = task.toc_rn[href] if href in task.toc_rn else href
    book_path = get_bookpath(href, task.tocpath)
    if not book_path:
        return match.group()
    return f'src="Text/{path.basename(book_path)}{fragment}"'


def _rewrite_hashed_reference(task: Any, match: re.Match[str]) -> str:
    href, fragment = _split_file_reference(match.group(3))
    if path.basename(href).endswith(".ncx"):
        return match.group()
    if href.startswith("/"):
        href = href[1:]
    elif href.startswith("./"):
        href = href[2:]
    elif href.startswith("../"):
        href = href[3:]
    return match.group(1) + "Text/" + task.toc_rn[href] + fragment + match.group(4)


ENCRYPT_REWRITE_POLICY = RewritePolicy(
    mapped_css_imports=True,
    rewrite_toc=_rewrite_hashed_toc,
    rewrite_opf_reference=_rewrite_hashed_reference,
)

ENCRYPT_TASK_POLICY = EpubTaskPolicy(
    action_name="加密",
    already_processed_message="警告: 该文件已加密，无需再次处理！",
    skip_when_encrypted=False,
    encryption_state_skip_message="警告: 该文件已加密，无需再次处理！",
    invalid_idref_advice=(
        "措施: 请自行检查spine内的itemref节点并手动修改，确保引用的ID存在于manifest的item项。\n"
        "大小写不一致也会导致引用无效。）"
    ),
)


class EpubTool(EpubTaskBase):
    missing_manifest_id_seed = "newsrc"
    output_suffix = "_encrypt_epub.epub"

    def __init__(self, epub_src: str) -> None:
        super().__init__(epub_src, logger)

    def _initialize_task_state(self) -> None:
        self.encrypted = False

    def _configure_resources(self) -> None:
        image_id_by_href = {}
        for item_id, item_href, item_mime, _ in self.manifest_list:
            base_href, _, is_slim = split_slim_href(item_href)
            if item_mime and "image/" in item_mime and not is_slim:
                image_id_by_href[base_href.lower()] = item_id

        # 生成新的href
        ############################################################
        def create_target_href(_id: str, _href: str, _mime: str) -> str:
            _id_name = _id.split(".")[0]
            base_href, _file_extension, is_slim = split_slim_href(_href)
            if _mime and "image/" in _mime and is_slim:
                image_slim = "~slim"
                _id_name = image_id_by_href.get(base_href.lower(), _id).split(".")[0]
            else:
                image_slim = ""
            _href_hash = hashlibmd5(_id_name.encode()).digest()
            _href_hash = int.from_bytes(_href_hash, byteorder="big")
            bin_hash = bin(_href_hash)
            new_href = (
                bin_hash.replace("-", "*")
                .replace("0b", "")
                .replace("1", "*")
                .replace("0", ":")
            )
            # 加_为了防止Windows系统异常
            new_href = f"_{new_href}{image_slim}{_file_extension.lower()}"
            if new_href not in self.toc_rn.values():
                self.toc_rn[_href] = new_href
                logger.write(f"encrypt href: {_id}:{_href} -> {self.toc_rn[_href]}")
            else:
                self.toc_rn[_href] = new_href
                logger.write(f"encrypt href: {_id}:{_href} -> {new_href} 重复")
            return new_href

        ############################################################

        self._classify_manifest_resources(
            create_target_href,
            on_item=self._mark_encrypted_for_unsafe_href,
        )
        self._check_manifest_and_spine()

    # 重构
    def restructure(self):
        EpubRewriteEngine(self, ENCRYPT_REWRITE_POLICY).run()


def run(epub_src: str, output_path: str | None = None):
    return run_epub_task(epub_src, output_path, EpubTool, logger, ENCRYPT_TASK_POLICY)
