from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"


def image_bytes(format_name: str, *, color: tuple[int, ...]) -> bytes:
    mode = "RGBA" if len(color) == 4 else "RGB"
    image = Image.new(mode, (12, 8), color)
    output = io.BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


def write_golden_input(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            b"""<container><rootfiles><rootfile full-path=\"OPS/package.opf\"/></rootfiles></container>""",
        )
        archive.writestr(
            "OPS/package.opf",
            b"""<package><metadata/><manifest>
<item id=\"png\" href=\"Images/picture.png\" media-type=\"image/png\" properties=\"cover-image\"/>
<item id=\"jpg\" href=\"Images/photo.jpg\" media-type=\"image/jpeg\"/>
<item id=\"webp-transparent\" href=\"Images/picture.webp\" media-type=\"image/webp\"/>
<item id=\"webp-opaque\" href=\"Images/opaque.webp\" media-type=\"image/webp\"/>
<item id=\"chapter\" href=\"chapter.xhtml\" media-type=\"application/xhtml+xml\"/>
<item id=\"style\" href=\"style.css\" media-type=\"text/css\"/>
</manifest></package>""",
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            """<html><body>
<img src=\"Images/picture.png?rev=1#hero\"/>
<img src=\"Images/photo.jpg\"/>
<img src=\"Images/picture.webp\"/>
<img srcset=\"Images/picture.webp 1x, Images/opaque.webp 2x\"/>
<p id=\"简体\" title=\"汉语\">汉语发展 后台程序 鼠标软件 网络信息 干杯 发型 复习 面条</p><script>const text = '汉语';</script>
</body></html>""".encode(),
        )
        archive.writestr(
            "OPS/style.css",
            """.简体 { background: url(\"Images/picture.webp?rev=1\"); }
.two { background: url(Images/opaque.webp#cover); }""".encode(),
        )
        archive.writestr("OPS/Images/picture.png", image_bytes("PNG", color=(220, 20, 20, 255)))
        archive.writestr("OPS/Images/photo.jpg", image_bytes("JPEG", color=(20, 120, 220)))
        archive.writestr("OPS/Images/picture.webp", image_bytes("WEBP", color=(30, 200, 30, 120)))
        archive.writestr("OPS/Images/opaque.webp", image_bytes("WEBP", color=(30, 30, 220)))


def replace_epub_member(path: Path, member_name: str, content: bytes) -> None:
    members = epub_members(path)
    members[member_name] = content
    rewritten = path.with_suffix(".rewritten.epub")
    with zipfile.ZipFile(rewritten, "w") as archive:
        for name, data in members.items():
            archive.writestr(
                name,
                data,
                zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED,
            )
    rewritten.replace(path)


def run_python_task(input_file: Path, output_dir: Path, task_type: str, options: dict[str, object]) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "python_backend.cli",
            "run",
            "--task-id",
            "python-golden",
            "--task-type",
            task_type,
            "--input-file",
            str(input_file),
            "--output-dir",
            str(output_dir),
            "--options-json",
            json.dumps(options),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return finished_event(completed.stdout)["result"]


