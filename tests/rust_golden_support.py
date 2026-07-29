"""Protocol-aware helpers shared by Rust/Python golden regression tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

_TASK_TYPE_NAMES = {
    "reformat_epub": "TASK_TYPE_REFORMAT_EPUB",
    "decrypt_epub": "TASK_TYPE_DECRYPT_EPUB",
    "encrypt_epub": "TASK_TYPE_ENCRYPT_EPUB",
    "encrypt_font": "TASK_TYPE_ENCRYPT_FONT",
    "decrypt_font": "TASK_TYPE_DECRYPT_FONT",
    "webp_to_img": "TASK_TYPE_WEBP_TO_IMG",
    "image_compress": "TASK_TYPE_IMAGE_COMPRESS",
    "image_to_webp": "TASK_TYPE_IMAGE_TO_WEBP",
    "chinese_convert": "TASK_TYPE_CHINESE_CONVERT",
    "replace_cover": "TASK_TYPE_REPLACE_COVER",
}


def run_python_task(
    input_file: Path,
    output_dir: Path,
    task_type: str,
    options: dict[str, object] | None = None,
) -> dict[str, Any]:
    request = {
        "protocolVersion": "PROTOCOL_VERSION_V1",
        "requestId": "python-golden",
        "runTask": {
            "taskId": "python-golden",
            "taskType": _TASK_TYPE_NAMES[task_type],
            "inputFiles": [str(input_file)],
            "outputDir": str(output_dir),
            "options": wire_options(task_type, options or {}),
        },
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "python_backend.cli",
            "run",
            "--requestJson",
            json.dumps(request),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    response = _last_json_line(completed.stdout)
    result = response.get("taskResult")
    assert isinstance(result, dict), response
    assert result["status"] == "success"
    return result


def run_python_font_targets(input_file: Path) -> dict[str, Any]:
    request = {
        "protocolVersion": "PROTOCOL_VERSION_V1",
        "requestId": "python-font-targets",
        "scanFonts": {"inputFiles": [str(input_file)]},
    }
    completed = subprocess.run(
        [sys.executable, "-m", "python_backend.cli", "serve"],
        cwd=REPO_ROOT,
        input=json.dumps(request) + "\n",
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    response = _last_json_line(completed.stdout)
    results = response.get("fontScanResult", {}).get("results", [])
    assert len(results) == 1, response
    result = results[0]
    return {
        "ok": result["ok"],
        "input_file": result["inputFile"],
        "font_families": result["fontFamilies"],
    }


def wire_options(task_type: str, options: dict[str, object]) -> dict[str, object]:
    if task_type in {"reformat_epub", "decrypt_epub", "encrypt_epub"}:
        return {"empty": {}}
    if task_type in {"encrypt_font", "decrypt_font"}:
        by_file = options.get("target_font_families_by_file", {})
        assert isinstance(by_file, dict)
        font: dict[str, object] = {
            "targetFontFamiliesByFile": {
                path: {"values": families} for path, families in by_file.items()
            },
        }
        if "target_font_families" in options:
            font["targetFontFamilies"] = options["target_font_families"]
        if "ocr_char_policy" in options:
            font["ocrCharPolicy"] = options["ocr_char_policy"]
        if "min_ocr_confidence" in options:
            font["minOcrConfidence"] = options["min_ocr_confidence"]
        return {"font": font}
    if task_type == "image_compress":
        return {
            "imageCompress": _without_none({
                "jpegQuality": options.get("jpeg_quality"),
                "webpQuality": options.get("webp_quality"),
                "pngToJpg": options.get("png_to_jpg"),
                "pngQuantize": options.get("png_quantize"),
            })
        }
    if task_type in {"webp_to_img", "image_to_webp"}:
        return {
            "imageConversion": _without_none({
                "quality": options.get("quality"),
                "pngQuantize": options.get("png_quantize"),
            })
        }
    if task_type == "chinese_convert":
        return {"chineseConvert": {"direction": options.get("direction")}}
    if task_type == "replace_cover":
        return {"replaceCover": {"coverPathByFile": options.get("cover_path_by_file", {})}}
    raise AssertionError(f"unsupported task type: {task_type}")


def _last_json_line(stdout: str) -> dict[str, Any]:
    lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    assert lines, "Python golden CLI did not emit a JSON response"
    return lines[-1]


def _without_none(values: dict[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in values.items() if value is not None}
