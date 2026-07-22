from __future__ import annotations

import codecs
import re
from pathlib import Path, PurePosixPath

from opencc import OpenCC

from python_backend.epub_workspace import EpubWorkspace
from python_backend.services.log import logwriter


logger = logwriter()
TAG_RE = re.compile(r"(?s)(<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[^>]+>)")
VISIBLE_ATTRIBUTE_RE = re.compile(
    r"(?P<prefix>\b(?:alt|title)\s*=\s*)(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE,
)
XML_ENCODING_RE = re.compile(
    r"<\?xml\b[^>]*\bencoding\s*=\s*([\"'])(?P<encoding>[^\"']+)\1",
    re.IGNORECASE,
)


def _detect_xml_encoding(data: bytes) -> str:
    if data.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
        return "utf-32"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if data.startswith(b"\x00<\x00?\x00x"):
        return "utf-16-be"
    if data.startswith(b"<\x00?\x00x\x00"):
        return "utf-16-le"
    declaration = XML_ENCODING_RE.search(data[:1024].decode("latin-1"))
    return declaration.group("encoding") if declaration else "utf-8"


def _decode_xml(data: bytes) -> str:
    encoding = _detect_xml_encoding(data)
    try:
        return data.decode(encoding)
    except LookupError as exc:
        raise ValueError(f"XML 声明了不支持的编码: {encoding}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError(f"无法按 {encoding} 解码 XML: {exc}") from exc


def _as_utf8_xml(text: str) -> bytes:
    normalized = XML_ENCODING_RE.sub(
        lambda match: match.group(0).replace(match.group("encoding"), "UTF-8"),
        text,
        count=1,
    )
    return normalized.encode("utf-8")


def _convert_xml(data: bytes, converter: OpenCC) -> tuple[bytes, bool]:
    pieces = TAG_RE.split(_decode_xml(data))
    blocked_depth = 0
    output: list[str] = []
    for piece in pieces:
        if piece.startswith("<"):
            lowered = piece.lstrip().lower()
            if lowered.startswith(("<script", "<style")) and not lowered.startswith(("</script", "</style")):
                blocked_depth += 1
            if blocked_depth == 0 and not lowered.startswith(("<!--", "<![cdata[")):
                def replace_attribute(match: re.Match[str]) -> str:
                    converted = converter.convert(match.group("value"))
                    return match.group("prefix") + match.group("quote") + converted + match.group("quote")

                piece = VISIBLE_ATTRIBUTE_RE.sub(replace_attribute, piece)
            output.append(piece)
            if lowered.startswith(("</script", "</style")):
                blocked_depth = max(0, blocked_depth - 1)
            continue
        if blocked_depth or not piece:
            output.append(piece)
            continue
        output.append(converter.convert(piece))
    converted = _as_utf8_xml("".join(output))
    return converted, converted != data


def run(input_file: str, output_dir: str | None, *, options: dict[str, object]) -> int:
    direction = str(options["direction"])
    converter = OpenCC("s2t" if direction == "s2t" else "t2s")
    workspace = EpubWorkspace.load(input_file, logger=logger)
    changed_files = 0
    for name, data in list(workspace.members.items()):
        if PurePosixPath(name).suffix.lower() not in {".xhtml", ".html", ".htm", ".opf", ".ncx"}:
            continue
        converted, changed = _convert_xml(data, converter)
        if changed:
            workspace.members[name] = converted
            changed_files += 1
    suffix = "traditional" if direction == "s2t" else "simplified"
    target_dir = Path(output_dir) if output_dir else Path(input_file).parent
    workspace.write(target_dir / f"{Path(input_file).stem}_{suffix}.epub", logger=logger)
    logger.write(f"简繁转换完成：更新 {changed_files} 个文本文件")
    return 0
