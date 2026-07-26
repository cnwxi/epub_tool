from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from python_backend.services.font.decrypt_font import OnnxGlyphOcrBackend


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"


def rust_preprocess(
    image_path: Path,
    image_shape: list[int],
    image_mode: str,
    max_image_width: int,
) -> np.ndarray:
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
            ",".join(map(str, image_shape)),
            "--ocr-image-mode",
            image_mode,
            "--ocr-max-image-width",
            str(max_image_width),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    return np.array(result["data"], dtype=np.float32).reshape(result["shape"])


def python_preprocess(
    image: Image.Image,
    image_shape: list[int],
    image_mode: str,
    max_image_width: int,
) -> np.ndarray:
    backend = OnnxGlyphOcrBackend.__new__(OnnxGlyphOcrBackend)
    backend.np = np
    backend.image_shape = image_shape
    backend.image_mode = image_mode
    backend.max_img_width = max_image_width
    return backend.preprocess_image(image)[0]


def test_rust_ocr_preprocess_stays_within_one_input_level_of_python_for_non_integer_resize_and_bgr(
    tmp_path: Path,
) -> None:
    pixels = np.array(
        [
            [[0, 10, 20], [30, 40, 50], [60, 70, 80], [90, 100, 110], [120, 130, 140], [150, 160, 170], [180, 190, 200]],
            [[5, 15, 25], [35, 45, 55], [65, 75, 85], [95, 105, 115], [125, 135, 145], [155, 165, 175], [185, 195, 205]],
            [[10, 20, 30], [40, 50, 60], [70, 80, 90], [100, 110, 120], [130, 140, 150], [160, 170, 180], [190, 200, 210]],
            [[15, 25, 35], [45, 55, 65], [75, 85, 95], [105, 115, 125], [135, 145, 155], [165, 175, 185], [195, 205, 215]],
        ],
        dtype=np.uint8,
    )
    image = Image.fromarray(pixels, "RGB")
    image_path = tmp_path / "gradient.png"
    image.save(image_path)

    image_shape = [3, 3, 4]
    expected = python_preprocess(image, image_shape, "BGR", 4)
    actual = rust_preprocess(image_path, image_shape, "BGR", 4)

    assert actual.shape == expected.shape == (3, 3, 4)
    # `image`'s Triangle sampler and Pillow BILINEAR use different fixed-point
    # coefficient rounding. The Rust inference path remains disabled until an
    # exact Pillow-compatible sampler is adopted; this bounds the independent
    # preprocessing implementation without falsely claiming byte equality.
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1 / 127.5 + 1e-6)


def test_rust_ocr_preprocess_uses_python_bankers_rounding_for_resized_width(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (1, 2), (0, 0, 0))
    image_path = tmp_path / "half-width.png"
    image.save(image_path)

    image_shape = [3, 3, 4]
    expected = python_preprocess(image, image_shape, "RGB", 4)
    actual = rust_preprocess(image_path, image_shape, "RGB", 4)

    np.testing.assert_allclose(actual, expected, rtol=0, atol=0)
    # 3 * (1 / 2) is 1.5, and Python round() must resolve it to the even width 2.
    assert np.all(actual[:, :, :2] == -1.0)
    assert np.all(actual[:, :, 2:] == 0.0)