def run_rust_task(input_file: Path, output_dir: Path, task_type: str, options: dict[str, object]) -> dict[str, object]:
    request = {
        "taskId": "rust-golden",
        "taskType": task_type,
        "inputFiles": [str(input_file)],
        "outputDir": str(output_dir),
        "options": options,
    }
    command = [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--bin",
            "rust-task-runner",
            "--",
            "--request-json",
            json.dumps(request),
        ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return finished_event(completed.stdout)["result"]


def finished_event(stdout: str) -> dict[str, object]:
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    event = next(event for event in reversed(events) if event["event"] == "task.finished")
    assert event["status"] == "success"
    return event


def epub_members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def image_signature(data: bytes) -> tuple[str, tuple[int, int], str]:
    with Image.open(io.BytesIO(data)) as image:
        return image.format or "", image.size, image.mode


def opencc_dictionary_keys(*names: str) -> list[str]:
    directory = REPO_ROOT / "src-tauri" / "bundle-resources" / "opencc"
    keys: list[str] = []
    for name in names:
        for line in (directory / name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                keys.append(line.split("\t", maxsplit=1)[0])
    return keys


@pytest.mark.parametrize(
    ("task_type", "options", "expected_absent", "expected_references"),
    [
        (
            "image_to_webp",
            {"quality": 75},
            {"OPS/Images/picture.png", "OPS/Images/photo.jpg"},
            {"Images/picture-2.webp?rev=1#hero", "Images/photo.webp"},
        ),
        (
            "webp_to_img",
            {"quality": 75, "png_quantize": False},
            {"OPS/Images/picture.webp", "OPS/Images/opaque.webp"},
            {"Images/picture-2.png", "Images/opaque.jpg"},
        ),
        (
            "webp_to_img",
            {"quality": 75, "png_quantize": True},
            {"OPS/Images/picture.webp", "OPS/Images/opaque.webp"},
            {"Images/picture-2.png", "Images/opaque.jpg"},
        ),
        (
            "image_compress",
            {"jpeg_quality": 70, "webp_quality": 70, "png_to_jpg": False, "png_quantize": False},
            set(),
            {"Images/picture.webp", "Images/opaque.webp"},
        ),
    ],
)
def test_rust_image_tasks_match_python_golden_structure(
    tmp_path: Path,
    task_type: str,
    options: dict[str, object],
    expected_absent: set[str],
    expected_references: set[str],
) -> None:
    input_file = tmp_path / "book.epub"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)

    python_result = run_python_task(input_file, python_output_dir, task_type, options)
    rust_result = run_rust_task(input_file, rust_output_dir, task_type, options)

    assert python_result["summary"] == rust_result["summary"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
    }
    python_epub = Path(python_result["outputs"][0])
    rust_epub = Path(rust_result["outputs"][0])
    python_members = epub_members(python_epub)
    rust_members = epub_members(rust_epub)
    assert set(python_members) == set(rust_members)
    assert not expected_absent & set(rust_members)

    for name in rust_members:
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            assert image_signature(python_members[name]) == image_signature(rust_members[name])

    documents = []
    for name in ("OPS/chapter.xhtml", "OPS/style.css"):
        python_document = python_members[name].decode("utf-8")
        rust_document = rust_members[name].decode("utf-8")
        assert python_document == rust_document
        documents.append(rust_document)
    for reference in expected_references:
        assert reference in "\n".join(documents)

    for opf in (python_members["OPS/package.opf"], rust_members["OPS/package.opf"]):
        decoded = opf.decode("utf-8")
        assert 'name="generator" content="Epub Tool"' in decoded
        for reference in expected_references:
            assert reference.split("?", 1)[0].split("#", 1)[0] in decoded or reference in rust_members


def test_rust_replace_cover_matches_python_golden_structure(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    cover_file = tmp_path / "replacement.jpg"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)
    cover_file.write_bytes(image_bytes("JPEG", color=(20, 180, 220)))
    options = {"cover_path_by_file": {str(input_file): str(cover_file)}}

    python_result = run_python_task(input_file, python_output_dir, "replace_cover", options)
    rust_result = run_rust_task(input_file, rust_output_dir, "replace_cover", options)

    assert python_result["summary"] == rust_result["summary"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
    }
    python_members = epub_members(Path(python_result["outputs"][0]))
    rust_members = epub_members(Path(rust_result["outputs"][0]))
    assert set(python_members) == set(rust_members)
    assert image_signature(python_members["OPS/Images/cover.jpg"]) == image_signature(
        rust_members["OPS/Images/cover.jpg"]
    )
    documents = []
    for name in ("OPS/chapter.xhtml", "OPS/style.css"):
        assert python_members[name] == rust_members[name]
        documents.append(rust_members[name])
    assert b"Images/cover.jpg" in b"\n".join(documents)
    assert b"Images/picture.png" not in b"\n".join(documents)
    for opf in (python_members["OPS/package.opf"], rust_members["OPS/package.opf"]):
        assert b'cover-image' in opf
        assert b'name="cover"' in opf
        assert b'Images/cover.jpg' in opf
        assert b'name="generator" content="Epub Tool"' in opf


def test_rust_png_quantization_matches_python_output_structure(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)
    image = Image.new("RGBA", (80, 80))
    image.putdata(
        [
            ((x * 13) % 256, (y * 17) % 256, (x * y) % 256, (x + y) % 256)
            for y in range(80)
            for x in range(80)
        ]
    )
    image_output = io.BytesIO()
    image.save(image_output, format="PNG", compress_level=0)
    replace_epub_member(input_file, "OPS/Images/picture.png", image_output.getvalue())
    options = {
        "jpeg_quality": 70,
        "webp_quality": 70,
        "png_to_jpg": False,
        "png_quantize": True,
    }

    python_result = run_python_task(input_file, python_output_dir, "image_compress", options)
    rust_result = run_rust_task(input_file, rust_output_dir, "image_compress", options)
    assert python_result["summary"] == rust_result["summary"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
    }
    python_members = epub_members(Path(python_result["outputs"][0]))
    rust_members = epub_members(Path(rust_result["outputs"][0]))
    assert set(python_members) == set(rust_members)
    assert image_signature(python_members["OPS/Images/picture.png"]) == image_signature(
        rust_members["OPS/Images/picture.png"]
    ) == ("PNG", (80, 80), "P")
    assert len(rust_members["OPS/Images/picture.png"]) < len(image_output.getvalue())
    assert python_members["OPS/chapter.xhtml"] == rust_members["OPS/chapter.xhtml"]
    assert python_members["OPS/style.css"] == rust_members["OPS/style.css"]


@pytest.mark.parametrize(
    ("direction", "expected_text", "expected_title"),
    [("s2t", "漢語發展", "漢語"), ("t2s", "汉语发展", "汉语")],
)
def test_rust_chinese_conversion_matches_python_opencc_golden(
    tmp_path: Path,
    direction: str,
    expected_text: str,
    expected_title: str,
) -> None:
    input_file = tmp_path / "book.epub"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)
    options = {"direction": direction}

    python_result = run_python_task(input_file, python_output_dir, "chinese_convert", options)
    rust_result = run_rust_task(input_file, rust_output_dir, "chinese_convert", options)

    assert python_result["summary"] == rust_result["summary"] == {
        "total": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
    }
    python_members = epub_members(Path(python_result["outputs"][0]))
    rust_members = epub_members(Path(rust_result["outputs"][0]))
    assert set(python_members) == set(rust_members)
    assert python_members["OPS/chapter.xhtml"] == rust_members["OPS/chapter.xhtml"]
    chapter = rust_members["OPS/chapter.xhtml"].decode("utf-8")
    assert expected_text in chapter
    assert f'title="{expected_title}"' in chapter
    assert 'id="简体"' in chapter
    assert "const text = '汉语';" in chapter
    assert python_members["OPS/style.css"] == rust_members["OPS/style.css"]


