from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from build_tool.ocr_model_config import onnx_model_name, resolve_ocr_model_name
except ModuleNotFoundError:
    from ocr_model_config import onnx_model_name, resolve_ocr_model_name


REPO_ROOT = Path(__file__).resolve().parent.parent
TAURI_CONF = Path(
    os.environ.get(
        "EPUB_TOOL_TAURI_CONF_PATH",
        REPO_ROOT / "src-tauri" / "tauri.conf.json",
    )
)
OCR_RESOURCE_PREFIX = "bundle-resources/ocr-models/"


def configure_ocr_bundle_variant(config_path: Path = TAURI_CONF) -> str:
    model_name = resolve_ocr_model_name()
    model_dir_name = onnx_model_name(model_name)
    source = f"{OCR_RESOURCE_PREFIX}{model_dir_name}/*"
    target = f"ocr-models/{model_dir_name}/"

    config = json.loads(config_path.read_text(encoding="utf-8"))
    bundle = config.setdefault("bundle", {})
    resources = bundle.setdefault("resources", {})
    for key in list(resources):
        if key.startswith(OCR_RESOURCE_PREFIX):
            del resources[key]
    resources[source] = target

    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"OCR bundle variant configured: {model_name} -> {source}")
    return model_name


def main() -> int:
    configure_ocr_bundle_variant()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
