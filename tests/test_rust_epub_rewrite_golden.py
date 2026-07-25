from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"


def write_epub(path: Path, *, encrypted: bool) -> None:
    image_href = "Images/%2A%3A.jpg" if encrypted else "Images/base.jpg"
    image_member = "OEBPS/Images/*:.jpg" if encrypted else "OEBPS/Images/base.jpg"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            b'<container><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            f"""<package version="2.0"><metadata/><manifest>
<item id="image" href="{image_href}" media-type="image/jpeg"/>
<item id="style" href="Styles/style.css" media-type="text/css"/>
<item id="other-style" href="Styles/other.css" media-type="text/css"/>
<item id="font" href="Fonts/font.ttf" media-type="font/ttf"/>
<item id="audio" href="Audio/audio.mp3" media-type="audio/mpeg"/>
<item id="video" href="Video/video.mp4" media-type="video/mp4"/>
<item id="script" href="Misc/script.js" media-type="application/javascript"/>
<item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
<item id="other" href="Text/other.xhtml" media-type="application/xhtml+xml"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest><spine toc="ncx"><itemref idref="chapter"/></spine>
<guide><reference type="text" href="Text/chapter.xhtml#guide"/></guide></package>""".encode(),
        )
        archive.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html><head><link href="../Styles/style.css#style" rel="stylesheet"/></head><body>
<a href="other.xhtml#other">chapter</a><img src="../{image_href}#cover"/>
<video src="../Video/video.mp4#video" poster="../{image_href}#poster"/>
<audio src="../Audio/audio.mp3#audio"/><script src="../Misc/script.js#script"></script>
<div style="background:url('../{image_href}#inline');src:url('../Fonts/font.ttf#font')"></div></body></html>""".encode(),
        )
        archive.writestr("OEBPS/Text/other.xhtml", b"<html><body id='other'>Other</body></html>")
        archive.writestr(
            "OEBPS/Styles/style.css",
            f'@import "other.css#imp"; .image {{ background: url("../{image_href}#background"); }} .font {{ src: url("../Fonts/font.ttf#font"); }}'.encode(),
        )
        archive.writestr("OEBPS/Styles/other.css", b"body { color: black; }")
        archive.writestr("OEBPS/toc.ncx", b'<ncx><content src="Text/chapter.xhtml#toc"/></ncx>')
        archive.writestr(image_member, b"image")
        archive.writestr("OEBPS/Fonts/font.ttf", b"font")
        archive.writestr("OEBPS/Audio/audio.mp3", b"audio")
        archive.writestr("OEBPS/Video/video.mp4", b"video")
        archive.writestr("OEBPS/Misc/script.js", b"script")


def write_slim_epub(path: Path, *, encrypted: bool) -> None:
    base_href = "Images/%2A%3A.jpg" if encrypted else "Images/base.jpg"
    slim_href = "Images/%2A%3A~slim.jpg" if encrypted else "Images/base_slim.jpg"
    base_member = "OEBPS/Images/*:.jpg" if encrypted else "OEBPS/Images/base.jpg"
    slim_member = "OEBPS/Images/*:~slim.jpg" if encrypted else "OEBPS/Images/base_slim.jpg"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            b'<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/content.opf",
            f"""<package version="3.0"><metadata><meta refines="#f4" property="title-type">cover</meta></metadata><manifest>
<item id="f2" href="{base_href}" media-type="image/jpeg" properties="cover-image"/>
<item id="f4" href="{slim_href}" media-type="image/jpeg" properties="cover-image"/>
<item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
</manifest><spine><itemref idref="chapter"/></spine></package>""".encode(),
        )
        archive.writestr(
            "OEBPS/Text/chapter.xhtml",
            f'<html><body><img src="../{base_href}"/><img src="../{slim_href}"/></body></html>'.encode(),
        )
        archive.writestr(base_member, b"base-image")
        archive.writestr(slim_member, b"slim-image")


