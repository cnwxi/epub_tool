"""EPUB 重写任务的解析基类与基础工具。

这里放置三个 EPUB 任务共享的输入解析、资源分类和路径逻辑；资源改写与
输出归档由 :mod:`rewrite_engine` 负责，任务模块仅保留命名与加解密策略。
"""

from __future__ import annotations

import codecs
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol
from urllib.parse import unquote
from xml.etree import ElementTree
import zipfile
from os import path


class _LogWriter(Protocol):
    def write(self, message: str) -> None: ...


def split_file_reference(reference: str) -> tuple[str, str]:
    """拆分字面 fragment，只解码用于文件处理的路径部分。"""
    raw_path, separator, fragment = reference.strip().partition("#")
    suffix = separator + fragment if separator else ""
    return unquote(raw_path), suffix


def get_relpath(from_path: str, to_path: str) -> str:
    """按照现有 EPUB 重写逻辑计算两个归档路径之间的相对路径。"""
    from_parts = re.split(r"[\\/]", from_path)
    to_parts = re.split(r"[\\/]", to_path)
    while from_parts and to_parts and from_parts[0] == to_parts[0]:
        from_parts.pop(0)
        to_parts.pop(0)
    return "../" * (len(from_parts) - 1) + "/".join(to_parts)


def get_bookpath(relative_path: str, refer_bkpath: str) -> str:
    """将 EPUB 内相对路径解析为相对于归档根目录的路径。"""
    relative_parts = re.split(r"[\\/]", relative_path)
    refer_parts = re.split(r"[\\/]", refer_bkpath)

    back_step = 0
    while relative_parts and relative_parts[0] == "..":
        back_step += 1
        relative_parts.pop(0)

    if len(refer_parts) <= 1:
        return "/".join(relative_parts)

    refer_parts.pop()
    if back_step < 1:
        return "/".join(refer_parts + relative_parts)
    if back_step > len(refer_parts):
        return "/".join(relative_parts)

    while back_step > 0 and refer_parts:
        refer_parts.pop()
        back_step -= 1
    return "/".join(refer_parts + relative_parts)


def split_slim_href(href: str) -> tuple[str, str, bool]:
    """按 href 文件名识别并移除多看的 slim 后缀。"""
    href_dir, href_basename = path.split(href)
    href_stem, href_extension = path.splitext(href_basename)
    is_slim = href_stem.lower().endswith("slim")
    if is_slim:
        href_stem = re.sub(r"(?i)[~_-]?slim$", "", href_stem)
    return path.join(href_dir, href_stem + href_extension), href_extension, is_slim


