from __future__ import annotations

import os
import sys
import time
from importlib import import_module
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from python_backend.epub_metadata import mark_epub_generated_by_tool
from python_backend.json_output import dumps_json_line
from python_backend.protocol import TaskEvent, TaskRequest, TaskResult


def resolve_default_log_path() -> Path:
    override = os.environ.get("EPUB_TOOL_LOG_PATH")
    if override:
        return Path(override)

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("log.txt")

    return Path(__file__).resolve().parent.parent / "log.txt"


LOG_PATH = resolve_default_log_path()
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("EPUB_TOOL_LOG_PATH", str(LOG_PATH))
TASK_SUFFIX = {
    "reformat_epub": "_reformat.epub",
    "decrypt_epub": "_decrypt.epub",
    "encrypt_epub": "_encrypt.epub",
    "encrypt_font": "_font_encrypt.epub",
    "decrypt_font": "_font_decrypt.epub",
    "webp_to_img": "_webp_to_img.epub",
    "image_compress": "_image_compress.epub",
    "image_to_webp": "_img2webp.epub",
    "replace_cover": "_cover.epub",
}
TASK_LABELS = {
    "reformat_epub": "格式化",
    "decrypt_epub": "文件解密",
    "encrypt_epub": "文件加密",
    "encrypt_font": "字体加密",
    "decrypt_font": "字体解密",
    "image_compress": "图片压缩",
    "webp_to_img": "WebP 转图片",
    "image_to_webp": "图片转 WebP",
    "replace_cover": "更换封面",
    "chinese_convert": "简繁转换",

}
MODULE_PATHS = {
    "reformat_epub": "python_backend.services.reformat_epub",
    "decrypt_epub": "python_backend.services.decrypt_epub",
    "encrypt_epub": "python_backend.services.encrypt_epub",
    "encrypt_font": "python_backend.services.encrypt_font",
    "decrypt_font": "python_backend.services.decrypt_font",
    "webp_to_img": "python_backend.services.webp_to_img",
    "image_compress": "python_backend.services.image_compress",
    "image_to_webp": "python_backend.services.image_to_webp",
    "chinese_convert": "python_backend.services.chinese_convert",
    "replace_cover": "python_backend.services.replace_cover",
}
_LOADED_MODULES: dict[str, Any] = {}


class JsonLineEmitter:
    def emit(self, event: TaskEvent) -> None:
        sys.stdout.write(dumps_json_line(event.to_dict()) + "\n")
        sys.stdout.flush()


class BroadcastLogger:
    def __init__(
        self,
        emitter: JsonLineEmitter,
        task_id: str,
        context_provider: Callable[[], dict[str, Any]],
    ):
        self.emitter = emitter
        self.task_id = task_id
        self.context_provider = context_provider
        self.path = str(LOG_PATH)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        LOG_PATH.write_text(f"time: {current_time}\n", encoding="utf-8")

    def write(self, text: str) -> None:
        text = str(text).rstrip("\n")
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(f"{text}\n")
        context = self.context_provider()
        self.emitter.emit(
            TaskEvent(
                event="task.log",
                task_id=self.task_id,
                status="running",
                progress=context["progress"],
                message=text,
                current_file=context["current_file"],
                current_index=context["current_index"],
                total_files=context["total_files"],
                output_path=context["output_path"],
                level="info",
            )
        )


def load_module(task_type: str) -> Any:
    """按任务惰性加载服务模块，并在当前进程内复用。

    sidecar 既会处理实际任务，也会只读取字体列表。原先的实现每次都会
    导入全部任务服务模块，使轻任务和字体列表读取也承担 OCR 相关依赖的
    初始化成本。
    """
    module = _LOADED_MODULES.get(task_type)
    if module is not None:
        return module

    module_path = MODULE_PATHS.get(task_type)
    if module_path is None:
        raise ValueError(f"不支持的任务类型: {task_type}")

    try:
        module = import_module(module_path)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python 依赖未安装完整，请先执行 `python -m pip install -r requirements/requirements.txt`。"
        ) from exc

    _LOADED_MODULES[task_type] = module
    return module


