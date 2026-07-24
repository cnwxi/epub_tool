from __future__ import annotations

import io
import json
import random
import subprocess
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from python_backend.epub_workspace import EpubWorkspace
from python_backend.services.image import image_compress, image_to_webp, replace_cover
from python_backend.services.text import chinese_convert
from python_backend.services.image.image_processing import format_size_mb
from python_backend.protocol import TaskRequest
from python_backend.task_runner import (
    input_has_task_output_suffix,
    run_task,
    validate_task_options,
)


class Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)


@pytest.mark.parametrize(
    "size_bytes, expected",
    [(0, "0.00 MB"), (1024 * 1024, "1.00 MB"), (3 * 1024 * 1024 // 2, "1.50 MB")],
)
def test_format_image_storage_size_as_mb(size_bytes: int, expected: str) -> None:
    assert format_size_mb(size_bytes) == expected


def image_bytes(format_name: str, *, color=(220, 20, 20, 255), size=(40, 30)) -> bytes:
    mode = "RGBA" if len(color) == 4 else "RGB"
    image = Image.new(mode, size, color)
    output = io.BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


def write_epub(
    path: Path,
    image_name: str = "Images/picture.png",
    image_format: str = "PNG",
    image_data: bytes | None = None,
    chapter_data: bytes | None = None,
    manifest_image_name: str | None = None,
    reference_image_name: str | None = None,
) -> None:
    manifest_image_name = manifest_image_name or image_name
    reference_image_name = reference_image_name or image_name
    container = b'''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>简体书</dc:title></metadata>
 <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="picture" href="{manifest_image_name}" media-type="{'image/jpeg' if image_format == 'JPEG' else 'image/png'}" properties="cover-image"/></manifest>
 <spine><itemref idref="chapter"/></spine>
</package>'''.encode()
    chapter = f'''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>简体标题</title><style>.简体{{background:url('{reference_image_name}')}}</style></head><body id="简体"><p title="简体提示">汉语发展</p><img src="{reference_image_name}?v=1#hero" alt="简体图"/><script>const 简体='汉语';</script></body></html>'''.encode()
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OPS/package.opf", opf)
        archive.writestr("OPS/chapter.xhtml", chapter_data or chapter)
        archive.writestr(
            f"OPS/{image_name}",
            image_data
            or image_bytes(
                image_format,
                color=(220, 20, 20) if image_format == "JPEG" else (220, 20, 20, 255),
            ),
        )


def test_workspace_rejects_unsafe_and_duplicate_members(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.epub"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("../escape", b"bad")
    with pytest.raises(ValueError, match="不安全路径"):
        EpubWorkspace.load(unsafe)

    duplicate = tmp_path / "duplicate.epub"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
    with pytest.raises(ValueError, match="重复成员"):
        EpubWorkspace.load(duplicate)


def test_workspace_accepts_nonstandard_mimetype_and_normalizes_output(
    tmp_path: Path,
) -> None:
    standard = tmp_path / "standard.epub"
    nonstandard = tmp_path / "nonstandard.epub"
    normalized = tmp_path / "normalized.epub"
    write_epub(standard)
    with zipfile.ZipFile(standard) as source, zipfile.ZipFile(
        nonstandard, "w", compression=zipfile.ZIP_DEFLATED
    ) as target:
        for info in source.infolist()[1:]:
            target.writestr(info.filename, source.read(info.filename))
        target.writestr("mimetype", b"application/epub+zip")

    logger = Logger()
    workspace = EpubWorkspace.load(nonstandard, logger=logger)
    workspace.write(normalized, logger=logger)

    with zipfile.ZipFile(normalized) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
    assert any("允许兼容读取" in message for message in logger.messages)


def test_image_to_webp_updates_manifest_and_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    write_epub(source)
    logger = Logger()
    monkeypatch.setattr(image_to_webp, "logger", logger)
    assert image_to_webp.run(str(source), str(tmp_path), options={"quality": 75}) == 0
    output = EpubWorkspace.load(tmp_path / "book_image_to_webp.epub")
    assert "OPS/Images/picture.webp" in output.members
    assert "OPS/Images/picture.png" not in output.members
    opf = output.members[output.opf_path]
    chapter = output.members["OPS/chapter.xhtml"]
    assert b"Images/picture.webp" in opf
    assert b'image/webp' in opf
    assert b"Images/picture.webp?v=1#hero" in chapter


def test_image_to_webp_converts_even_when_webp_is_larger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "book.epub"
    random_source = random.Random(0)
    noise = Image.frombytes(
        "RGB",
        (120, 120),
        bytes(random_source.randrange(256) for _ in range(120 * 120 * 3)),
    )
    jpeg_buffer = io.BytesIO()
    noise.save(jpeg_buffer, format="JPEG", quality=20)
    original = jpeg_buffer.getvalue()
    webp_buffer = io.BytesIO()
    noise.save(webp_buffer, format="WEBP", quality=82)
    assert len(webp_buffer.getvalue()) > len(original)
    write_epub(
        source,
        image_name="Images/picture.jpg",
        image_format="JPEG",
        image_data=original,
    )
    monkeypatch.setattr(image_to_webp, "logger", Logger())

    assert image_to_webp.run(str(source), str(tmp_path), options={"quality": 82}) == 0

    output = EpubWorkspace.load(tmp_path / "book_image_to_webp.epub")
    assert "OPS/Images/picture.webp" in output.members
    assert "OPS/Images/picture.jpg" not in output.members


def test_image_to_webp_preserves_existing_webp_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "webp-book.epub"
    write_epub(source, image_name="Images/picture.webp", image_format="WEBP")
    original = EpubWorkspace.load(source).members["OPS/Images/picture.webp"]
    monkeypatch.setattr(image_to_webp, "logger", Logger())

    assert image_to_webp.run(str(source), str(tmp_path), options={"quality": 1}) == 0

    output = EpubWorkspace.load(tmp_path / "webp-book_image_to_webp.epub")
    assert output.members["OPS/Images/picture.webp"] == original


def test_image_compress_preserves_transparent_png_when_jpeg_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    write_epub(source)
    monkeypatch.setattr(image_compress, "logger", Logger())
    assert image_compress.run(str(source), str(tmp_path), options={"jpeg_quality": 70, "webp_quality": 70, "png_to_jpg": True}) == 0
    output = EpubWorkspace.load(tmp_path / "book_image_compress.epub")
    assert "OPS/Images/picture.png" in output.members


def test_image_compress_quantizes_png_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "quantized-png.epub"
    image = Image.new("RGBA", (80, 80))
    image.putdata(
        [
            ((x * 13) % 256, (y * 17) % 256, (x * y) % 256, (x + y) % 256)
            for y in range(80)
            for x in range(80)
        ]
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=0)
    write_epub(source, image_data=buffer.getvalue())
    monkeypatch.setattr(image_compress, "logger", Logger())

    assert image_compress.run(
        str(source),
        str(tmp_path),
        options={
            "jpeg_quality": 70,
            "webp_quality": 70,
            "png_to_jpg": False,
            "png_quantize": True,
        },
    ) == 0

    output = EpubWorkspace.load(tmp_path / "quantized-png_image_compress.epub")
    with Image.open(io.BytesIO(output.members["OPS/Images/picture.png"])) as quantized:
        assert quantized.mode == "P"
        assert "transparency" in quantized.info


def test_image_compress_handles_jpg_after_exif_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "jpeg-book.epub"
    write_epub(source, image_name="Images/picture.jpg", image_format="JPEG")
    monkeypatch.setattr(image_compress, "logger", Logger())

    assert image_compress.run(
        str(source),
        str(tmp_path),
        options={"jpeg_quality": 70, "webp_quality": 70, "png_to_jpg": False},
    ) == 0
    output = EpubWorkspace.load(tmp_path / "jpeg-book_image_compress.epub")
    with Image.open(io.BytesIO(output.members["OPS/Images/picture.jpg"])) as image:
        assert image.format == "JPEG"


def test_new_tasks_delete_and_replace_existing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    output_path = tmp_path / "book_image_compress.epub"
    write_epub(source)
    output_path.write_bytes(b"existing output")
    logger = Logger()
    monkeypatch.setattr(image_compress, "logger", logger)

    assert image_compress.run(
        str(source),
        str(tmp_path),
        options={"jpeg_quality": 70, "webp_quality": 70, "png_to_jpg": False},
    ) == 0

    assert EpubWorkspace.load(output_path).opf_path == "OPS/package.opf"
    assert any("已删除同名输出文件" in message for message in logger.messages)


def test_chinese_conversion_preserves_paths_ids_css_and_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    write_epub(source)
    monkeypatch.setattr(chinese_convert, "logger", Logger())
    assert chinese_convert.run(str(source), str(tmp_path), options={"direction": "s2t"}) == 0
    output = EpubWorkspace.load(tmp_path / "book_chinese_convert_tc.epub")
    chapter = output.members["OPS/chapter.xhtml"].decode()
    assert "漢語發展" in chapter
    assert 'title="簡體提示"' in chapter
    assert 'id="简体"' in chapter
    assert ".简体" in chapter
    assert "const 简体='汉语'" in chapter
    assert "Images/picture.png" in chapter


def test_chinese_conversion_reads_utf16_and_writes_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "utf16-book.epub"
    chapter = """<?xml version=\"1.0\" encoding=\"UTF-16\"?>
<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>汉语发展</p></body></html>""".encode(
        "utf-16"
    )
    write_epub(source, chapter_data=chapter)
    monkeypatch.setattr(chinese_convert, "logger", Logger())

    assert chinese_convert.run(str(source), str(tmp_path), options={"direction": "s2t"}) == 0

    output = EpubWorkspace.load(tmp_path / "utf16-book_chinese_convert_tc.epub")
    converted = output.members["OPS/chapter.xhtml"]
    assert converted.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert "漢語發展" in converted.decode("utf-8")


def test_replace_cover_uses_detected_format_and_updates_epub2_and_epub3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "book.epub"
    write_epub(source)
    cover = tmp_path / "cover.dat"
    cover.write_bytes(image_bytes("JPEG", color=(10, 30, 200)))
    monkeypatch.setattr(replace_cover, "logger", Logger())
    assert replace_cover.run(str(source), str(tmp_path), cover_path=str(cover)) == 0
    output = EpubWorkspace.load(tmp_path / "book_replace_cover.epub")
    assert "OPS/Images/cover.jpg" in output.members
    assert "OPS/Images/picture.png" in output.members
    opf = output.members[output.opf_path]
    chapter = output.members["OPS/chapter.xhtml"]
    assert b'image/jpeg' in opf
    assert b'cover-image' in opf
    assert b'name="cover"' in opf
    assert b"Images/cover.jpg?v=1#hero" in chapter
    assert b"Images/picture.png?v=1#hero" not in chapter


def test_replace_cover_rewrites_percent_encoded_cover_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "encoded-cover.epub"
    write_epub(
        source,
        image_name="Images/cover image.png",
        manifest_image_name="Images/cover%20image.png",
        reference_image_name="Images/cover%20image.png",
    )
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(image_bytes("JPEG", color=(10, 30, 200)))
    monkeypatch.setattr(replace_cover, "logger", Logger())

    assert replace_cover.run(str(source), str(tmp_path), cover_path=str(cover)) == 0

    output = EpubWorkspace.load(tmp_path / "encoded-cover_replace_cover.epub")
    chapter = output.members["OPS/chapter.xhtml"]
    assert b"Images/cover.jpg?v=1#hero" in chapter
    assert b"Images/cover%20image.png?v=1#hero" not in chapter


@pytest.mark.parametrize("task_type, options", [
    ("image_compress", {"jpeg_quality": 0}),
    ("image_compress", {"png_quantize": "yes"}),
    ("webp_to_img", {"quality": True}),
    ("webp_to_img", {"png_quantize": "yes"}),
    ("image_to_webp", {"quality": True}),
    ("chinese_convert", {"direction": "invalid"}),
    ("replace_cover", {"cover_path_by_file": []}),
])
def test_invalid_options_fail_before_processing(task_type: str, options: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_task_options(task_type, options)


def test_runner_emits_real_output_and_continues_partial_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "book.epub"
    missing = tmp_path / "missing.epub"
    write_epub(source)
    result = run_task(TaskRequest(
        task_id="image-batch",
        task_type="image_to_webp",
        input_files=[str(missing), str(source)],
        output_dir=str(tmp_path / "output"),
        options={"quality": 75},
    ))
    events = capsys.readouterr().out
    assert result.status == "partial"
    assert result.summary == {"total": 2, "success": 1, "failed": 1, "skipped": 0}
    assert result.outputs == [str(tmp_path / "output" / "book_image_to_webp.epub")]
    event_names = [json.loads(line)["event"] for line in events.splitlines()]
    assert event_names.count("task.file.started") == 2
    assert event_names.count("task.file.finished") == 2


def test_runner_creates_missing_output_directory_for_rewrite_task(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "book.epub"
    output_dir = tmp_path / "new-output"
    write_epub(source)

    result = run_task(TaskRequest(
        task_id="rewrite-output-dir",
        task_type="reformat_epub",
        input_files=[str(source)],
        output_dir=str(output_dir),
        options={},
    ))
    capsys.readouterr()

    assert result.ok is True
    assert result.outputs == [str(output_dir / "book_reformat_epub.epub")]
    assert (output_dir / "book_reformat_epub.epub").is_file()


def test_replace_cover_runner_skips_files_without_mapping(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = tmp_path / "first.epub"
    second = tmp_path / "second.epub"
    cover = tmp_path / "cover.png"
    write_epub(first)
    write_epub(second)
    cover.write_bytes(image_bytes("PNG"))
    result = run_task(TaskRequest(
        task_id="cover-batch",
        task_type="replace_cover",
        input_files=[str(first), str(second)],
        output_dir=None,
        options={"cover_path_by_file": {str(first): str(cover)}},
    ))
    capsys.readouterr()
    assert result.status == "partial"
    assert result.summary == {"total": 2, "success": 1, "failed": 0, "skipped": 1}


def test_built_sidecar_loads_opencc_data_when_available(tmp_path: Path) -> None:
    sidecar = Path("src-tauri/binaries/epub-tool-python/epub-tool-python")
    if not sidecar.is_file():
        pytest.skip("sidecar 尚未构建")
    source = tmp_path / "sidecar.epub"
    write_epub(source)
    completed = subprocess.run(
        [
            str(sidecar),
            "run",
            "--task-type",
            "chinese_convert",
            "--input-file",
            str(source),
            "--output-dir",
            str(tmp_path),
            "--options-json",
            '{"direction":"s2t"}',
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    output = EpubWorkspace.load(tmp_path / "sidecar_chinese_convert_tc.epub")
    assert "漢語發展" in output.members["OPS/chapter.xhtml"].decode()


@pytest.mark.parametrize(
    "task_type, filename, options",
    [
        ("reformat_epub", "book_reformat_epub.epub", {}),
        ("decrypt_epub", "book_decrypt_epub.epub", {}),
        ("encrypt_epub", "book_encrypt_epub.epub", {}),
        ("encrypt_font", "book_encrypt_font.epub", {}),
        ("decrypt_font", "book_decrypt_font.epub", {}),
        ("webp_to_img", "book_webp_to_img.epub", {}),
        ("image_compress", "book_image_compress.epub", {}),
        ("image_to_webp", "book_image_to_webp.epub", {}),
        ("chinese_convert", "book_chinese_convert_tc.epub", {"direction": "s2t"}),
        ("chinese_convert", "book_chinese_convert_sc.epub", {"direction": "t2s"}),
        ("replace_cover", "book_replace_cover.epub", {}),
    ],
)
def test_task_output_suffix_prevents_same_task_reexecution(
    task_type: str, filename: str, options: dict[str, object]
) -> None:
    assert input_has_task_output_suffix(filename, task_type, options)


def test_different_task_suffix_does_not_block_processing() -> None:
    assert not input_has_task_output_suffix(
        "book_reformat_epub.epub", "image_compress", {}
    )