def run_task(backend: str, input_file: Path, output_dir: Path, task_type: str) -> dict[str, object]:
    if backend == "python":
        command = [
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
        ]
        timeout = 60
    else:
        request = {
            "taskId": "rust-golden",
            "taskType": task_type,
            "inputFiles": [str(input_file)],
            "outputDir": str(output_dir),
            "options": {},
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
        timeout = 120
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    result = next(event["result"] for event in reversed(events) if event["event"] == "task.finished")
    assert result["status"] == "success"
    return result


def members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def run_golden(tmp_path: Path, *, task_type: str, encrypted: bool) -> tuple[dict[str, bytes], dict[str, bytes]]:
    input_file = tmp_path / "book.epub"
    python_dir = tmp_path / "python"
    rust_dir = tmp_path / "rust"
    python_dir.mkdir()
    rust_dir.mkdir()
    write_epub(input_file, encrypted=encrypted)
    python = run_task("python", input_file, python_dir, task_type)
    rust = run_task("rust", input_file, rust_dir, task_type)
    return members(Path(python["outputs"][0])), members(Path(rust["outputs"][0]))


def test_rust_encrypt_epub_matches_python_member_layout_and_references(tmp_path: Path) -> None:
    python, rust = run_golden(tmp_path, task_type="encrypt_epub", encrypted=False)

    assert set(python) == set(rust)
    assert python["OEBPS/Fonts/" + next(name.rsplit("/", 1)[-1] for name in python if name.startswith("OEBPS/Fonts/"))] == rust["OEBPS/Fonts/" + next(name.rsplit("/", 1)[-1] for name in rust if name.startswith("OEBPS/Fonts/"))]
    chapter = next(data for name, data in rust.items() if name.startswith("OEBPS/Text/") and b"#cover" in data)
    stylesheet = next(data for name, data in rust.items() if name.startswith("OEBPS/Styles/") and b"#background" in data)
    assert b"../Images/" in chapter
    assert b"../Fonts/" in stylesheet
    assert b"#toc" in rust["OEBPS/toc.ncx"]
    assert b"#guide" in rust["OEBPS/content.opf"]


def test_rust_reformat_epub_matches_python_member_layout_and_references(tmp_path: Path) -> None:
    python, rust = run_golden(tmp_path, task_type="reformat_epub", encrypted=False)

    assert set(python) == set(rust)
    assert b"../Images/base.jpg#cover" in rust["OEBPS/Text/chapter.xhtml"]
    assert b"../Fonts/font.ttf#font" in rust["OEBPS/Styles/style.css"]
    assert b'../Text/chapter.xhtml#guide' in rust["OEBPS/content.opf"]


def test_rust_decrypt_epub_matches_python_member_layout_and_references(tmp_path: Path) -> None:
    python, rust = run_golden(tmp_path, task_type="decrypt_epub", encrypted=True)

    assert set(python) == set(rust)
    assert "OEBPS/Images/image.jpg" in rust
    assert b"../Images/image.jpg#cover" in rust["OEBPS/Text/chapter.xhtml"]
    assert b"../Fonts/font.ttf#font" in rust["OEBPS/Styles/style.css"]
    assert b'Text/chapter.xhtml#toc' in rust["OEBPS/toc.ncx"]
    assert b'href="Text/chapter.xhtml#guide"' in rust["OEBPS/content.opf"]


def test_rust_encrypt_epub_preserves_duokan_slim_pairing(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    python_dir = tmp_path / "python"
    rust_dir = tmp_path / "rust"
    python_dir.mkdir()
    rust_dir.mkdir()
    write_slim_epub(input_file, encrypted=False)

    python = members(Path(run_task("python", input_file, python_dir, "encrypt_epub")["outputs"][0]))
    rust = members(Path(run_task("rust", input_file, rust_dir, "encrypt_epub")["outputs"][0]))

    assert set(python) == set(rust)
    image_names = sorted(name for name in rust if name.startswith("OEBPS/Images/"))
    assert len(image_names) == 2
    assert image_names[1].replace("~slim", "") == image_names[0]


def test_rust_decrypt_epub_rewrites_duokan_slim_ids_and_references(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    python_dir = tmp_path / "python"
    rust_dir = tmp_path / "rust"
    python_dir.mkdir()
    rust_dir.mkdir()
    write_slim_epub(input_file, encrypted=True)

    python = members(Path(run_task("python", input_file, python_dir, "decrypt_epub")["outputs"][0]))
    rust = members(Path(run_task("rust", input_file, rust_dir, "decrypt_epub")["outputs"][0]))

    assert set(python) == set(rust)
    assert {"OEBPS/Images/f2.jpg", "OEBPS/Images/f2~slim.jpg"} <= set(rust)
    assert b'refines="#f2~slim"' in rust["OEBPS/content.opf"]
    assert b'../Images/f2~slim.jpg' in rust["OEBPS/Text/chapter.xhtml"]
