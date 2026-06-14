from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OCR_MODEL_NAME = "PP-OCRv6_small_rec"
HIGH_ACCURACY_OCR_MODEL_NAME = "PP-OCRv6_medium_rec"
OCR_MODEL_URLS = {
    DEFAULT_OCR_MODEL_NAME: (
        "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
        "official_inference_model/paddle3.0.0/PP-OCRv6_small_rec_infer.tar"
    ),
    HIGH_ACCURACY_OCR_MODEL_NAME: (
        "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
        "official_inference_model/paddle3.0.0/PP-OCRv6_medium_rec_infer.tar"
    ),
}


def resolve_ocr_model_name() -> str:
    model_name = os.environ.get("EPUB_TOOL_OCR_MODEL_NAME", DEFAULT_OCR_MODEL_NAME).strip()
    if model_name not in OCR_MODEL_URLS:
        supported = ", ".join(sorted(OCR_MODEL_URLS))
        raise SystemExit(f"不支持的 OCR 模型 {model_name!r}。可选模型: {supported}")
    return model_name


def ocr_model_url(model_name: str) -> str:
    return OCR_MODEL_URLS[model_name]


def ocr_model_dir(model_name: str) -> Path:
    return REPO_ROOT / "src-tauri" / "bundle-resources" / "ocr-models" / model_name


def onnx_model_name(model_name: str) -> str:
    return f"{model_name}_onnx"


def onnx_model_dir(model_name: str) -> Path:
    return REPO_ROOT / "src-tauri" / "bundle-resources" / "ocr-models" / onnx_model_name(model_name)
