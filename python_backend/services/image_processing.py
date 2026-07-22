from __future__ import annotations

import io
import posixpath
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from PIL import Image, ImageOps, UnidentifiedImageError

from python_backend.epub_workspace import (
    EpubWorkspace,
    media_type_for,
    replace_reference_path,
    resolve_reference,
)


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
IMAGE_FORMAT_BY_EXTENSION = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
    ".bmp": "BMP",
}
MAX_IMAGE_PIXELS = 40_000_000
BYTES_PER_MEBIBYTE = 1024 * 1024
REFERENCE_ATTRIBUTES_RE = re.compile(
    rb"(?P<prefix>\b(?:src|href|xlink:href|poster)\s*=\s*[\"'])(?P<value>[^\"']+)(?P<suffix>[\"'])",
    re.IGNORECASE,
)
SRCSET_RE = re.compile(rb"(?P<prefix>\bsrcset\s*=\s*[\"'])(?P<value>[^\"']+)(?P<suffix>[\"'])", re.IGNORECASE)
CSS_URL_RE = re.compile(rb"(?P<prefix>url\(\s*[\"']?)(?P<value>[^\"')]+)(?P<suffix>[\"']?\s*\))", re.IGNORECASE)


def format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / BYTES_PER_MEBIBYTE:.2f} MB"


def _converted_path(source: str, extension: str, existing: set[str]) -> str:
    base = str(PurePosixPath(source).with_suffix(extension))
    candidate = base
    index = 2
    while candidate in existing and candidate != source:
        stem = str(PurePosixPath(base).with_suffix(""))
        candidate = f"{stem}-{index}{extension}"
        index += 1
    return candidate


def _rewrite_one(reference: str, document_path: str, replacements: dict[str, str]) -> str:
    try:
        resolved = resolve_reference(document_path, reference)
    except ValueError:
        return reference
    target = replacements.get(resolved or "")
    if not target:
        return reference
    rewritten = replace_reference_path(reference, document_path, target)
    path, marker, suffix = rewritten.partition("?")
    if not marker:
        path, marker, suffix = rewritten.partition("#")
        return quote(path, safe="/.:@~!$&'()*+,;=-_") + (marker + suffix if marker else "")
    return quote(path, safe="/.:@~!$&'()*+,;=-_") + marker + suffix


def _rewrite_document(data: bytes, document_path: str, replacements: dict[str, str]) -> bytes:
    def replace_attribute(match: re.Match[bytes]) -> bytes:
        value = match.group("value").decode("utf-8", "surrogateescape")
        rewritten = _rewrite_one(value, document_path, replacements)
        return match.group("prefix") + rewritten.encode("utf-8", "surrogateescape") + match.group("suffix")

    def replace_srcset(match: re.Match[bytes]) -> bytes:
        value = match.group("value").decode("utf-8", "surrogateescape")
        entries = []
        for entry in value.split(","):
            pieces = entry.strip().split(maxsplit=1)
            if pieces:
                pieces[0] = _rewrite_one(pieces[0], document_path, replacements)
            entries.append(" ".join(pieces))
        return match.group("prefix") + ", ".join(entries).encode("utf-8", "surrogateescape") + match.group("suffix")

    result = REFERENCE_ATTRIBUTES_RE.sub(replace_attribute, data)
    result = SRCSET_RE.sub(replace_srcset, result)
    return CSS_URL_RE.sub(replace_attribute, result)


def _update_opf(workspace: EpubWorkspace, replacements: dict[str, str]) -> None:
    data = _rewrite_document(workspace.members[workspace.opf_path], workspace.opf_path, replacements)
    opf_dir = posixpath.dirname(workspace.opf_path)
    for old, new in replacements.items():
        old_href = posixpath.relpath(old, opf_dir or ".").encode()
        new_href = posixpath.relpath(new, opf_dir or ".").encode()
        data = data.replace(old_href, new_href)
        old_type = media_type_for(old).encode()
        new_type = media_type_for(new).encode()
        if old_type != new_type:
            pattern = re.compile(rb"(<item\b[^>]*\bhref=[\"']" + re.escape(new_href) + rb"[\"'][^>]*\bmedia-type=[\"'])" + re.escape(old_type) + rb"([\"'])", re.IGNORECASE)
            data = pattern.sub(rb"\1" + new_type + rb"\2", data)
    workspace.members[workspace.opf_path] = data


def process_images(
    input_file: str,
    output_path: str,
    *,
    mode: str,
    quality: int,
    webp_quality: int | None = None,
    png_to_jpg: bool = False,
    logger,
) -> int:
    workspace = EpubWorkspace.load(input_file, logger=logger)
    replacements: dict[str, str] = {}
    processed = kept = failed = saved = 0
    existing = set(workspace.members)
    for source, original in list(workspace.members.items()):
        extension = PurePosixPath(source).suffix.lower()
        if extension not in IMAGE_EXTENSIONS:
            continue
        if mode == "webp" and extension == ".webp":
            kept += 1
            continue
        try:
            with Image.open(io.BytesIO(original)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过安全限制")
                detected_format = (image.format or "").upper()
                image.load()
                image = ImageOps.exif_transpose(image)
                target_extension = extension
                target_format = (
                    "JPEG"
                    if detected_format == "JPG"
                    else detected_format or IMAGE_FORMAT_BY_EXTENSION[extension]
                )
                save_options: dict[str, object] = {"optimize": True}
                if mode == "webp":
                    target_extension = ".webp"
                    target_format = "WEBP"
                    save_options["quality"] = quality
                elif extension in {".jpg", ".jpeg", ".webp"}:
                    save_options["quality"] = webp_quality if extension == ".webp" and webp_quality is not None else quality
                elif extension == ".png" and png_to_jpg and "A" not in image.getbands():
                    target_extension = ".jpg"
                    target_format = "JPEG"
                    save_options["quality"] = quality
                elif extension == ".bmp":
                    kept += 1
                    continue
                if target_format == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                if "icc_profile" in image.info:
                    save_options["icc_profile"] = image.info["icc_profile"]
                buffer = io.BytesIO()
                image.save(buffer, format=target_format, **save_options)
                converted = buffer.getvalue()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            failed += 1
            logger.write(f"跳过无法处理的图片 {source}: {exc}")
            continue
        if mode == "compress" and len(converted) >= len(original):
            kept += 1
            continue
        target = source
        if target_extension != extension:
            target = _converted_path(source, target_extension, existing)
            replacements[source] = target
            del workspace.members[source]
            existing.discard(source)
            existing.add(target)
        workspace.members[target] = converted
        processed += 1
        saved += len(original) - len(converted)
    if replacements:
        for name, data in list(workspace.members.items()):
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in {".opf", ".xhtml", ".html", ".htm", ".css", ".svg", ".ncx"}:
                workspace.members[name] = _rewrite_document(data, name, replacements)
        _update_opf(workspace, replacements)
    workspace.write(output_path, logger=logger)
    logger.write(
        f"图片处理完成：处理 {processed}，保留 {kept}，失败 {failed}，"
        f"节省 {format_size_mb(saved)}"
    )
    return 0
