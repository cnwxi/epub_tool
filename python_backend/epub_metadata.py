from __future__ import annotations

import os
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree


TOOL_META_NAME = "generator"
TOOL_META_CONTENT = "Epub Tool"
TOOL_META_LINE = f'<meta name="{TOOL_META_NAME}" content="{TOOL_META_CONTENT}" />'


def mark_epub_generated_by_tool(epub_path: str | os.PathLike[str]) -> bool:
    epub_file = Path(epub_path)
    if not epub_file.exists():
        raise FileNotFoundError(f"EPUB输出文件不存在: {epub_file}")

    temp_path: Path | None = None
    try:
        with zipfile.ZipFile(epub_file, "r") as source:
            opf_path = find_opf_path(source)
            opf_text = decode_xml(source.read(opf_path))
            next_opf_text, changed = add_tool_meta_to_opf(opf_text)
            if not changed:
                return False
            temp_path = build_epub_with_replaced_opf(
                epub_file,
                source,
                opf_path,
                next_opf_text,
            )
        os.replace(temp_path, epub_file)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
    return True


def find_opf_path(epub: zipfile.ZipFile) -> str:
    names = epub.namelist()
    container_path = next(
        (name for name in names if name.lower() == "meta-inf/container.xml"),
        None,
    )
    if container_path:
        container_text = decode_xml(epub.read(container_path))
        opf_path = find_opf_path_from_container(container_text)
        if opf_path and opf_path in names:
            return opf_path

    for name in names:
        if name.lower().endswith(".opf"):
            return name

    raise RuntimeError("无法发现opf文件")


def find_opf_path_from_container(container_text: str) -> str | None:
    try:
        root = ElementTree.fromstring(container_text)
        for element in root.iter():
            if local_name(element.tag) == "rootfile":
                full_path = element.attrib.get("full-path")
                if full_path and full_path.lower().endswith(".opf"):
                    return full_path
    except ElementTree.ParseError:
        pass

    match = re.search(
        r'<rootfile[^>]*\bfull-path\s*=\s*([\'"])(?P<path>.*?\.opf)\1',
        container_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group("path") if match else None


def add_tool_meta_to_opf(opf_text: str) -> tuple[str, bool]:
    if has_tool_meta(opf_text):
        return opf_text, False

    updated = add_meta_to_existing_metadata(opf_text)
    if updated is not None:
        return updated, True

    updated = expand_self_closing_metadata(opf_text)
    if updated is not None:
        return updated, True

    updated = add_metadata_to_package(opf_text)
    if updated is not None:
        return updated, True

    raise RuntimeError("content.opf 缺少 package 节点，无法写入工具元数据")


def has_tool_meta(opf_text: str) -> bool:
    try:
        root = ElementTree.fromstring(opf_text)
        for element in root.iter():
            if local_name(element.tag) != "meta":
                continue
            if (
                element.attrib.get("name") == TOOL_META_NAME
                and element.attrib.get("content") == TOOL_META_CONTENT
            ):
                return True
    except ElementTree.ParseError:
        pass

    meta_pattern = re.compile(
        r"<(?:[A-Za-z_][\w.-]*:)?meta\b(?=[^>]*\bname\s*=\s*([\"'])"
        + re.escape(TOOL_META_NAME)
        + r"\1)(?=[^>]*\bcontent\s*=\s*([\"'])"
        + re.escape(TOOL_META_CONTENT)
        + r"\2)[^>]*/?>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return bool(meta_pattern.search(opf_text))


def add_meta_to_existing_metadata(opf_text: str) -> str | None:
    pattern = re.compile(
        r"(?is)<(?P<prefix>[A-Za-z_][\w.-]*:)?metadata\b[^>]*>"
        r"(?P<body>.*?)"
        r"(?P<closing>[ \t]*</(?P=prefix)metadata>)"
    )
    match = pattern.search(opf_text)
    if not match:
        return None

    closing = match.group("closing")
    metadata_indent = re.match(r"[ \t]*", closing).group(0)
    meta_indent = metadata_indent + "  "
    insertion = f"\n{meta_indent}{TOOL_META_LINE}\n"
    insert_at = match.start("closing")
    return opf_text[:insert_at].rstrip() + insertion + opf_text[insert_at:]


def expand_self_closing_metadata(opf_text: str) -> str | None:
    pattern = re.compile(
        r"(?is)<(?P<prefix>[A-Za-z_][\w.-]*:)?metadata\b(?P<attrs>[^>]*)/>"
    )
    match = pattern.search(opf_text)
    if not match:
        return None

    prefix = match.group("prefix") or ""
    attrs = match.group("attrs").rstrip()
    replacement = (
        f"<{prefix}metadata{attrs}>\n"
        f"    {TOOL_META_LINE}\n"
        f"  </{prefix}metadata>"
    )
    return opf_text[: match.start()] + replacement + opf_text[match.end() :]


def add_metadata_to_package(opf_text: str) -> str | None:
    pattern = re.compile(r"(?is)<(?P<prefix>[A-Za-z_][\w.-]*:)?package\b[^>]*>")
    match = pattern.search(opf_text)
    if not match:
        return None

    prefix = match.group("prefix") or ""
    metadata_block = (
        f"\n  <{prefix}metadata>\n"
        f"    {TOOL_META_LINE}\n"
        f"  </{prefix}metadata>"
    )
    return opf_text[: match.end()] + metadata_block + opf_text[match.end() :]


def build_epub_with_replaced_opf(
    epub_file: Path,
    source: zipfile.ZipFile,
    opf_path: str,
    opf_text: str,
) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        delete=False,
        dir=str(epub_file.parent),
        suffix=".tmp",
    )
    temp_path = Path(temp_file.name)
    temp_file.close()

    try:
        with zipfile.ZipFile(temp_path, "w") as target:
            for info in source.infolist():
                content = (
                    opf_text.encode("utf-8")
                    if info.filename == opf_path
                    else source.read(info)
                )
                target.writestr(info, content)
        return temp_path
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def decode_xml(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    if ":" in tag:
        return tag.rsplit(":", 1)[1]
    return tag
