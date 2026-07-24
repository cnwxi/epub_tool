# -*- coding: utf-8 -*-
# !/usr/bin/env python
# 源码: sigil吧ID: 遥遥心航
# 二改: cnwxi
# 额外感谢: 故里

import re
from os import path
from typing import Any
from difflib import SequenceMatcher
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


def _prepare_decrypt_rewrite(task: Any) -> None:
    if task.encrypted and task.encrypted_text_or_css:
        task._logger.write("检测到 encryption.xml 标记了 Text/Styles 资源，继续尝试反混淆重构。")
        task._logger.write("如果内容确实被加密，后续读取资源时会失败并中止处理。")


def _rewrite_decrypt_toc(
    task: Any, match: re.Match[str], _check_link: Any
) -> str:
    href, fragment = _split_file_reference(match.group(2))
    href = task.toc_rn[href] if href in task.toc_rn else href
    book_path = get_bookpath(href, task.tocpath)
    if not book_path:
        return match.group()
    return f'src="Text/{path.basename(book_path)}{fragment}"'


def _rewrite_decrypt_reference(task: Any, match: re.Match[str]) -> str:
    href, fragment = _split_file_reference(match.group(3))
    if path.basename(href).endswith(".ncx"):
        return match.group()
    try:
        filename = task.toc_rn[href]
    except KeyError:
        task._logger.write(f"写入content.opf时，文件链接出错: {href}")
        similarities = [
            SequenceMatcher(
                None,
                item[0].rsplit("/", 1)[-1].split(".")[0],
                href.rsplit("/", 1)[-1].split(".")[0],
            ).quick_ratio()
            for item in task.text_list
        ]
        index = max(range(len(similarities)), key=similarities.__getitem__)
        original_href = href
        href = task.text_list[index][1]
        task._logger.write(
            f"已自动替换为相似度最高文件: {original_href} <-> {task.text_list[index]}"
        )
        filename = task.toc_rn[href]
    return match.group(1) + "Text/" + filename + fragment + match.group(4)


def _decrypt_manifest_id(task: Any, item_id: str) -> str:
    return task.manifest_id_renames.get(item_id, item_id)


def _replace_decrypt_manifest_ids(task: Any, opf: str) -> str:
    return task._replace_manifest_id_references(opf)


DECRYPT_REWRITE_POLICY = RewritePolicy(
    mapped_css_imports=True,
    strict_text_and_css_reads=True,
    prepare=_prepare_decrypt_rewrite,
    rewrite_toc=_rewrite_decrypt_toc,
    rewrite_opf_reference=_rewrite_decrypt_reference,
    output_manifest_id=_decrypt_manifest_id,
    transform_opf=_replace_decrypt_manifest_ids,
)

DECRYPT_TASK_POLICY = EpubTaskPolicy(
    action_name="解密",
    already_processed_message="警告: 该文件已解密，无需再次处理！",
    skip_when_encrypted=True,
    encryption_state_skip_message="警告: 该文件未加密，无需处理！",
    include_corrected_path=True,
    invalid_idref_advice=(
        "措施: 请自行检查spine内的itemref节点并手动修改，确保引用的ID存在于manifest的item项。\n"
        "（大小写不一致也会导致引用无效。）"
    ),
)


def _append_slim_to_id(item_id):
    """在 ID 的扩展名前插入统一的 ~slim 后缀。"""
    id_stem, id_extension = path.splitext(item_id)
    return f"{id_stem}~slim{id_extension}"


def _strip_slim_suffix_from_id(item_id):
    """移除 ID 末尾已有的多看 slim 后缀，用于孤立 slim 图片兜底命名。"""
    id_stem, id_extension = path.splitext(item_id)
    if id_stem.lower().endswith("slim"):
        id_stem = re.sub(r"(?i)[~_-]?slim$", "", id_stem)
    return id_stem + id_extension


