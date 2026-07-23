from __future__ import annotations

import io
import posixpath
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from PIL import Image, UnidentifiedImageError

from python_backend.epub_workspace import EpubWorkspace, media_type_for, resolve_reference
from python_backend.services.image.image_processing import _rewrite_document
from python_backend.services.utils.log import logwriter


logger = logwriter()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _unique_path(preferred: str, members: dict[str, bytes], current: str | None) -> str:
    if preferred == current or preferred not in members:
        return preferred
    stem = str(PurePosixPath(preferred).with_suffix(""))
    extension = PurePosixPath(preferred).suffix
    index = 2
    while f"{stem}-{index}{extension}" in members:
        index += 1
    return f"{stem}-{index}{extension}"


def run(input_file: str, output_dir: str | None, *, cover_path: str) -> int:
    cover_file = Path(cover_path)
    raw_cover = cover_file.read_bytes()
    try:
        with Image.open(io.BytesIO(raw_cover)) as image:
            image.load()
            image_format = (image.format or "").upper()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"封面不是有效图片: {exc}") from exc
    extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(image_format)
    if extension is None:
        raise ValueError("封面仅支持 JPG、PNG 或 WebP")

    workspace = EpubWorkspace.load(input_file, logger=logger)
    root = ElementTree.fromstring(workspace.members[workspace.opf_path])
    manifest = next((node for node in root.iter() if _local_name(node.tag) == "manifest"), None)
    metadata = next((node for node in root.iter() if _local_name(node.tag) == "metadata"), None)
    if manifest is None or metadata is None:
        raise ValueError("OPF 缺少 manifest 或 metadata")
    items = [node for node in manifest if _local_name(node.tag) == "item"]
    cover_item = next((node for node in items if "cover-image" in node.get("properties", "").split()), None)
    if cover_item is None:
        cover_id = next((node.get("content") for node in metadata if _local_name(node.tag) == "meta" and node.get("name") == "cover"), None)
        cover_item = next((node for node in items if node.get("id") == cover_id), None)
    opf_dir = posixpath.dirname(workspace.opf_path)
    old_path: str | None = None
    if cover_item is not None and cover_item.get("href"):
        old_path = resolve_reference(workspace.opf_path, cover_item.get("href", ""))
    preferred_dir = posixpath.dirname(old_path) if old_path else posixpath.join(opf_dir, "Images")
    new_path = _unique_path(posixpath.join(preferred_dir, f"cover{extension}"), workspace.members, old_path)
    if cover_item is None:
        namespace = root.tag.partition("}")[0].lstrip("{")
        tag = f"{{{namespace}}}item" if namespace else "item"
        used_ids = {item.get("id") for item in items}
        cover_id = "cover-image"
        index = 2
        while cover_id in used_ids:
            cover_id = f"cover-image-{index}"
            index += 1
        cover_item = ElementTree.SubElement(manifest, tag, {"id": cover_id, "properties": "cover-image"})
    cover_item.set("href", posixpath.relpath(new_path, opf_dir or "."))
    cover_item.set("media-type", media_type_for(new_path))
    properties = set(cover_item.get("properties", "").split())
    properties.add("cover-image")
    cover_item.set("properties", " ".join(sorted(properties)))
    cover_id = cover_item.get("id", "cover-image")
    epub2_meta = next((node for node in metadata if _local_name(node.tag) == "meta" and node.get("name") == "cover"), None)
    if epub2_meta is None:
        namespace = root.tag.partition("}")[0].lstrip("{")
        tag = f"{{{namespace}}}meta" if namespace else "meta"
        epub2_meta = ElementTree.SubElement(metadata, tag)
        epub2_meta.set("name", "cover")
    epub2_meta.set("content", cover_id)
    if old_path and old_path != new_path:
        replacements = {old_path: new_path}
        for name, data in list(workspace.members.items()):
            suffix = PurePosixPath(name).suffix.lower()
            if suffix in {".xhtml", ".html", ".htm", ".css", ".svg", ".ncx"}:
                workspace.members[name] = _rewrite_document(data, name, replacements)
    workspace.members[new_path] = raw_cover
    workspace.members[workspace.opf_path] = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    target_dir = Path(output_dir) if output_dir else Path(input_file).parent
    workspace.write(
        target_dir / f"{Path(input_file).stem}_replace_cover.epub", logger=logger
    )
    logger.write(f"封面已更换为 {new_path}")
    return 0
