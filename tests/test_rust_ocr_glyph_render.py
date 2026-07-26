from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from python_backend.services.font.decrypt_font import (
    FontGlyphRenderer,
    OnnxGlyphOcrBackend,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"
MODEL_DIR = (
    REPO_ROOT
    / "src-tauri"
    / "bundle-resources"
    / "ocr-models"
    / "PP-OCRv6_small_rec_onnx"
)
REAL_ENCRYPTED_SAMPLE = REPO_ROOT / "fixtures" / "解密前_reformat_decrypt_encrypt_font.epub"
FONT_MEMBER = "OEBPS/Fonts/htt.ttf"
# This codepoint is present in the encrypted sample's cmap and maps to the
# original glyph for “中”.  It gives the renderer a real encrypted TTF glyph
# rather than a synthetic test outline.
OBFUSCATED_CHAR = "겳"


def run_rust(*arguments: str) -> dict:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--offline",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--bin",
            "rust-task-runner",
            "--",
            *arguments,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_rust_renders_and_recognizes_real_encrypted_ttf_glyph_like_python(
    tmp_path: Path,
) -> None:
    if not REAL_ENCRYPTED_SAMPLE.is_file():
        pytest.skip("本地真实 EPUB 样本不可用")

    font_path = tmp_path / "encrypted.ttf"
    with zipfile.ZipFile(REAL_ENCRYPTED_SAMPLE) as epub:
        font_path.write_bytes(epub.read(FONT_MEMBER))

    rust_image_path = tmp_path / "rust-glyph.png"
    render = run_rust(
        "--render-font-glyph",
        str(font_path),
        "--glyph",
        OBFUSCATED_CHAR,
        "--glyph-output",
        str(rust_image_path),
    )
    assert rust_image_path.is_file()
    assert render["period_like"] is False

    python_image = FontGlyphRenderer(font_path.read_bytes(), str(font_path)).render(
        OBFUSCATED_CHAR
    )
    python_result = OnnxGlyphOcrBackend({"onnx_model_dir": str(MODEL_DIR)}).recognize(
        python_image
    )
    rust_result = run_rust(
        "--recognize-ocr-image",
        str(rust_image_path),
        "--ocr-model-dir",
        str(MODEL_DIR),
    )

    assert python_result.text == rust_result["text"] == "中"
    assert python_result.confidence > 0.99
    assert rust_result["confidence"] > 0.99
    assert rust_result["confidence"] == pytest.approx(
        python_result.confidence, rel=0, abs=1e-3
    )