def build_resource_path_maps(
    opf_path: str,
    resource_groups: Mapping[str, Iterable[tuple[str, ...]]],
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """为各类资源构建归档路径到目标文件名的映射。

    资源项的前两个字段分别为 manifest ID 与原始 href，最后一个字段为
    目标 href。三个任务已经使用同一结构，因此可共享重名消解规则。
    """
    path_maps = {resource_type: {} for resource_type in resource_groups}
    used_basenames = {resource_type: [] for resource_type in resource_groups}
    lower_to_original: dict[str, str] = {}

    for resource_type, resources in resource_groups.items():
        for resource in resources:
            item_id, href = resource[:2]
            target_href = resource[-1]
            filename, extension = path.splitext(path.basename(target_href))
            candidate = filename
            index = 0
            while candidate + extension in used_basenames[resource_type]:
                index += 1
                candidate = f"{filename}_{index}"
            basename = candidate + extension
            used_basenames[resource_type].append(basename)

            book_path = get_bookpath(href, opf_path)
            path_maps[resource_type][book_path] = basename
            lower_to_original[book_path.lower()] = book_path

    return path_maps, lower_to_original


class EpubTaskBase:
    """EPUB 重写任务的共享状态与解析生命周期。

    子类在初始化开始时设置 ``_logger``，使任务运行器替换模块 logger 后
    仍能把日志正确发送到任务事件流。
    """

    _logger: _LogWriter
    output_suffix: str
    preserve_raw_manifest_hrefs = False

    def __init__(self, epub_src: str, logger: _LogWriter) -> None:
        self._logger = logger
        self.epub = zipfile.ZipFile(epub_src)
        self.tgt_epub = None
        self.file_write_path = None
        self.epub_src = epub_src
        self.epub_name = path.basename(epub_src)
        self.ebook_root = path.dirname(epub_src)
        self.output_path = self.ebook_root
        self.epub_type = ""
        self.temp_dir = ""
        self._init_namelist()
        self._init_mime_map()
        self._init_opf()

        self.manifest_list = []
        self.id_to_href = {}
        self.href_to_id = {}
        self.text_list = []
        self.css_list = []
        self.image_list = []
        self.font_list = []
        self.audio_list = []
        self.video_list = []
        self.spine_list = []
        self.other_list = []
        self.toc_rn = {}
        self.errorOPF_log = []
        self.errorLink_log = {}
        if self.preserve_raw_manifest_hrefs:
            self.id_to_raw_href = {}

        self._initialize_task_state()
        self._parse_opf()

    def _initialize_task_state(self) -> None:
        """初始化任务专属状态；普通格式化任务无需额外状态。"""

    def set_output_path(self, output_path: str | None) -> None:
        if output_path is not None and os.path.isdir(output_path):
            self.output_path = output_path
        self.file_write_path = path.join(
            self.output_path, self.epub_name.replace(".epub", self.output_suffix)
        )

    def _init_namelist(self) -> None:
        self.namelist = self.epub.namelist()

    def _init_mime_map(self) -> None:
        self.mime_map = {
            ".html": "application/xhtml+xml",
            ".xhtml": "application/xhtml+xml",
            ".css": "text/css",
            ".js": "application/javascript",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".bmp": "image/bmp",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".ttf": "font/ttf",
            ".otf": "font/otf",
            ".woff": "font/woff",
            ".ncx": "application/x-dtbncx+xml",
            ".mp3": "audio/mpeg",
            ".mp4": "video/mp4",
            ".smil": "application/smil+xml",
            ".pls": "application/pls+xml",
        }

    def _classify_manifest_resources(
        self,
        create_target_href: Callable[[str, str, str], str],
        on_item: Callable[[str, str, str, str], None] | None = None,
        on_text: Callable[[str, str, str, str], None] | None = None,
    ) -> None:
        """按统一资源项契约分类 manifest，并保留任务专属命名钩子。"""
        opf_dir = path.dirname(self.opfpath)
        for item_id, href, mime, properties in self.manifest_list:
            if on_item is not None:
                on_item(item_id, href, mime, properties)

            if mime == "application/xhtml+xml":
                target_href = create_target_href(item_id, href, mime)
                self.text_list.append((item_id, href, properties, target_href))
                if on_text is not None:
                    on_text(item_id, href, mime, target_href)
            elif mime == "text/css":
                self.css_list.append(
                    (item_id, href, properties, create_target_href(item_id, href, mime))
                )
            elif "image/" in mime:
                self.image_list.append(
                    (item_id, href, properties, create_target_href(item_id, href, mime))
                )
            elif "font/" in mime or href.lower().endswith((".ttf", ".otf", ".woff")):
                self.font_list.append(
                    (item_id, href, properties, create_target_href(item_id, href, mime))
                )
            elif "audio/" in mime:
                self.audio_list.append(
                    (item_id, href, properties, create_target_href(item_id, href, mime))
                )
            elif "video/" in mime:
                self.video_list.append(
                    (item_id, href, properties, create_target_href(item_id, href, mime))
                )
            elif self.tocid != "" and item_id == self.tocid:
                self.tocpath = f"{opf_dir}/{href}" if opf_dir else href
            else:
                self.other_list.append(
                    (
                        item_id,
                        href,
                        mime,
                        properties,
                        create_target_href(item_id, href, mime),
                    )
                )

    def _mark_encrypted_for_unsafe_href(
        self, _item_id: str, href: str, _mime: str, _properties: str
    ) -> None:
        """标记包含原加密任务非法文件名字符的 manifest 项。"""
        if re.search(r'[\\/:*?"<>|]', href.rsplit("/", 1)[-1]):
            self.encrypted = True

    def _decode_xml_bytes(self, data: bytes, default: str = "utf-8") -> str:
        decode_order = [
            default,
            "utf-8",
            "utf-8-sig",
            "utf-16",
            "utf-16le",
            "utf-16be",
        ]
        if data.startswith(codecs.BOM_UTF8):
            decode_order = ["utf-8-sig"] + decode_order
        elif data.startswith(codecs.BOM_UTF16_LE):
            decode_order = ["utf-16", "utf-16le"] + decode_order
        elif data.startswith(codecs.BOM_UTF16_BE):
            decode_order = ["utf-16", "utf-16be"] + decode_order
        seen = set()
        for encoding in decode_order:
            if not encoding or encoding in seen:
                continue
            seen.add(encoding)
            try:
                return re.sub(
                    r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode(encoding)
                )
            except UnicodeDecodeError:
                continue
        try:
            self._logger.write("XML decode fallback: utf decode failed, try gb18030")
            return re.sub(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("gb18030")
            )
        except UnicodeDecodeError:
            self._logger.write("XML decode fallback: gb18030 failed, use latin-1")
            return re.sub(
                r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("latin-1")
            )

    def _read_xml_text(self, zip_path: str) -> str:
        try:
            data = self.epub.read(zip_path)
        except KeyError as error:
            raise FileNotFoundError(f"zip内缺少XML文件: {zip_path}") from error
        return self._decode_xml_bytes(data)

    @staticmethod
    def _sanitize_attr_value(value: str) -> str:
        value = re.sub(
            r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][\w.-]*;)", "&amp;", value
        )
        return value.replace("<", "&lt;").replace(">", "&gt;")

    def _sanitize_xml_attr_text(self, xml_text: str) -> str:
        pattern = re.compile(
            r"(<[^>]+?)((?:\s+[^\s=>/]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'))?)+)(\s*/?>)",
            re.DOTALL,
        )

        def repl_tag(match: re.Match[str]) -> str:
            prefix, attrs, suffix = match.groups()
            attrs = re.sub(
                r"(=\s*\")([^\"]*)(\")",
                lambda item: item.group(1)
                + self._sanitize_attr_value(item.group(2))
                + item.group(3),
                attrs,
                flags=re.DOTALL,
            )
            attrs = re.sub(
                r"(=\s*')([^']*)(')",
                lambda item: item.group(1)
                + self._sanitize_attr_value(item.group(2))
                + item.group(3),
                attrs,
                flags=re.DOTALL,
            )
            return prefix + attrs + suffix

        return pattern.sub(repl_tag, xml_text)

    def _xml_parse_error_with_context(
        self, xml_text: str, label: str, err: Exception
    ) -> None:
        self._logger.write(f"XML parse error [{label}]: {err}")
        position = getattr(err, "position", None)
        if not position:
            return
        line_no, col_no = position
        self._logger.write(f"位置: line={line_no}, column={col_no}")
        lines = xml_text.splitlines()
        start = max(1, line_no - 2)
        end = min(len(lines), line_no + 2)
        self._logger.write(f"{label} 出错上下文:")
        for index in range(start, end + 1):
            self._logger.write(f"{index:>6}: {lines[index - 1]}")

    def _parse_xml_safe(
        self, xml_text: str, label: str, allow_sanitize: bool = True
    ) -> ElementTree.Element:
        first_error: ElementTree.ParseError | None = None
        try:
            return ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as error:
            first_error = error
            self._xml_parse_error_with_context(xml_text, label, error)
            if not allow_sanitize:
                raise
        sanitized = self._sanitize_xml_attr_text(xml_text)
        if sanitized == xml_text:
            assert first_error is not None
            raise first_error
        try:
            self._logger.write(f"XML sanitize retry: {label}")
            return ElementTree.fromstring(sanitized)
        except ElementTree.ParseError as error:
            self._xml_parse_error_with_context(sanitized, f"{label}[sanitized]", error)
            raise

    @staticmethod
    def _parse_tag_attrs(text: str) -> dict[str, str]:
        attrs = {}
        for match in re.finditer(
            r"([:\w.-]+)\s*=\s*([\"'])(.*?)\2", text, flags=re.DOTALL
        ):
            attrs[match.group(1)] = match.group(3)
        return attrs

    def _parse_opf(self) -> None:
        """执行三个 EPUB 任务共享的 OPF 解析、修复和资源配置生命周期。"""
        parse_failed = False
        try:
            package = self._parse_xml_safe(self.opf, label=f"OPF:{self.opfpath}")
            self.etree_opf = {"package": package}
        except Exception as error:
            self._logger.write(f"OPF 解析失败，启用降级解析: {error}")
            self._fallback_parse_opf_manifest_spine()
            parse_failed = True

        if not parse_failed:
            for child in self.etree_opf["package"]:
                tag = re.sub(r"\{.*?\}", "", child.tag)
                self.etree_opf[tag] = child
            self._parse_metadata()
            self._parse_manifest()
            self._parse_spine()
            self._clear_duplicate_id_href()
            self._parse_hrefs_not_in_epub()
            self._add_files_not_in_opf()
        else:
            self._parse_hrefs_not_in_epub()
            self._add_files_not_in_opf()

        self.manifest_list = [
            (item_id, href, mime, properties)
            for item_id, (href, mime, properties) in self.id_to_h_m_p.items()
        ]
        version = self.etree_opf["package"].get("version")
        if version in ("2.0", "3.0"):
            self.epub_type = version
        elif parse_failed:
            self.epub_type = "2.0"
            self._logger.write("OPF 版本无法可靠识别，降级按 EPUB2 继续处理")
        else:
            raise RuntimeError("此脚本不支持该EPUB类型")

        self.tocpath = ""
        tocid = self.etree_opf.get("spine", ElementTree.Element("spine")).get("toc")
        self.tocid = tocid if tocid is not None else ""
        self._configure_resources()

    def _configure_resources(self) -> None:
        """由任务提供资源目标命名及少量专属资源钩子。"""
        raise NotImplementedError

    def _parse_manifest(self) -> None:
        """解析 manifest；解密任务可选择保留未 URL 解码的 href。"""
        self.id_to_h_m_p = {}
        self.id_to_href = {}
        self.href_to_id = {}
        if self.preserve_raw_manifest_hrefs:
            self.id_to_raw_href = {}

        if_error = False
        for item in self.etree_opf["manifest"]:
            try:
                item_id = item.get("id")
                raw_href = item.get("href")
                href = unquote(raw_href) if raw_href is not None else None
            except (AttributeError, TypeError) as error:
                item_xml = ElementTree.tostring(item, encoding="unicode").replace(
                    "\n", ""
                ).replace("\r", "").replace("\t", "")
                self._logger.write(f"item: {item_xml} error: {error}")
                if_error = True
                continue
            if not item_id or href is None:
                if_error = True
                continue
            mime = item.get("media-type")
            properties = item.get("properties") or ""
            self.id_to_h_m_p[item_id] = (href, mime, properties)
            self.id_to_href[item_id] = href.lower()
            self.href_to_id[href.lower()] = item_id
            if self.preserve_raw_manifest_hrefs:
                self.id_to_raw_href[item_id] = raw_href
        if if_error:
            self._logger.write("opf文件中存在错误，请检查！")

    def _fallback_parse_opf_manifest_spine(self) -> None:
        self._logger.write("opf_malformed_fallback_used")
        self.errorOPF_log.append(("opf_malformed_fallback_used", self.opfpath))
        self.metadata = {
            "title": "",
            "creator": "",
            "language": "",
            "subject": "",
            "source": "",
            "identifier": "",
            "cover": "",
        }
        self.id_to_h_m_p = {}
        self.id_to_href = {}
        self.href_to_id = {}
        if self.preserve_raw_manifest_hrefs:
            self.id_to_raw_href = {}
        self.spine_list = []

        manifest_match = re.search(r"(?is)<manifest\b[^>]*>(.*?)</manifest>", self.opf)
        if manifest_match:
            for item in re.finditer(r"(?is)<item\b(.*?)/?>", manifest_match.group(1)):
                attrs = self._parse_tag_attrs(item.group(1))
                item_id = attrs.get("id")
                href_raw = attrs.get("href")
                mime = attrs.get("media-type")
                properties = attrs.get("properties", "")
                if not item_id or href_raw is None:
                    continue
                href = unquote(href_raw)
                self.id_to_h_m_p[item_id] = (href, mime, properties)
                self.id_to_href[item_id] = href.lower()
                self.href_to_id[href.lower()] = item_id
                if self.preserve_raw_manifest_hrefs:
                    self.id_to_raw_href[item_id] = href_raw

        self.tocid = ""
        spine_open = re.search(r"(?is)<spine\b([^>]*)>", self.opf)
        if spine_open:
            self.tocid = self._parse_tag_attrs(spine_open.group(1)).get("toc", "")
        spine_match = re.search(r"(?is)<spine\b[^>]*>(.*?)</spine>", self.opf)
        if spine_match:
            for itemref in re.finditer(
                r"(?is)<itemref\b(.*?)/?>", spine_match.group(1)
            ):
                attrs = self._parse_tag_attrs(itemref.group(1))
                item_id = attrs.get("idref")
                if not item_id:
                    continue
                linear = attrs.get("linear", "")
                properties = attrs.get("properties", "")
                self.spine_list.append((item_id, linear, properties))

        package = ElementTree.Element("package")
        version_match = re.search(
            r"(?is)<package\b[^>]*\bversion\s*=\s*([\"'])(.*?)\1", self.opf
        )
        if version_match:
            package.set("version", version_match.group(2))
        self.etree_opf = {
            "package": package,
            "metadata": ElementTree.Element("metadata"),
            "manifest": ElementTree.Element("manifest"),
            "spine": ElementTree.Element("spine"),
        }
        if self.tocid:
            self.etree_opf["spine"].set("toc", self.tocid)

    def _init_opf(self) -> None:
        try:
            container_xml = self._read_xml_text("META-INF/container.xml")
            rootfile = re.search(
                r'<rootfile[^>]*full-path\s*=\s*([\'\"])(?i:(.*?\.opf))\1',
                container_xml,
            )
            if rootfile is not None:
                self.opfpath = rootfile.group(2)
                self.opf = self._read_xml_text(self.opfpath)
                return
        except Exception as error:
            self._logger.write(f"读取 container.xml 失败，将回退扫描opf: {error}")
        for book_path in self.namelist:
            if book_path.lower().endswith(".opf"):
                self.opfpath = book_path
                self.opf = self._read_xml_text(self.opfpath)
                return
        raise RuntimeError("无法发现opf文件")

    def _parse_metadata(self) -> None:
        self.metadata = {
            key: ""
            for key in (
                "title",
                "creator",
                "language",
                "subject",
                "source",
                "identifier",
                "cover",
            )
        }
        for meta in self.etree_opf["metadata"]:
            tag = re.sub(r"\{.*?\}", r"", meta.tag)
            if tag in {
                "title",
                "creator",
                "language",
                "subject",
                "source",
                "identifier",
            }:
                self.metadata[tag] = meta.text
            elif tag == "meta" and meta.get("name") and meta.get("content"):
                self.metadata["cover"] = meta.get("content")

    def _parse_spine(self) -> None:
        self.spine_list = []
        for itemref in self.etree_opf["spine"]:
            sid = itemref.get("idref")
            linear = itemref.get("linear") or ""
            properties = itemref.get("properties") or ""
            self.spine_list.append((sid, linear, properties))

    def _clear_duplicate_id_href(self) -> None:
        id_used = [item_id for item_id, _, _ in self.spine_list]
        if self.metadata["cover"]:
            id_used.append(self.metadata["cover"])

        deleted_ids = []
        for item_id, href in self.id_to_href.items():
            if self.href_to_id[href] == item_id:
                continue
            if item_id in id_used and self.href_to_id[href] not in id_used:
                if self.href_to_id[href] not in deleted_ids:
                    deleted_ids.append(self.href_to_id[href])
                self.href_to_id[href] = item_id
            elif item_id in id_used and self.href_to_id[href] in id_used:
                continue
            elif item_id not in deleted_ids:
                deleted_ids.append(item_id)

        for item_id in deleted_ids:
            self.errorOPF_log.append(("duplicate_id", item_id))
            del self.id_to_href[item_id]
            del self.id_to_h_m_p[item_id]

    def _check_manifest_and_spine(self) -> None:
        spine_idrefs = [item_id for item_id, _, _ in self.spine_list]
        for item_id in spine_idrefs:
            if not self.id_to_h_m_p.get(item_id):
                self.errorOPF_log.append(("invalid_idref", item_id))

        for item_id, _, mime, _ in self.manifest_list:
            if mime == "application/xhtml+xml" and item_id not in spine_idrefs:
                self.errorOPF_log.append(("xhtml_not_in_spine", item_id))

    def _parse_hrefs_not_in_epub(self) -> None:
        deleted_ids = []
        namelist = [item.lower() for item in self.epub.namelist()]
        for item_id, href in self.id_to_href.items():
            book_path = get_bookpath(href, self.opfpath)
            if book_path.lower() not in namelist:
                deleted_ids.append(item_id)
                del self.href_to_id[href]
        for item_id in deleted_ids:
            del self.id_to_href[item_id]
            del self.id_to_h_m_p[item_id]

    def _add_files_not_in_opf(self) -> None:
        hrefs_not_in_opf = []
        for archive_path in self.namelist:
            if archive_path.lower().endswith(
                (
                    ".html",
                    ".xhtml",
                    ".css",
                    ".jpg",
                    ".jpeg",
                    ".bmp",
                    ".gif",
                    ".png",
                    ".webp",
                    ".svg",
                    ".ttf",
                    ".otf",
                    ".js",
                    ".mp3",
                    ".mp4",
                    ".smil",
                )
            ):
                opf_href = get_relpath(self.opfpath, archive_path)
                if opf_href.lower() not in self.href_to_id:
                    hrefs_not_in_opf.append(opf_href)

        def allocate_id(href: str) -> str:
            basename = path.basename(href)
            if "A" <= basename[0] <= "Z" or "a" <= basename[0] <= "z":
                new_id = basename
            else:
                new_id = "x" + basename
            prefix, suffix = path.splitext(new_id)
            candidate_prefix = prefix
            index = 0
            while candidate_prefix + suffix in self.id_to_href:
                index += 1
                candidate_prefix = f"{prefix}_{index}"
            return candidate_prefix + suffix

        id_seed = getattr(self, "missing_manifest_id_seed", None)
        for href in hrefs_not_in_opf:
            new_id = allocate_id(id_seed or href)
            self.id_to_href[new_id] = href.lower()
            self.href_to_id[href.lower()] = new_id
            extension = path.splitext(href)[1].lower()
            mime = self.mime_map.get(extension, "text/plain")
            self.id_to_h_m_p[new_id] = (href, mime, "")

    def create_tgt_epub(self) -> zipfile.ZipFile:
        output_path = self.output_path
        self._logger.write(f"输出路径: {output_path}")
        target_path = path.join(
            output_path, self.epub_name.replace(".epub", self.output_suffix)
        )
        if path.exists(target_path):
            os.remove(target_path)
            self._logger.write(f"已删除同名输出文件: {target_path}")
        return zipfile.ZipFile(target_path, "w", zipfile.ZIP_STORED)

    def close_files(self) -> None:
        if self.epub:
            self.epub.close()
        if self.tgt_epub:
            self.tgt_epub.close()

    def fail_del_target(self) -> None:
        if self.file_write_path and os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)
            self._logger.write(f"删除临时文件: {self.file_write_path}")
        else:
            self._logger.write("临时文件不存在或已被删除。")


def epub_sources() -> list[str]:
    """保留旧独立脚本使用的命令行 EPUB 参数收集行为。"""
    if len(sys.argv) <= 1:
        return sys.argv
    sources = [path.dirname(sys.argv[0])]
    for source in sys.argv[1:]:
        if path.splitext(path.basename(source))[1].lower() == ".epub" and path.exists(
            source
        ):
            sources.append(source)
    return sources


def run_epub_cli(
    run_task: Callable[[str], object],
    prompt: str = "【使用说明】请把EPUB文件拖曳到本窗口上（输入'e'退出）: ",
) -> None:
    """运行旧独立脚本的交互入口。"""
    epub_src = input(prompt).strip("'").strip('"').strip()
    if epub_src.lower() == "e":
        print("程序已退出")
        sys.exit()
    if not os.path.isfile(epub_src):
        print("错误: 找不到指定的EPUB文件，请确认文件路径是否正确并重新输入！")
        return
    result = run_task(epub_src)
    if result == "skip":
        print("已跳过该文件")
    elif result == "e":
        print("操作失败，请检查日志！")
    else:
        print("操作成功！")
