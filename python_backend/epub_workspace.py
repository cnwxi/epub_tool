from __future__ import annotations

import os
import posixpath
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree


MIMETYPE = b"application/epub+zip"
MAX_MEMBER_SIZE = 128 * 1024 * 1024
MAX_TOTAL_SIZE = 768 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000


class LoggerLike(Protocol):
    def write(self, text: str) -> None: ...


def normalize_member_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    if (
        not normalized
        or normalized in {".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
        or PurePosixPath(normalized).is_absolute()
    ):
        raise ValueError(f"EPUB 包含不安全路径: {value}")
    return normalized


def resolve_reference(document_path: str, reference: str) -> str | None:
    parts = urlsplit(reference)
    if parts.scheme or parts.netloc or not parts.path:
        return None
    decoded = unquote(parts.path)
    return normalize_member_path(posixpath.join(posixpath.dirname(document_path), decoded))


def replace_reference_path(reference: str, document_path: str, target_path: str) -> str:
    parts = urlsplit(reference)
    relative = posixpath.relpath(target_path, posixpath.dirname(document_path) or ".")
    return urlunsplit((parts.scheme, parts.netloc, relative, parts.query, parts.fragment))


def media_type_for(path: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }.get(PurePosixPath(path).suffix.lower(), "application/octet-stream")


@dataclass(slots=True)
class EpubWorkspace:
    input_path: Path
    members: dict[str, bytes]
    opf_path: str

    @classmethod
    def load(
        cls, input_path: str | Path, *, logger: LoggerLike | None = None
    ) -> "EpubWorkspace":
        path = Path(input_path)
        members: dict[str, bytes] = {}
        total_size = 0
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"无效 EPUB ZIP: {exc}") from exc
        with archive:
            entries = archive.infolist()
            mimetype_entry = next(
                (entry for entry in entries if entry.filename == "mimetype"), None
            )
            if mimetype_entry is None:
                raise ValueError("EPUB 缺少 mimetype")
            if (
                entries[0].filename != "mimetype"
                or mimetype_entry.compress_type != zipfile.ZIP_STORED
            ) and logger is not None:
                logger.write(
                    "输入 EPUB 的 mimetype 不是未压缩的首个 ZIP 成员；"
                    "本次允许兼容读取，输出时将自动规范化。"
                )
            for info in entries:
                name = normalize_member_path(info.filename)
                if info.is_dir():
                    continue
                if name in members:
                    raise ValueError(f"EPUB 包含重复成员: {name}")
                if info.file_size > MAX_MEMBER_SIZE:
                    raise ValueError(f"EPUB 成员过大: {name}")
                total_size += info.file_size
                if total_size > MAX_TOTAL_SIZE:
                    raise ValueError("EPUB 解压后总大小超过安全限制")
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ValueError(f"EPUB 成员压缩比异常: {name}")
                members[name] = archive.read(info)
        if members.get("mimetype") != MIMETYPE:
            raise ValueError("EPUB mimetype 缺失或内容不正确")
        container = members.get("META-INF/container.xml")
        if container is None:
            raise ValueError("EPUB 缺少 META-INF/container.xml")
        try:
            root = ElementTree.fromstring(container)
        except ElementTree.ParseError as exc:
            raise ValueError(f"container.xml 无效: {exc}") from exc
        rootfile = next((item for item in root.iter() if item.tag.rsplit("}", 1)[-1] == "rootfile"), None)
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("container.xml 缺少 OPF rootfile")
        opf_path = normalize_member_path(rootfile.get("full-path", ""))
        if opf_path not in members:
            raise ValueError(f"EPUB 缺少 OPF 文件: {opf_path}")
        return cls(path, members, opf_path)

    def write(self, output_path: str | Path, *, logger: LoggerLike | None = None) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file():
                raise IsADirectoryError(f"输出路径已存在且不是文件: {target}")
            target.unlink()
            if logger is not None:
                logger.write(f"已删除同名输出文件: {target}")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w") as archive:
                archive.writestr("mimetype", MIMETYPE, compress_type=zipfile.ZIP_STORED)
                for name, data in self.members.items():
                    if name == "mimetype":
                        continue
                    archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(temporary) as check:
                first = check.infolist()[0]
                if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
                    raise RuntimeError("生成的 EPUB mimetype 结构无效")
                if check.testzip() is not None:
                    raise RuntimeError("生成的 EPUB ZIP 校验失败")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