@contextmanager
def patched_logger(task_type: str, logger: BroadcastLogger):
    """只替换当前任务模块的 logger，避免为日志配置而导入无关模块。"""
    module = load_module(task_type)
    original = module.logger
    module.logger = logger
    try:
        yield
    finally:
        module.logger = original


def build_expected_output_path(
    input_file: str, task_type: str, output_dir: str | None
) -> str | None:
    if task_type == "chinese_convert":
        return None
    suffix = TASK_SUFFIX.get(task_type)
    if suffix is None:
        return None
    input_path = Path(input_file)
    target_dir = Path(output_dir) if output_dir else input_path.parent
    return str(target_dir / f"{input_path.stem}{suffix}")


def get_task_output_suffix(task_type: str, options: dict[str, Any]) -> str | None:
    """返回当前任务请求唯一对应的输出后缀。"""
    if task_type == "chinese_convert":
        direction = options.get("direction")
        if direction == "s2t":
            return "_traditional.epub"
        if direction == "t2s":
            return "_simplified.epub"
        return None
    return TASK_SUFFIX.get(task_type)


def input_has_task_output_suffix(
    input_file: str, task_type: str, options: dict[str, Any]
) -> bool:
    suffix = get_task_output_suffix(task_type, options)
    return bool(suffix and Path(input_file).name.lower().endswith(suffix.lower()))


def build_request_output_path(
    input_file: str,
    task_type: str,
    output_dir: str | None,
    options: dict[str, Any],
) -> str | None:
    if task_type == "chinese_convert":
        suffix = get_task_output_suffix(task_type, options)
        if suffix is None:
            return None
        input_path = Path(input_file)
        target_dir = Path(output_dir) if output_dir else input_path.parent
        return str(target_dir / f"{input_path.stem}{suffix}")
    return build_expected_output_path(input_file, task_type, output_dir)


def _validate_quality(options: dict[str, Any], key: str, default: int = 82) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError(f"{key} 必须是 1 到 100 的整数")
    return value


def validate_task_options(task_type: str, options: dict[str, Any]) -> None:
    if not isinstance(options, dict):
        raise ValueError("options 必须是对象")
    if task_type == "image_compress":
        _validate_quality(options, "jpeg_quality")
        _validate_quality(options, "webp_quality")
        if "png_to_jpg" in options and not isinstance(options["png_to_jpg"], bool):
            raise ValueError("png_to_jpg 必须是布尔值")
    elif task_type == "image_to_webp":
        _validate_quality(options, "quality")
    elif task_type == "chinese_convert":
        if options.get("direction") not in {"s2t", "t2s"}:
            raise ValueError("direction 必须是 s2t 或 t2s")
    elif task_type == "replace_cover":
        mapping = options.get("cover_path_by_file")
        if not isinstance(mapping, dict):
            raise ValueError("cover_path_by_file 必须是按 EPUB 路径映射的对象")
        for input_path, cover_path in mapping.items():
            if not isinstance(input_path, str) or not isinstance(cover_path, str):
                raise ValueError("cover_path_by_file 的键和值必须是路径字符串")
            if not Path(cover_path).is_file():
                raise ValueError(f"封面文件不存在: {cover_path}")


def resolve_generated_output_path(
    input_file: str, task_type: str, output_dir: str | None
) -> str | None:
    candidates = []
    primary_output = build_expected_output_path(input_file, task_type, output_dir)
    fallback_output = build_expected_output_path(input_file, task_type, None)

    for candidate in (primary_output, fallback_output):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return primary_output


