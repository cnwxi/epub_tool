from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fontTools.ttLib import TTFont
from PIL import Image

from rust_golden_support import run_python_task


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"
REAL_INPUT = REPO_ROOT / "fixtures" / "解密前.epub"
REAL_DECRYPTED_INPUT = REPO_ROOT / "fixtures" / "解密后.epub"
REAL_FONT_ENCRYPT_INPUT = REPO_ROOT / "fixtures" / "解密前_reformat_decrypt.epub"
REAL_FONT_OCR_INPUT = REPO_ROOT / "fixtures" / "解密前_reformat_decrypt_encrypt_font.epub"


def run_task(
    backend: str, input_file: Path, output_dir: Path, task_type: str = "reformat_epub"
) -> Path:
    output_dir.mkdir()
    if backend == "python":
        return Path(run_python_task(input_file, output_dir, task_type)["outputs"][0])
    request = {
        "taskId": "real-rust-golden",
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
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    result = next(event["result"] for event in reversed(events) if event["event"] == "task.finished")
    assert result["status"] == "success"
    return Path(result["outputs"][0])


def run_font_ocr_task(
    backend: str,
    input_file: Path,
    output_dir: Path,
    target_families: list[str] | None = None,
) -> Path:
    output_dir.mkdir()
    options = {
        "target_font_families_by_file": {str(input_file): target_families or ["htt"]},
        "min_ocr_confidence": 0.8,
        "ocr_char_policy": "strict",
    }
    if backend == "python":
        return Path(run_python_task(input_file, output_dir, "decrypt_font", options)["outputs"][0])
    request = {
        "taskId": "real-rust-font-ocr",
        "taskType": "decrypt_font",
        "inputFiles": [str(input_file)],
        "outputDir": str(output_dir),
        "options": options,
    }
    command = [
        "cargo",
        "run",
        "--quiet",
        "--offline",
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
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    result = next(event["result"] for event in reversed(events) if event["event"] == "task.finished")
    assert result["status"] == "success"
    return Path(result["outputs"][0])


def run_font_encrypt_task(
    backend: str,
    input_file: Path,
    output_dir: Path,
    target_families: list[str] | None = None,
) -> Path:
    output_dir.mkdir()
    options = {
        "target_font_families_by_file": {str(input_file): target_families or ["htt"]}
    }
    if backend == "python":
        return Path(run_python_task(input_file, output_dir, "encrypt_font", options)["outputs"][0])
    request = {
        "taskId": "real-rust-font-encrypt",
        "taskType": "encrypt_font",
        "inputFiles": [str(input_file)],
        "outputDir": str(output_dir),
        "options": options,
    }
    command = [
        "cargo",
        "run",
        "--quiet",
        "--offline",
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
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    result = next(event["result"] for event in reversed(events) if event["event"] == "task.finished")
    assert result["status"] == "success"
    return Path(result["outputs"][0])


def run_image_to_webp_task(backend: str, input_file: Path, output_dir: Path) -> Path:
    output_dir.mkdir()
    options = {"quality": 75}
    if backend == "python":
        return Path(run_python_task(input_file, output_dir, "image_to_webp", options)["outputs"][0])
    request = {
        "taskId": "real-rust-image-to-webp",
        "taskType": "image_to_webp",
        "inputFiles": [str(input_file)],
        "outputDir": str(output_dir),
        "options": options,
    }
    command = [
        "cargo",
        "run",
        "--quiet",
        "--offline",
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
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    result = next(event["result"] for event in reversed(events) if event["event"] == "task.finished")
    assert result["status"] == "success"
    return Path(result["outputs"][0])


def image_info(data: bytes) -> tuple[str, tuple[int, int], bool]:
    with Image.open(BytesIO(data)) as image:
        return image.format or "", image.size, "A" in image.getbands()


@pytest.mark.skipif(not REAL_INPUT.is_file(), reason="本地 fixtures 中没有真实 EPUB 样本")
def test_rust_reformat_matches_python_for_real_encrypted_epub(tmp_path: Path) -> None:
    python_output = run_task("python", REAL_INPUT, tmp_path / "python")
    rust_output = run_task("rust", REAL_INPUT, tmp_path / "rust")

    assert zipfile.is_zipfile(rust_output)
    with zipfile.ZipFile(python_output) as python_epub, zipfile.ZipFile(rust_output) as rust_epub:
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        for member in python_epub.namelist():
            assert python_epub.read(member) == rust_epub.read(member), member
        assert b'full-path="OEBPS/content.opf"' in rust_epub.read("META-INF/container.xml")


@pytest.mark.parametrize(
    ("input_file", "task_type"),
    [
        (REAL_INPUT, "decrypt_epub"),
        (REAL_DECRYPTED_INPUT, "encrypt_epub"),
    ],
)
def test_rust_file_rewrite_matches_python_for_real_epub(
    tmp_path: Path, input_file: Path, task_type: str
) -> None:
    if not input_file.is_file():
        pytest.skip("本地 fixtures 中没有真实 EPUB 样本")
    python_output = run_task("python", input_file, tmp_path / "python", task_type)
    rust_output = run_task("rust", input_file, tmp_path / "rust", task_type)

    with zipfile.ZipFile(python_output) as python_epub, zipfile.ZipFile(rust_output) as rust_epub:
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        for member in python_epub.namelist():
            assert python_epub.read(member) == rust_epub.read(member), member


@pytest.mark.skipif(
    not REAL_FONT_OCR_INPUT.is_file(), reason="本地 fixtures 中没有真实字体 OCR EPUB 样本"
)
def test_rust_font_ocr_matches_python_for_real_epub(tmp_path: Path) -> None:
    python_output = run_font_ocr_task("python", REAL_FONT_OCR_INPUT, tmp_path / "python")
    rust_output = run_font_ocr_task("rust", REAL_FONT_OCR_INPUT, tmp_path / "rust")

    assert zipfile.is_zipfile(rust_output)
    with zipfile.ZipFile(python_output) as python_epub, zipfile.ZipFile(rust_output) as rust_epub:
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        for member in python_epub.namelist():
            assert python_epub.read(member) == rust_epub.read(member), member
        assert "OEBPS/Fonts/htt.ttf" not in rust_epub.namelist()


@pytest.mark.skipif(
    not REAL_FONT_ENCRYPT_INPUT.is_file(), reason="本地 fixtures 中没有真实字体加密 EPUB 样本"
)
def test_rust_font_encrypt_matches_python_for_real_epub(tmp_path: Path) -> None:
    python_output = run_font_encrypt_task("python", REAL_FONT_ENCRYPT_INPUT, tmp_path / "python")
    rust_output = run_font_encrypt_task("rust", REAL_FONT_ENCRYPT_INPUT, tmp_path / "rust")

    with (
        zipfile.ZipFile(REAL_FONT_ENCRYPT_INPUT) as input_epub,
        zipfile.ZipFile(python_output) as python_epub,
        zipfile.ZipFile(rust_output) as rust_epub,
    ):
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        original_cmap = TTFont(BytesIO(input_epub.read("OEBPS/Fonts/htt.ttf"))).getBestCmap() or {}
        python_cmap = TTFont(BytesIO(python_epub.read("OEBPS/Fonts/htt.ttf"))).getBestCmap() or {}
        rust_cmap = TTFont(BytesIO(rust_epub.read("OEBPS/Fonts/htt.ttf"))).getBestCmap() or {}
        assert python_cmap != original_cmap
        assert rust_cmap != original_cmap
        assert len(rust_cmap) == len(python_cmap) == len(original_cmap)
        assert rust_epub.read("OEBPS/Text/lqz0001.xhtml") != input_epub.read(
            "OEBPS/Text/lqz0001.xhtml"
        )


@pytest.mark.skipif(
    not REAL_DECRYPTED_INPUT.is_file(), reason="本地 fixtures 中没有真实 OTF EPUB 样本"
)
def test_rust_cff_otf_encrypt_matches_python_invariants(tmp_path: Path) -> None:
    python_output = run_font_encrypt_task(
        "python", REAL_DECRYPTED_INPUT, tmp_path / "python", ["yy"]
    )
    rust_output = run_font_encrypt_task(
        "rust", REAL_DECRYPTED_INPUT, tmp_path / "rust", ["yy"]
    )

    with (
        zipfile.ZipFile(REAL_DECRYPTED_INPUT) as input_epub,
        zipfile.ZipFile(python_output) as python_epub,
        zipfile.ZipFile(rust_output) as rust_epub,
    ):
        original_cmap = TTFont(BytesIO(input_epub.read("OEBPS/Fonts/yy.otf"))).getBestCmap() or {}
        python_font = TTFont(BytesIO(python_epub.read("OEBPS/Fonts/yy.otf")))
        rust_font = TTFont(BytesIO(rust_epub.read("OEBPS/Fonts/yy.otf")))
        python_cmap = python_font.getBestCmap() or {}
        rust_cmap = rust_font.getBestCmap() or {}

        assert "CFF " in python_font
        assert "CFF " in rust_font
        assert python_cmap != original_cmap
        assert rust_cmap != original_cmap
        assert len(rust_cmap) == len(python_cmap) == len(original_cmap)


@pytest.mark.skipif(
    not REAL_DECRYPTED_INPUT.is_file(), reason="本地 fixtures 中没有真实 OTF EPUB 样本"
)
def test_rust_cff_otf_decrypt_keeps_python_low_confidence_review_artifacts(
    tmp_path: Path,
) -> None:
    python_output = run_font_ocr_task(
        "python", REAL_DECRYPTED_INPUT, tmp_path / "python", ["yy"]
    )
    rust_output = run_font_ocr_task(
        "rust", REAL_DECRYPTED_INPUT, tmp_path / "rust", ["yy"]
    )

    with zipfile.ZipFile(python_output) as python_epub, zipfile.ZipFile(rust_output) as rust_epub:
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        for epub in (python_epub, rust_epub):
            members = set(epub.namelist())
            failure_images = {
                member for member in members if member.startswith("OEBPS/Images/ocr-failures/")
            }
            assert "OEBPS/Fonts/yy.otf" not in members
            assert failure_images
            assert all(member.endswith(".png") for member in failure_images)
            html = b"\n".join(
                epub.read(member)
                for member in members
                if member.startswith("OEBPS/Text/") and member.endswith(".xhtml")
            )
            assert b'class="ocr-failure"' in html
            assert b'data-status="OCR_LOW_CONF"' in html
            opf = epub.read("OEBPS/content.opf")
            assert b'ocr_failure_' in opf
            assert b'Images/ocr-failures/' in opf

        html_members = sorted(
            member
            for member in python_epub.namelist()
            if member.startswith("OEBPS/Text/") and member.endswith(".xhtml")
        )
        for member in html_members:
            python_html = BeautifulSoup(python_epub.read(member), "html.parser")
            rust_html = BeautifulSoup(rust_epub.read(member), "html.parser")
            assert python_html.get_text() == rust_html.get_text(), member

            def failure_markers(soup: BeautifulSoup) -> list[tuple[str | None, ...]]:
                return [
                    (
                        marker.get("data-codepoint"),
                        marker.get("data-original-char"),
                        marker.get("data-status"),
                        marker.get("data-font-path"),
                        marker.img.get("src") if marker.img else None,
                        marker.img.get("alt") if marker.img else None,
                    )
                    for marker in soup.select("span.ocr-failure")
                ]

            assert failure_markers(python_html) == failure_markers(rust_html), member


@pytest.mark.skipif(
    not REAL_FONT_ENCRYPT_INPUT.is_file(), reason="本地 fixtures 中没有真实图片 EPUB 样本"
)
def test_rust_image_to_webp_matches_python_for_real_epub(tmp_path: Path) -> None:
    python_output = run_image_to_webp_task("python", REAL_FONT_ENCRYPT_INPUT, tmp_path / "python")
    rust_output = run_image_to_webp_task("rust", REAL_FONT_ENCRYPT_INPUT, tmp_path / "rust")

    with zipfile.ZipFile(python_output) as python_epub, zipfile.ZipFile(rust_output) as rust_epub:
        assert python_epub.testzip() is None
        assert rust_epub.testzip() is None
        assert set(python_epub.namelist()) == set(rust_epub.namelist())
        webp_members = [name for name in rust_epub.namelist() if name.lower().endswith(".webp")]
        assert webp_members
        for member in webp_members:
            assert image_info(python_epub.read(member)) == image_info(rust_epub.read(member))
        assert python_epub.read("OEBPS/content.opf") == rust_epub.read("OEBPS/content.opf")
