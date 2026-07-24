"""EPUB 资源重写的共享执行引擎。

任务模块负责解析 OPF、决定资源目标名和提供少量格式差异；本模块负责
读取资源、改写链接、更新 OPF 并写出归档。它刻意不依赖 ``EpubWorkspace``：
EPUB 格式化任务需要保留对畸形 container/OPF 的既有容错路径。
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from os import path
from typing import Protocol

from python_backend.services.epub.task_base import (
    build_resource_path_maps,
    get_bookpath,
    split_file_reference,
)


class _Logger(Protocol):
    def write(self, message: str) -> None: ...


class RewriteTask(Protocol):
    """共享重写流程所需的任务状态。"""

    _logger: _Logger
    epub: zipfile.ZipFile
    tgt_epub: zipfile.ZipFile | None
    opf: str
    opfpath: str
    tocpath: str
    tocid: str
    toc_rn: dict[str, str]
    manifest_list: list[tuple[str, str, str, str]]
    text_list: list[tuple[str, str, str, str]]
    css_list: list[tuple[str, str, str, str]]
    image_list: list[tuple[str, str, str, str]]
    font_list: list[tuple[str, str, str, str]]
    audio_list: list[tuple[str, str, str, str]]
    video_list: list[tuple[str, str, str, str]]
    other_list: list[tuple[str, str, str, str, str]]
    errorLink_log: dict[str, list[tuple[str, str | None]]]

    def _read_xml_text(self, zip_path: str) -> str: ...

    def create_tgt_epub(self) -> zipfile.ZipFile: ...

    def close_files(self) -> None: ...


class EpubRunTask(RewriteTask, Protocol):
    """任务运行、报告和清理阶段所需的额外状态。"""

    output_suffix: str
    encrypted: bool
    errorOPF_log: list[tuple[str, str]]
    errorLink_log: dict[str, list[tuple[str, str | None]]]

    def set_output_path(self, output_path: str | None) -> None: ...

    def restructure(self) -> object: ...

    def fail_del_target(self) -> None: ...


TocRewriter = Callable[[RewriteTask, re.Match[str], Callable[..., str | None]], str]
OpfReferenceRewriter = Callable[[RewriteTask, re.Match[str]], str]
ManifestIdRewriter = Callable[[RewriteTask, str], str]
OpfTransformer = Callable[[RewriteTask, str], str]


@dataclass(frozen=True)
class RewritePolicy:
    """任务专属的、不会扩散到主流程条件分支的改写差异。"""

    mapped_css_imports: bool
    permissive_css_import_whitespace: bool = True
    normalize_css_import_to_quotes: bool = False
    strict_text_and_css_reads: bool = False
    write_toc_after_resources: bool = True
    prepare: Callable[[RewriteTask], None] | None = None
    rewrite_toc: TocRewriter | None = None
    rewrite_opf_reference: OpfReferenceRewriter | None = None
    output_manifest_id: ManifestIdRewriter | None = None
    transform_opf: OpfTransformer | None = None


@dataclass(frozen=True)
class EpubTaskPolicy:
    """EPUB 任务入口的显示、跳过和报告差异。"""

    action_name: str
    already_processed_message: str
    skip_when_encrypted: bool | None = None
    encryption_state_skip_message: str = ""
    include_corrected_path: bool = False
    invalid_idref_advice: str = (
        "措施: 请自行检查spine内的itemref节点并手动修改，确保引用的ID存在于manifest的item项。\n"
        "      （大小写不一致也会导致引用无效。）"
    )


RESOURCE_DIRECTORIES = {
    "text": "Text",
    "css": "Styles",
    "image": "Images",
    "font": "Fonts",
    "audio": "Audio",
    "video": "Video",
    "other": "Misc",
}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".svg")
HREF_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS[:-1]
FONT_EXTENSIONS = (".ttf", ".otf")


class EpubRewriteEngine:
    """执行三个 EPUB 任务共享的重写生命周期。"""

    def __init__(self, task: RewriteTask, policy: RewritePolicy) -> None:
        self.task = task
        self.policy = policy
        self.path_maps: dict[str, dict[str, str]] = {}
        self.lower_to_original: dict[str, str] = {}

    def run(self) -> None:
        if self.policy.prepare:
            self.policy.prepare(self.task)
        self.task.tgt_epub = self.task.create_tgt_epub()
        self._write_bootstrap_files()
        self.path_maps, self.lower_to_original = build_resource_path_maps(
            self.task.opfpath,
            {
                "text": self.task.text_list,
                "css": self.task.css_list,
                "image": self.task.image_list,
                "font": self.task.font_list,
                "audio": self.task.audio_list,
                "video": self.task.video_list,
                "other": self.task.other_list,
            },
        )
        if self.task.tocpath and not self.policy.write_toc_after_resources:
            self._write_toc()
        self._write_text_resources()
        self._write_css_resources()
        self._write_binary_resources()
        if self.task.tocpath and self.policy.write_toc_after_resources:
            self._write_toc()
        self._write_opf()
        self.task.close_files()

    @property
    def target(self) -> zipfile.ZipFile:
        assert self.task.tgt_epub is not None
        return self.task.tgt_epub

    def _write_bootstrap_files(self) -> None:
        mimetype = self.task.epub.read("mimetype")
        self.target.writestr("mimetype", mimetype, zipfile.ZIP_DEFLATED)
        container = self.task._read_xml_text("META-INF/container.xml")
        container = re.sub(
            r'<rootfile[^>]*media-type="application/oebps-[^>]*/>',
            r'<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>',
            container,
        )
        self.target.writestr(
            "META-INF/container.xml", container.encode("utf-8"), zipfile.ZIP_DEFLATED
        )

    def _check_link(
        self, filename: str, book_path: str, href: str, target_id: str = ""
    ) -> str | None:
        if href == "" or href.startswith(("http://", "https://", "res:/", "file:/", "data:")):
            return None
        original = self.lower_to_original.get(book_path.lower())
        if original is None:
            self.task.errorLink_log.setdefault(filename, []).append((href + target_id, None))
            return None
        if book_path != original:
            self.task.errorLink_log.setdefault(filename, []).append((href + target_id, original))
        return original

    def _resolve_link(
        self, filename: str, reference: str, target_id: str = ""
    ) -> str | None:
        return self._check_link(
            filename, get_bookpath(reference, filename), reference, target_id
        )

    def _write_toc(self) -> None:
        if self.policy.rewrite_toc is None:
            return
        toc = self.task.epub.read(self.task.tocpath).decode("utf-8")
        toc = re.sub(
            r"src=([\'\"])(.*?)(\1)",
            lambda match: self.policy.rewrite_toc(self.task, match, self._check_link),
            toc,
        )
        self.target.writestr("OEBPS/toc.ncx", toc.encode("utf-8"), zipfile.ZIP_DEFLATED)

    def _read_text_resource(self, archive_path: str, resource_type: str) -> str:
        try:
            return self.task.epub.read(archive_path).decode("utf-8")
        except UnicodeDecodeError as error:
            if self.policy.strict_text_and_css_reads:
                label = "XHTML" if resource_type == "text" else "CSS"
                raise RuntimeError(
                    f"{label}资源无法按UTF-8读取，可能仍处于加密状态: {archive_path}"
                ) from error
            raise

    def _write_text_resources(self) -> None:
        for source_path, new_name in self.path_maps["text"].items():
            text = self._read_text_resource(source_path, "text")
            if not text.startswith("<?xml"):
                text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
            if not re.match(r"(?s).*<!DOCTYPE html", text):
                text = re.sub(
                    r"(<\?xml.*?>)\n*",
                    r'\1\n<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN"\n  "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">\n',
                    text,
                    count=1,
                )
            text = self._rewrite_xhtml(text, source_path)
            self.target.writestr(
                f"OEBPS/Text/{new_name}", text.encode("utf-8"), zipfile.ZIP_DEFLATED
            )

    def _rewrite_xhtml(self, text: str, source_path: str) -> str:
        def rewrite_href(match: re.Match[str]) -> str:
            reference, fragment = split_file_reference(match.group(3))
            book_path = self._resolve_link(source_path, reference, fragment)
            if not book_path:
                return match.group()
            suffix = reference.lower()
            if suffix.endswith(HREF_IMAGE_EXTENSIONS):
                resource_type, directory = "image", "../Images/"
            elif suffix.endswith(".css"):
                resource_type, directory = "css", "../Styles/"
            elif suffix.endswith((".xhtml", ".html")):
                resource_type, directory = "text", ""
            else:
                return match.group()
            name = self.path_maps.get(resource_type, {}).get(book_path)
            if name is None:
                return match.group()
            if resource_type == "css":
                return f'<link href="{directory}{name}{fragment}" type="text/css" rel="stylesheet"/>'
            return match.group(1) + directory + name + fragment + match.group(4)

        def rewrite_src(match: re.Match[str]) -> str:
            reference, fragment = split_file_reference(match.group(3))
            book_path = self._resolve_link(source_path, reference, fragment)
            if not book_path:
                return match.group()
            suffix = reference.lower()
            if suffix.endswith(IMAGE_EXTENSIONS):
                resource_type, directory = "image", "../Images/"
            elif suffix.endswith(".mp3"):
                resource_type, directory = "audio", "../Audio/"
            elif suffix.endswith(".mp4"):
                resource_type, directory = "video", "../Video/"
            elif suffix.endswith(".js"):
                resource_type, directory = "other", "../Misc/"
            else:
                return match.group()
            name = self.path_maps.get(resource_type, {}).get(book_path)
            return match.group() if name is None else match.group(1) + directory + name + fragment + match.group(4)

        def rewrite_url(match: re.Match[str]) -> str:
            reference, fragment = split_file_reference(match.group(2))
            book_path = self._resolve_link(source_path, reference, fragment)
            if not book_path:
                return match.group()
            suffix = reference.lower()
            if suffix.endswith(FONT_EXTENSIONS):
                resource_type, directory = "font", "../Fonts/"
            elif suffix.endswith(IMAGE_EXTENSIONS):
                resource_type, directory = "image", "../Images/"
            else:
                return match.group()
            name = self.path_maps.get(resource_type, {}).get(book_path)
            return match.group() if name is None else match.group(1) + directory + name + fragment + match.group(3)

        text = re.sub(r"(<[^>]*href=([\'\"]))(.*?)(\2[^>]*>)", rewrite_href, text)
        def rewrite_poster(match: re.Match[str]) -> str:
            reference, fragment = split_file_reference(match.group(3))
            book_path = self._resolve_link(source_path, reference, fragment)
            if not book_path or not reference.lower().endswith(IMAGE_EXTENSIONS):
                return match.group()
            name = self.path_maps["image"].get(book_path)
            return match.group() if name is None else match.group(1) + "../Images/" + name + fragment + match.group(4)

        text = re.sub(r"(<[^>]* src=([\'\"]))(.*?)(\2[^>]*>)", rewrite_src, text)
        text = re.sub(r"(<[^>]* poster=([\'\"]))(.*?)(\2[^>]*>)", rewrite_poster, text)
        for attribute in ("placeholder", "activestate", "zy-cover-pic"):
            text = re.sub(
                rf"(<[^>]* {attribute}=([\'\"]))(.*?)(\2[^>]*>)", rewrite_src, text
            )
        return re.sub(r"(url\([\'\"]?)(.*?)([\'\"]?\))", rewrite_url, text)

    def _write_css_resources(self) -> None:
        for source_path, new_name in self.path_maps["css"].items():
            try:
                css = self._read_text_resource(source_path, "css")
            except (KeyError, UnicodeDecodeError):
                if self.policy.strict_text_and_css_reads:
                    raise
                continue
            css = self._rewrite_css(css, source_path)
            self.target.writestr(
                f"OEBPS/Styles/{new_name}", css.encode("utf-8"), zipfile.ZIP_DEFLATED
            )

    def _rewrite_css(self, css: str, source_path: str) -> str:
        def rewrite_import(match: re.Match[str]) -> str:
            raw_reference = match.group(2) if match.group(2) else match.group(3)
            reference, fragment = split_file_reference(raw_reference)
            if not reference.lower().endswith(".css"):
                return match.group()
            name = path.basename(reference)
            if self.policy.mapped_css_imports:
                book_path = self._resolve_link(source_path, reference, fragment)
                if not book_path:
                    return match.group()
                name = self.path_maps["css"].get(book_path, name)
            if self.policy.normalize_css_import_to_quotes or match.group(2):
                return f'@import "{name}{fragment}"'
            return f'@import url("{name}{fragment}")'

        def rewrite_url(match: re.Match[str]) -> str:
            reference, fragment = split_file_reference(match.group(2))
            book_path = self._resolve_link(source_path, reference, fragment)
            if not book_path:
                return match.group()
            suffix = reference.lower()
            if suffix.endswith(FONT_EXTENSIONS):
                resource_type, directory = "font", "../Fonts/"
            elif suffix.endswith(IMAGE_EXTENSIONS):
                resource_type, directory = "image", "../Images/"
            else:
                return match.group()
            name = self.path_maps.get(resource_type, {}).get(book_path)
            return match.group() if name is None else match.group(1) + directory + name + fragment + match.group(3)

        whitespace = "+" if self.policy.permissive_css_import_whitespace else ""
        css = re.sub(
            rf"@import {whitespace}([\'\"])(.*?)\1|@import {whitespace}url\([\'\"]?(.*?)[\'\"]?\)",
            rewrite_import,
            css,
        )
        return re.sub(r"(url\([\'\"]?)(.*?)([\'\"]?\))", rewrite_url, css)

    def _write_binary_resources(self) -> None:
        for resource_type in ("image", "font", "audio", "video", "other"):
            for source_path, new_name in self.path_maps[resource_type].items():
                try:
                    content = self.task.epub.read(source_path)
                except KeyError:
                    continue
                self.target.writestr(
                    f"OEBPS/{RESOURCE_DIRECTORIES[resource_type]}/{new_name}",
                    content,
                    zipfile.ZIP_DEFLATED,
                )

    def _write_opf(self) -> None:
        manifest = "<manifest>"
        for item_id, href, mime, properties in self.task.manifest_list:
            book_path = get_bookpath(href, self.task.opfpath)
            output_id = item_id
            if self.policy.output_manifest_id:
                output_id = self.policy.output_manifest_id(self.task, item_id)
            prop = f' properties="{properties}"' if properties else ""
            if item_id == self.task.tocid:
                manifest += f'\n    <item id="{output_id}" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
                continue
            resource_type = self._resource_type(href, mime)
            name = self.path_maps[resource_type][book_path]
            manifest += (
                f'\n    <item id="{output_id}" href="{RESOURCE_DIRECTORIES[resource_type]}/{name}" '
                f'media-type="{mime}"{prop}/>'
            )
        manifest += "\n  </manifest>"
        opf = re.sub(r"(?s)<manifest.*?>.*?</manifest>", manifest, self.task.opf, count=1)
        if self.policy.transform_opf:
            opf = self.policy.transform_opf(self.task, opf)
        if self.policy.rewrite_opf_reference:
            opf = re.sub(
                r"(<reference[^>]*href=([\'\"]))(.*?)(\2[^>]*/>)",
                lambda match: self.policy.rewrite_opf_reference(self.task, match),
                opf,
            )
        self.target.writestr("OEBPS/content.opf", opf.encode("utf-8"), zipfile.ZIP_DEFLATED)

    def _resource_type(self, href: str, mime: str) -> str:
        if mime == "application/xhtml+xml":
            return "text"
        if mime == "text/css":
            return "css"
        if "image/" in mime:
            return "image"
        if "font/" in mime or href.lower().endswith((".ttf", ".otf", ".woff")):
            return "font"
        if "audio/" in mime:
            return "audio"
        if "video/" in mime:
            return "video"
        return "other"


def run_epub_task(
    epub_src: str,
    output_path: str | None,
    task_factory: type[EpubRunTask],
    logger: _Logger,
    policy: EpubTaskPolicy,
) -> int | str | Exception:
    """运行共享 EPUB 任务入口，并保留任务策略定义的可见行为。"""
    task: EpubRunTask | None = None
    try:
        logger.write(f"\n正在尝试{policy.action_name}EPUB: {epub_src}")
        if epub_src.lower().endswith(task_factory.output_suffix.lower()):
            logger.write(policy.already_processed_message)
            return "skip"

        task = task_factory(epub_src)
        task.set_output_path(output_path)
        if (
            policy.skip_when_encrypted is not None
            and task.encrypted != policy.skip_when_encrypted
        ):
            logger.write(policy.encryption_state_skip_message)
            task.close_files()
            task.fail_del_target()
            return "skip"
        if task.restructure() == "skip":
            return "skip"
        _write_task_report(task, logger, policy)
    except Exception as error:
        logger.write(f"{epub_src} {policy.action_name}EPUB失败: {error}")
        if task is not None:
            task.close_files()
            task.fail_del_target()
        return error
    logger.write(f"{epub_src} {policy.action_name}EPUB成功")
    return 0


def _write_task_report(
    task: EpubRunTask, logger: _Logger, policy: EpubTaskPolicy
) -> None:
    link_errors = task.errorLink_log.copy()
    for file_path, errors in task.errorLink_log.items():
        if file_path.lower().endswith(".css"):
            filtered = [error for error in errors if error[1] is not None]
            if filtered:
                link_errors[file_path] = filtered
            else:
                link_errors.pop(file_path, None)

    if task.errorOPF_log:
        logger.write("-------在 OPF文件 发现问题------:")
        for error_type, error_value in task.errorOPF_log:
            if error_type == "duplicate_id":
                logger.write(f"问题: 发现manifest节点内部存在重复ID {error_value} !!!")
                logger.write("措施: 已自动清除重复ID对应的manifest项。")
            elif error_type == "invalid_idref":
                logger.write(f"问题: 发现spine节点内部存在无效引用ID {error_value} !!!")
                logger.write(policy.invalid_idref_advice)
            elif error_type == "xhtml_not_in_spine":
                logger.write(
                    f"问题: 发现ID为 {error_value} 的文件manifest中登记为application/xhtml+xml类型，但不被spine节点的项所引用"
                )
                logger.write(
                    "措施: 自行检查该文件是否需要被spine引用。部分阅读器中，如果存在xhtml文件不被spine引用，可能导致epub无法打开。"
                )
            elif error_type == "opf_malformed_fallback_used":
                logger.write("问题: OPF 存在畸形XML，已启用正则降级解析。")
                logger.write("措施: 建议手工修复 OPF 后再重试，以避免遗漏元数据。")

    for file_path, errors in link_errors.items():
        logger.write(f"-----在 {path.basename(file_path)} 发现问题链接-----:")
        for href, correct_path in errors:
            if correct_path is None:
                logger.write(f"链接: {href}\n问题: 未能找到对应文件！！！")
            elif policy.include_corrected_path:
                logger.write(
                    f"链接: {href}\n问题: 与实际文件名大小写不一致！\n措施: 程序已自动纠正链接为: {correct_path}。"
                )
            else:
                logger.write(
                    f"链接: {href}\n问题: 与实际文件名大小写不一致！\n措施: 程序已自动纠正链接。"
                )
