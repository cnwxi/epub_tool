from __future__ import annotations

import io
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
OPF_ITEM_RE = re.compile(rb"(?P<prefix><item\b)(?P<attributes>[^>]*)(?P<suffix>/?>)", re.IGNORECASE)
OPF_HREF_RE = re.compile(
    rb"(?P<prefix>\bhref\s*=\s*[\"'])(?P<value>[^\"']+)(?P<suffix>[\"'])",
    re.IGNORECASE,
)
OPF_MEDIA_TYPE_RE = re.compile(
    rb"(?P<prefix>\bmedia-type\s*=\s*[\"'])(?P<value>[^\"']+)(?P<suffix>[\"'])",
    re.IGNORECASE,
)


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
        rewritten: list[str] = []
        position = 0
        while position < len(value):
            while position < len(value) and (
                value[position].isspace() or value[position] == ","
            ):
                rewritten.append(value[position])
                position += 1
            if position >= len(value):
                break

            url_start = position
            is_data_uri = value[position:].lower().startswith("data:")
            while position < len(value) and not value[position].isspace() and (
                is_data_uri or value[position] != ","
            ):
                position += 1
            raw_url = value[url_start:position]
            url = raw_url.rstrip(",")
            rewritten.append(_rewrite_one(url, document_path, replacements))
            rewritten.append(raw_url[len(url) :])
            if len(url) != len(raw_url):
                continue

            parenthesis_depth = 0
            while position < len(value):
                char = value[position]
                rewritten.append(char)
                position += 1
                if char == "(":
                    parenthesis_depth += 1
                elif char == ")":
                    parenthesis_depth = max(0, parenthesis_depth - 1)
                elif char == "," and parenthesis_depth == 0:
                    break

        encoded = "".join(rewritten).encode("utf-8", "surrogateescape")
        return match.group("prefix") + encoded + match.group("suffix")

    result = REFERENCE_ATTRIBUTES_RE.sub(replace_attribute, data)
    result = SRCSET_RE.sub(replace_srcset, result)
    return CSS_URL_RE.sub(replace_attribute, result)


def _update_opf(workspace: EpubWorkspace, replacements: dict[str, str]) -> None:
    def replace_item(match: re.Match[bytes]) -> bytes:
        attributes = match.group("attributes")
        href_match = OPF_HREF_RE.search(attributes)
        if href_match is None:
            return match.group(0)

        href = href_match.group("value").decode("utf-8", "surrogateescape")
        try:
            source = resolve_reference(workspace.opf_path, href)
        except ValueError:
            return match.group(0)
        target = replacements.get(source or "")
        if target is None:
            return match.group(0)

        rewritten_href = _rewrite_one(href, workspace.opf_path, replacements)
        attributes = (
            attributes[: href_match.start("value")]
            + rewritten_href.encode("utf-8", "surrogateescape")
            + attributes[href_match.end("value") :]
        )
        media_type_match = OPF_MEDIA_TYPE_RE.search(attributes)
        if media_type_match is not None:
            attributes = (
                attributes[: media_type_match.start("value")]
                + media_type_for(target).encode()
                + attributes[media_type_match.end("value") :]
            )
        return match.group("prefix") + attributes + match.group("suffix")

    updated = OPF_ITEM_RE.sub(
        replace_item, workspace.members[workspace.opf_path]
    )
    workspace.members[workspace.opf_path] = _rewrite_document(
        updated, workspace.opf_path, replacements
    )


def _has_transparency(image: Image.Image) -> bool:
    if "A" in image.getbands():
        alpha = image.getchannel("A")
        return alpha.getextrema()[0] < 255
    return image.mode == "P" and "transparency" in image.info


def _quantize_png(image: Image.Image) -> Image.Image:
    return image.quantize(colors=256, method=Image.Quantize.FASTOCTREE)


def process_images(
    input_file: str,
    output_path: str,
    *,
    mode: str,
    quality: int,
    webp_quality: int | None = None,
    png_to_jpg: bool = False,
    png_quantize: bool = False,
    logger,
) -> int | str:
    workspace = EpubWorkspace.load(input_file, logger=logger)
    replacements: dict[str, str] = {}
    candidates = processed = kept = failed = saved = 0
    existing = set(workspace.members)
    for source, original in list(workspace.members.items()):
        extension = PurePosixPath(source).suffix.lower()
        if extension not in IMAGE_EXTENSIONS:
            continue
        if mode == "webp_to_image" and extension != ".webp":
            continue
        candidates += 1
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
                elif mode == "webp_to_image":
                    if _has_transparency(image):
                        target_extension = ".png"
                        target_format = "PNG"
                        if png_quantize:
                            image = _quantize_png(image)
                    else:
                        target_extension = ".jpg"
                        target_format = "JPEG"
                        save_options["quality"] = quality
                elif extension in {".jpg", ".jpeg", ".webp"}:
                    save_options["quality"] = webp_quality if extension == ".webp" and webp_quality is not None else quality
                elif extension == ".png" and png_to_jpg and "A" not in image.getbands():
                    target_extension = ".jpg"
                    target_format = "JPEG"
                    save_options["quality"] = quality
                elif extension == ".png" and png_quantize:
                    image = _quantize_png(image)
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
    if mode == "webp_to_image":
        if candidates == 0:
            logger.write("没有找到需要转换的 WebP 图片")
            return "skip"
        if failed:
            raise RuntimeError(f"WebP 图片转换失败：{failed} 个文件无法处理")
    if replacements:
        for name, data in list(workspace.members.items()):
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in {".xhtml", ".html", ".htm", ".css", ".svg", ".ncx"}:
                workspace.members[name] = _rewrite_document(data, name, replacements)
        _update_opf(workspace, replacements)
    workspace.write(output_path, logger=logger)
    logger.write(
        f"图片处理完成：处理 {processed}，保留 {kept}，失败 {failed}，"
        f"节省 {format_size_mb(saved)}"
    )
    return 0