def build_progress(index: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((index / total) * 100, 2)


def normalize_target_map(raw_map: Any) -> dict[str, list[str]]:
    if not isinstance(raw_map, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, value in raw_map.items():
        if isinstance(value, list):
            normalized[os.path.normpath(key)] = [str(item) for item in value if item]
    return normalized


def list_font_targets(epub_path: str) -> dict[str, Any]:
    font_module = load_module("encrypt_font")
    result = font_module.list_epub_font_encrypt_targets(epub_path)
    return {
        "ok": True,
        "input_file": os.path.normpath(epub_path),
        "font_families": result.get("font_families", []),
    }


def iter_font_targets(epub_paths: list[str]):
    """逐本产生字体列表结果，使 CLI 能在一个 sidecar 中推送批量进度。"""
    total_files = len(epub_paths)
    for index, epub_path in enumerate(epub_paths, start=1):
        normalized_path = os.path.normpath(epub_path)
        try:
            result = list_font_targets(normalized_path)
        except Exception as exc:
            result = {
                "ok": False,
                "input_file": normalized_path,
                "font_families": [],
                "error": str(exc),
            }
        yield {
            "event": "font-targets.progress",
            "current_index": index,
            "total_files": total_files,
            "result": result,
        }


def list_font_targets_batch(epub_paths: list[str]) -> list[dict[str, Any]]:
    """保留可复用的批量 API，供 CLI 和测试调用。"""
    return [event["result"] for event in iter_font_targets(epub_paths)]


def execute_task(
    task_type: str,
    input_file: str,
    output_dir: str | None,
    options: dict[str, Any],
) -> Any:
    module = load_module(task_type)
    if module is None:
        raise ValueError(f"不支持的任务类型: {task_type}")
    func: Callable[..., Any] = module.run

    if task_type in ("encrypt_font", "decrypt_font"):
        target_map = normalize_target_map(options.get("target_font_families_by_file"))
        default_targets = options.get("target_font_families") or []
        targets = target_map.get(os.path.normpath(input_file), default_targets)
        if not targets:
            return "skip"
        kwargs = {"target_font_families": targets}
        if task_type == "decrypt_font":
            kwargs["ocr_options"] = options
        return func(input_file, output_dir, **kwargs)

    if task_type in {"image_compress", "image_to_webp", "chinese_convert"}:
        return func(input_file, output_dir, options=options)

    if task_type == "replace_cover":
        cover_map = {
            os.path.normpath(str(key)): str(value)
            for key, value in options["cover_path_by_file"].items()
        }
        cover_path = cover_map.get(os.path.normpath(input_file))
        if not cover_path:
            return "skip"
        return func(input_file, output_dir, cover_path=cover_path)

    return func(input_file, output_dir)


def run_task(request: TaskRequest) -> TaskResult:
    if request.task_type not in MODULE_PATHS:
        raise ValueError(f"不支持的任务类型: {request.task_type}")
    validate_task_options(request.task_type, request.options)
    emitter = JsonLineEmitter()
    total_files = len(request.input_files)
    context = {
        "current_file": None,
        "current_index": 0,
        "total_files": total_files,
        "progress": 0.0,
        "output_path": None,
    }
    logger = BroadcastLogger(emitter, request.task_id, lambda: context.copy())

    outputs: list[str] = []
    errors: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    success_count = 0

    emitter.emit(
        TaskEvent(
            event="task.started",
            task_id=request.task_id,
            status="started",
            progress=0,
            message=(
                f"正在加载{TASK_LABELS.get(request.task_type, request.task_type)}处理模块…"
            ),
            total_files=total_files,
        )
    )

    with patched_logger(request.task_type, logger):
        for index, input_file in enumerate(request.input_files, start=1):
            normalized_input = os.path.normpath(input_file)
            expected_output = build_request_output_path(
                normalized_input, request.task_type, request.output_dir, request.options
            )
            output_existed_before = bool(expected_output and os.path.exists(expected_output))
            context.update(
                {
                    "current_file": normalized_input,
                    "current_index": index,
                    "progress": build_progress(index - 1, total_files),
                    "output_path": expected_output,
                }
            )

            emitter.emit(
                TaskEvent(
                    event="task.file.started",
                    task_id=request.task_id,
                    status="running",
                    progress=context["progress"],
                    message=f"开始处理 {os.path.basename(normalized_input)}",
                    current_file=normalized_input,
                    current_index=index,
                    total_files=total_files,
                    output_path=expected_output,
                )
            )

            start_at = time.perf_counter()
            skip_message = "该文件在当前模式下无需处理，或未选择字体目标。"
            try:
                if not normalized_input.lower().endswith(".epub"):
                    raise ValueError("当前只支持 .epub 文件")
                if not os.path.exists(normalized_input):
                    raise FileNotFoundError(f"EPUB文件不存在: {normalized_input}")
                if input_has_task_output_suffix(
                    normalized_input, request.task_type, request.options
                ):
                    suffix = get_task_output_suffix(request.task_type, request.options)
                    skip_message = f"文件名已包含当前任务输出后缀 {suffix}，为避免重复执行已跳过。"
                    ret = "skip"
                else:
                    ret = execute_task(
                        request.task_type,
                        normalized_input,
                        request.output_dir,
                        request.options,
                    )
                duration_ms = int((time.perf_counter() - start_at) * 1000)
                context["progress"] = build_progress(index, total_files)

                if ret == 0:
                    actual_output = expected_output
                    if not actual_output or not os.path.isfile(actual_output):
                        raise RuntimeError("处理服务未生成预期输出文件")
                    if actual_output.lower().endswith(".epub"):
                        mark_epub_generated_by_tool(actual_output)
                    success_count += 1
                    if actual_output and actual_output not in outputs:
                        outputs.append(actual_output)
                    emitter.emit(
                        TaskEvent(
                            event="task.file.finished",
                            task_id=request.task_id,
                            status="success",
                            progress=context["progress"],
                            message=f"处理成功，用时 {duration_ms}ms",
                            current_file=normalized_input,
                            current_index=index,
                            total_files=total_files,
                            output_path=expected_output,
                        )
                    )
                elif ret == "skip":
                    skipped.append(
                        {
                            "input_file": normalized_input,
                            "message": skip_message,
                        }
                    )
                    emitter.emit(
                        TaskEvent(
                            event="task.file.finished",
                            task_id=request.task_id,
                            status="skip",
                            progress=context["progress"],
                            message=skip_message,
                            current_file=normalized_input,
                            current_index=index,
                            total_files=total_files,
                            output_path=expected_output,
                            level="warning",
                        )
                    )
                else:
                    errors.append(
                        {
                            "input_file": normalized_input,
                            "message": str(ret),
                        }
                    )
                    emitter.emit(
                        TaskEvent(
                            event="task.file.finished",
                            task_id=request.task_id,
                            status="error",
                            progress=context["progress"],
                            message=str(ret),
                            current_file=normalized_input,
                            current_index=index,
                            total_files=total_files,
                            output_path=expected_output,
                            level="error",
                        )
                    )
            except Exception as exc:
                error_message = str(exc)
                if expected_output and not output_existed_before:
                    try:
                        Path(expected_output).unlink(missing_ok=True)
                    except OSError as cleanup_exc:
                        error_message = f"{error_message}；清理失败产物失败: {cleanup_exc}"
                context["progress"] = build_progress(index, total_files)
                errors.append({"input_file": normalized_input, "message": error_message})
                emitter.emit(
                    TaskEvent(
                        event="task.file.finished",
                        task_id=request.task_id,
                        status="error",
                        progress=context["progress"],
                        message=error_message,
                        current_file=normalized_input,
                        current_index=index,
                        total_files=total_files,
                        output_path=expected_output,
                        level="error",
                    )
                )

    total = total_files
    success = success_count
    failed = len(errors)
    skipped_count = len(skipped)
    final_status = "error"
    if failed == 0 and skipped_count == 0:
        final_status = "success"
    elif failed == 0 and skipped_count > 0:
        final_status = "partial"
    elif success > 0 or skipped_count > 0:
        final_status = "partial"

    result = TaskResult(
        ok=failed == 0,
        status=final_status,
        outputs=outputs,
        errors=errors,
        skipped=skipped,
        summary={
            "total": total,
            "success": success,
            "failed": failed,
            "skipped": skipped_count,
        },
        log_path=str(LOG_PATH),
    )

    emitter.emit(
        TaskEvent(
            event="task.finished",
            task_id=request.task_id,
            status=final_status,
            progress=100,
            message="任务执行完成",
            total_files=total_files,
            result=result.to_dict(),
        )
    )
    return result
