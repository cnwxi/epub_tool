from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from python_backend.services.font.decrypt_font import OnnxGlyphOcrBackend


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"
MODEL_PATH = (
    REPO_ROOT
    / "src-tauri"
    / "bundle-resources"
    / "ocr-models"
    / "PP-OCRv6_small_rec_onnx"
    / "inference.onnx"
)
MODEL_DIR = MODEL_PATH.parent
RUNTIME_PATH = (
    REPO_ROOT
    / "src-tauri"
    / "binaries"
    / "epub-tool-python"
    / "_internal"
    / "onnxruntime"
    / "capi"
    / "libonnxruntime.1.27.0.dylib"
)


def test_rust_onnx_runtime_matches_python_ctc_argmax_for_bundled_model() -> None:
    if not RUNTIME_PATH.is_file():
        pytest.skip("本机构建的 Python sidecar ONNX Runtime 不可用")

    import onnxruntime as ort

    input_tensor = np.zeros((1, 3, 48, 320), dtype=np.float32)
    python_session = ort.InferenceSession(
        str(MODEL_PATH),
        providers=["CPUExecutionProvider"],
    )
    python_output = python_session.run(
        None,
        {python_session.get_inputs()[0].name: input_tensor},
    )[0]
    expected_token_ids = python_output[0].argmax(axis=-1)
    expected_scores = python_output[0].max(axis=-1)

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
            "--infer-ocr-model",
            str(MODEL_PATH),
            "--onnx-runtime",
            str(RUNTIME_PATH),
            "--ocr-tensor-shape",
            "3,48,320",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)

    assert actual["shape"] == list(python_output.shape)
    np.testing.assert_array_equal(actual["token_ids"], expected_token_ids)
    np.testing.assert_allclose(actual["scores"], expected_scores, rtol=0, atol=1e-6)


def test_rust_ocr_image_preprocess_and_runtime_match_python(tmp_path: Path) -> None:
    if not RUNTIME_PATH.is_file():
        pytest.skip("本机构建的 Python sidecar ONNX Runtime 不可用")

    import onnxruntime as ort

    pixels = np.fromfunction(
        lambda y, x, c: (x * 3 + y * 5 + c * 17) % 256,
        (48, 320, 3),
        dtype=int,
    ).astype(np.uint8)
    image = Image.fromarray(pixels, "RGB")
    image_path = tmp_path / "ocr-input.png"
    image.save(image_path)
    backend = OnnxGlyphOcrBackend.__new__(OnnxGlyphOcrBackend)
    backend.np = np
    backend.image_shape = [3, 48, 320]
    backend.image_mode = "RGB"
    backend.max_img_width = 3200
    python_input = backend.preprocess_image(image)
    python_session = ort.InferenceSession(
        str(MODEL_PATH),
        providers=["CPUExecutionProvider"],
    )
    python_output = python_session.run(
        None,
        {python_session.get_inputs()[0].name: python_input},
    )[0]

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
            "--preprocess-ocr-image",
            str(image_path),
            "--ocr-image-shape",
            "3,48,320",
            "--ocr-image-mode",
            "RGB",
            "--ocr-max-image-width",
            "3200",
            "--infer-ocr-model",
            str(MODEL_PATH),
            "--onnx-runtime",
            str(RUNTIME_PATH),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)

    assert actual["shape"] == list(python_output.shape)
    np.testing.assert_array_equal(actual["token_ids"], python_output[0].argmax(axis=-1))
    np.testing.assert_allclose(actual["scores"], python_output[0].max(axis=-1), rtol=0, atol=1e-6)


def test_rust_reusable_ocr_backend_matches_python_decoded_result(tmp_path: Path) -> None:
    if not RUNTIME_PATH.is_file():
        pytest.skip("本机构建的 Python sidecar ONNX Runtime 不可用")

    pixels = np.fromfunction(
        lambda y, x, c: (x * 11 + y * 7 + c * 23) % 256,
        (48, 320, 3),
        dtype=int,
    ).astype(np.uint8)
    image = Image.fromarray(pixels, "RGB")
    image_path = tmp_path / "recognize-input.png"
    image.save(image_path)
    python_backend = OnnxGlyphOcrBackend({"onnx_model_dir": str(MODEL_DIR)})
    expected = python_backend.recognize(image)

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
            "--recognize-ocr-image",
            str(image_path),
            "--ocr-model-dir",
            str(MODEL_DIR),
            "--onnx-runtime",
            str(RUNTIME_PATH),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)

    assert actual["text"] == expected.text
    assert actual["confidence"] == pytest.approx(expected.confidence, rel=0, abs=1e-6)
    assert actual["image_shape"] == [3, 48, 320]
    assert actual["image_mode"] == "BGR"
    assert actual["character_count"] == len(python_backend.characters)