class EpubTool(EpubTaskBase):
    missing_manifest_id_seed = "newsrc"
    output_suffix = "_decrypt_epub.epub"
    preserve_raw_manifest_hrefs = True

    def __init__(self, epub_src: str) -> None:
        super().__init__(epub_src, logger)

    def _initialize_task_state(self) -> None:
        self.encrypted = False
        self.encryption_uris = []
        self.encryption_keynames = set()
        self.encryption_algorithms = set()
        self.encrypted_text_or_css = False
        self._detect_encryption()

    def _detect_encryption(self):
        enc_name = None
        for name in self.namelist:
            if name.lower() == "meta-inf/encryption.xml":
                enc_name = name
                break
        if not enc_name:
            return
        try:
            encryption_xml = self._read_xml_text(enc_name)
            root = self._parse_xml_safe(
                encryption_xml,
                label=f"ENCRYPTION:{enc_name}",
                allow_sanitize=False,
            )
        except Exception as e:
            logger.write(f"解析 encryption.xml 失败: {e}")
            return

        uris, keynames, algs = self._extract_encryption_info(root)

        self.encryption_uris = uris
        self.encryption_keynames = keynames
        self.encryption_algorithms = algs
        key_hits = any(k == "DuoKan.Inc" for k in keynames)
        algo_hits = any("aes128-ctr" in a.lower() for a in algs)
        important = []
        for uri in uris:
            u = uri.lower()
            if (
                "oebps/text/" in u
                or "oebps/styles/" in u
                or "oebps/images/" in u
                or u.startswith("text/")
                or u.startswith("styles/")
                or u.startswith("images/")
            ):
                important.append(uri)
        self.encrypted_text_or_css = any(
            (
                "oebps/text/" in u.lower()
                or "oebps/styles/" in u.lower()
                or u.lower().startswith("text/")
                or u.lower().startswith("styles/")
            )
            for u in uris
        )
        if uris:
            self.encrypted = True
            logger.write(f"检测到 encryption.xml 加密资源: {len(uris)} 项")
            logger.write(f"加密资源示例(最多10条): {uris[:10]}")
        if key_hits:
            logger.write("检测到 DuoKan.Inc KeyName")
        if algo_hits:
            logger.write("检测到 aes128-ctr 加密算法标识")
        if uris and important:
            logger.write(
                "提醒: 本工具仅做结构规范化/反混淆，不提供 DRM 解密；对加密内容将无法还原明文。"
            )

    def _extract_encryption_info(self, root):
        uris = []
        keynames = set()
        algs = set()
        for elem in root.iter():
            tag = re.sub(r"\{.*?\}", "", elem.tag)
            if tag == "CipherReference":
                uri = elem.get("URI") or ""
                if uri:
                    uris.append(uri)
            elif tag == "KeyName" and elem.text:
                keynames.add(elem.text.strip())
            elif tag == "EncryptionMethod":
                alg = elem.get("Algorithm") or ""
                if alg:
                    algs.add(alg)
        return uris, keynames, algs

    def _configure_resources(self) -> None:
        # 多看 slim 图片只按 href 文件名配对，不能依赖原始 ID 是否包含 slim。
        image_id_by_href = {}
        for item_id, item_href, item_mime, _ in self.manifest_list:
            base_href, _, is_slim = split_slim_href(item_href)
            if item_mime and "image/" in item_mime and not is_slim:
                image_id_by_href[base_href.lower()] = item_id

        self.manifest_id_renames = {}
        used_manifest_ids = {item[0] for item in self.manifest_list}

        def allocate_slim_id(base_id, old_id):
            candidate = _append_slim_to_id(base_id)
            if candidate == old_id or candidate not in used_manifest_ids:
                return candidate

            id_stem, id_extension = path.splitext(base_id)
            sequence = 2
            while True:
                candidate = f"{id_stem}_{sequence}~slim{id_extension}"
                if candidate == old_id or candidate not in used_manifest_ids:
                    return candidate
                sequence += 1

        # 生成新的href
        ############################################################
        def create_target_href(_id: str, _href: str, _mime: str) -> str:
            base_href, href_extension, is_slim = split_slim_href(_href)
            output_id = _id
            if _mime and "image/" in _mime and is_slim:
                base_id = image_id_by_href.get(base_href.lower())
                if base_id is None:
                    base_id = _strip_slim_suffix_from_id(_id)
                output_id = allocate_slim_id(base_id, _id)
                self.manifest_id_renames[_id] = output_id
                used_manifest_ids.discard(_id)
                used_manifest_ids.add(output_id)

            id_name, _ = path.splitext(output_id)
            if re.search(r'[\\/:*?"<>|]', id_name):
                logger.write(f"ID: {output_id} 中包含非法字符")
                id_name = hashlibmd5(id_name.encode()).hexdigest()
                logger.write(f"ID: {output_id} 替换为 {id_name}")

            image_slim = "~slim" if _mime and "image/" in _mime and is_slim else ""
            if image_slim and id_name.lower().endswith("slim"):
                id_name = re.sub(r"(?i)[~_-]?slim$", "", id_name)
            new_href = f"{id_name}{image_slim}{href_extension.lower()}"
            logger.write(f"decrypt href: {_id}:{_href} -> {new_href}")
            return new_href

        ############################################################
        def register_text_toc_name(
            _id: str, href: str, _mime: str, target_href: str
        ) -> None:
            self.toc_rn[href] = target_href

        self._classify_manifest_resources(
            create_target_href,
            on_item=self._mark_encrypted_for_unsafe_href,
            on_text=register_text_toc_name,
        )
        self._check_manifest_and_spine()

    def _replace_manifest_id_references(self, opf):
        """同步替换 OPF 中指向已重命名 manifest ID 的引用。"""
        if not self.manifest_id_renames:
            return opf

        idref_pattern = re.compile(
            r'(\b(?:idref|fallback|fallback-style|media-overlay|handler|toc)\s*=\s*)(["\'])(.*?)\2',
            flags=re.IGNORECASE,
        )

        def replace_idref(match):
            value = self.manifest_id_renames.get(match.group(3), match.group(3))
            return f"{match.group(1)}{match.group(2)}{value}{match.group(2)}"

        opf = idref_pattern.sub(replace_idref, opf)

        refines_pattern = re.compile(
            r'(\brefines\s*=\s*)(["\'])#(.*?)\2',
            flags=re.IGNORECASE,
        )

        def replace_refines(match):
            value = self.manifest_id_renames.get(match.group(3), match.group(3))
            return f"{match.group(1)}{match.group(2)}#{value}{match.group(2)}"

        opf = refines_pattern.sub(replace_refines, opf)

        def replace_cover_meta(match):
            tag = match.group()
            if not re.search(
                r'\bname\s*=\s*(["\'])cover\1',
                tag,
                flags=re.IGNORECASE,
            ):
                return tag

            def replace_content(content_match):
                value = self.manifest_id_renames.get(
                    content_match.group(3), content_match.group(3)
                )
                return (
                    f"{content_match.group(1)}{content_match.group(2)}"
                    f"{value}{content_match.group(2)}"
                )

            return re.sub(
                r'(\bcontent\s*=\s*)(["\'])(.*?)\2',
                replace_content,
                tag,
                count=1,
                flags=re.IGNORECASE,
            )

        return re.sub(
            r"<meta\b[^>]*>",
            replace_cover_meta,
            opf,
            flags=re.IGNORECASE,
        )

    # 重构
    def restructure(self):
        EpubRewriteEngine(self, DECRYPT_REWRITE_POLICY).run()


def run(epub_src: str, output_path: str | None = None):
    return run_epub_task(epub_src, output_path, EpubTool, logger, DECRYPT_TASK_POLICY)
