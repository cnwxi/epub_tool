from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

try:
    from build_tool.ocr_model_config import (
        onnx_model_dir,
        onnx_model_name,
        ocr_model_dir,
        resolve_ocr_model_name,
    )
except ModuleNotFoundError:
    from ocr_model_config import (
        onnx_model_dir,
        onnx_model_name,
        ocr_model_dir,
        resolve_ocr_model_name,
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_NAME = resolve_ocr_model_name()
ONNX_MODEL_NAME = onnx_model_name(MODEL_NAME)
PADDLE_MODEL_DIR = ocr_model_dir(MODEL_NAME)
ONNX_MODEL_DIR = onnx_model_dir(MODEL_NAME)
ONNX_MODEL_FILE = ONNX_MODEL_DIR / "inference.onnx"
CONFIG_FILE_NAME = "inference.yml"


def onnx_model_is_ready() -> bool:
    return ONNX_MODEL_FILE.is_file() and (ONNX_MODEL_DIR / CONFIG_FILE_NAME).is_file()


def ensure_paddle_model() -> None:
    missing = [
        name
        for name in ("inference.json", "inference.pdiparams", CONFIG_FILE_NAME)
        if not (PADDLE_MODEL_DIR / name).is_file()
    ]
    if missing:
        raise SystemExit(
            "Paddle 源模型文件不完整: "
            + ", ".join(missing)
            + "。请先运行 `npm run maintenance:fetch-ocr-model`。"
        )


def run_paddle2onnx() -> None:
    ONNX_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    paddle2onnx = shutil.which("paddle2onnx")
    if not paddle2onnx:
        raise SystemExit(
            "未找到 paddle2onnx CLI。刷新 OCR 模型时，请在 conda epub_tool 环境安装 "
            "`requirements-ocr-conversion.txt`。"
        )

    command = [
        paddle2onnx,
        "--model_dir",
        str(PADDLE_MODEL_DIR),
        "--model_filename",
        "inference.json",
        "--params_filename",
        "inference.pdiparams",
        "--save_file",
        str(ONNX_MODEL_FILE),
        "--opset_version",
        os.environ.get("EPUB_TOOL_ONNX_OPSET_VERSION", "7"),
        "--optimize_tool",
        os.environ.get("EPUB_TOOL_ONNX_OPTIMIZE_TOOL", "None"),
    ]
    env = os.environ.copy()
    env.setdefault("MPLCONFIGDIR", str(REPO_ROOT / "build" / "python-sidecar" / "cache" / "matplotlib"))
    Path(env["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "Paddle2ONNX 转换失败。请确认当前命令运行在 conda epub_tool 环境，"
            "且已安装 requirements-ocr-conversion.txt。"
        ) from exc


def copy_runtime_config() -> None:
    shutil.copy2(PADDLE_MODEL_DIR / CONFIG_FILE_NAME, ONNX_MODEL_DIR / CONFIG_FILE_NAME)


def prepare_ocr_onnx_models() -> Path:
    ensure_paddle_model()
    if onnx_model_is_ready():
        print(f"ONNX OCR model reused: {ONNX_MODEL_DIR}")
        return ONNX_MODEL_DIR

    run_paddle2onnx()
    copy_runtime_config()
    if not onnx_model_is_ready():
        raise SystemExit(f"ONNX OCR model preparation failed: {ONNX_MODEL_DIR}")
    print(f"ONNX OCR model prepared: {ONNX_MODEL_DIR}")
    return ONNX_MODEL_DIR


def main() -> int:
    prepare_ocr_onnx_models()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