def test_rust_chinese_conversion_matches_python_for_utf16_xhtml(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)
    members = epub_members(input_file)
    members["OPS/chapter.xhtml"] = (
        "<?xml version=\"1.0\" encoding=\"UTF-16\"?>"
        "<html><body><p title=\"汉语\">汉语发展</p><script>const text = '汉语';</script></body></html>"
    ).encode("utf-16")
    rewritten = tmp_path / "rewritten.epub"
    with zipfile.ZipFile(rewritten, "w") as archive:
        for name, content in members.items():
            archive.writestr(
                name,
                content,
                zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED,
            )
    rewritten.replace(input_file)

    options = {"direction": "s2t"}
    python_result = run_python_task(input_file, python_output_dir, "chinese_convert", options)
    rust_result = run_rust_task(input_file, rust_output_dir, "chinese_convert", options)
    python_members = epub_members(Path(python_result["outputs"][0]))
    rust_members = epub_members(Path(rust_result["outputs"][0]))
    assert python_members["OPS/chapter.xhtml"] == rust_members["OPS/chapter.xhtml"]
    assert rust_members["OPS/chapter.xhtml"].startswith(
        b'<?xml version="1.0" encoding="UTF-8"?>'
    )
    assert "漢語發展" in rust_members["OPS/chapter.xhtml"].decode("utf-8")


@pytest.mark.parametrize(
    ("direction", "dictionary_names"),
    [
        ("s2t", ("STPhrases.txt", "STCharacters.txt")),
        ("t2s", ("TSPhrases.txt", "TSCharacters.txt")),
    ],
)
def test_rust_chinese_conversion_matches_python_for_bundled_opencc_dictionary_keys(
    tmp_path: Path, direction: str, dictionary_names: tuple[str, str]
) -> None:
    input_file = tmp_path / f"opencc-{direction}.epub"
    python_output_dir = tmp_path / "python"
    rust_output_dir = tmp_path / "rust"
    python_output_dir.mkdir()
    rust_output_dir.mkdir()
    write_golden_input(input_file)
    keys = opencc_dictionary_keys(*dictionary_names)
    source_text = "\n".join(keys)
    replace_epub_member(
        input_file,
        "OPS/chapter.xhtml",
        f"<html><body><p>{source_text}</p></body></html>".encode(),
    )

    options = {"direction": direction}
    python_result = run_python_task(input_file, python_output_dir, "chinese_convert", options)
    rust_result = run_rust_task(input_file, rust_output_dir, "chinese_convert", options)
    python_members = epub_members(Path(python_result["outputs"][0]))
    rust_members = epub_members(Path(rust_result["outputs"][0]))

    assert python_members["OPS/chapter.xhtml"] == rust_members["OPS/chapter.xhtml"]
