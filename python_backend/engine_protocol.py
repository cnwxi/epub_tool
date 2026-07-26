"""Adapters between the generated v1 wire contract and Python engine models.

Only this module knows both protobuf JSON names and the legacy internal
snake_case dataclasses. Services stay isolated from transport concerns.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from google.protobuf.json_format import MessageToDict, ParseDict

from python_backend.generated.epub_tool.v1 import engine_pb2
from python_backend.protocol import TaskEvent, TaskRequest, TaskResult


PROTOCOL_VERSION = engine_pb2.PROTOCOL_VERSION_V1

_TASK_TYPE_BY_WIRE = {
    engine_pb2.TASK_TYPE_REFORMAT_EPUB: "reformat_epub",
    engine_pb2.TASK_TYPE_DECRYPT_EPUB: "decrypt_epub",
    engine_pb2.TASK_TYPE_ENCRYPT_EPUB: "encrypt_epub",
    engine_pb2.TASK_TYPE_ENCRYPT_FONT: "encrypt_font",
    engine_pb2.TASK_TYPE_DECRYPT_FONT: "decrypt_font",
    engine_pb2.TASK_TYPE_WEBP_TO_IMG: "webp_to_img",
    engine_pb2.TASK_TYPE_IMAGE_COMPRESS: "image_compress",
    engine_pb2.TASK_TYPE_IMAGE_TO_WEBP: "image_to_webp",
    engine_pb2.TASK_TYPE_CHINESE_CONVERT: "chinese_convert",
    engine_pb2.TASK_TYPE_REPLACE_COVER: "replace_cover",
}


class EngineProtocolError(ValueError):
    """A client supplied a syntactically valid but unsupported wire message."""


def parse_engine_request(payload: Mapping[str, Any]) -> engine_pb2.EngineRequest:
    request = engine_pb2.EngineRequest()
    try:
        ParseDict(dict(payload), request, ignore_unknown_fields=False)
    except (TypeError, ValueError) as exc:
        raise EngineProtocolError(f"无效的引擎请求: {exc}") from exc
    validate_engine_request(request)
    return request


def validate_engine_request(request: engine_pb2.EngineRequest) -> None:
    if request.protocol_version != PROTOCOL_VERSION:
        raise EngineProtocolError("不支持的 protocolVersion")
    if not request.request_id:
        raise EngineProtocolError("请求缺少 requestId")
    if request.WhichOneof("operation") is None:
        raise EngineProtocolError("请求缺少 operation")


def task_request_from_wire(request: engine_pb2.RunTaskRequest) -> TaskRequest:
    task_type = _TASK_TYPE_BY_WIRE.get(request.task_type)
    if task_type is None:
        raise EngineProtocolError("runTask 请求包含不支持的 taskType")
    if not request.task_id:
        raise EngineProtocolError("runTask 请求缺少 taskId")
    return TaskRequest(
        task_id=request.task_id,
        task_type=task_type,
        input_files=list(request.input_files),
        output_dir=request.output_dir if request.HasField("output_dir") else None,
        options=task_options_from_wire(task_type, request.options),
    )


def task_options_from_wire(
    task_type: str, options: engine_pb2.TaskOptions
) -> dict[str, Any]:
    kind = options.WhichOneof("kind")
    expected_kind = {
        "encrypt_font": "font",
        "decrypt_font": "font",
        "image_compress": "image_compress",
        "webp_to_img": "image_conversion",
        "image_to_webp": "image_conversion",
        "chinese_convert": "chinese_convert",
        "replace_cover": "replace_cover",
    }.get(task_type, "empty")
    if kind != expected_kind:
        raise EngineProtocolError(
            f"{task_type} 必须使用 {expected_kind} options，而不是 {kind or 'empty'}"
        )

    if kind == "empty":
        return {}
    if kind == "font":
        font = options.font
        result: dict[str, Any] = {
            "target_font_families_by_file": {
                path: list(families.values)
                for path, families in font.target_font_families_by_file.items()
            },
            "target_font_families": list(font.target_font_families),
        }
        if font.HasField("ocr_char_policy"):
            result["ocr_char_policy"] = font.ocr_char_policy
        if font.HasField("min_ocr_confidence"):
            result["min_ocr_confidence"] = font.min_ocr_confidence
        return result
    if kind == "image_compress":
        image = options.image_compress
        result = {}
        for wire_name, internal_name in (
            ("jpeg_quality", "jpeg_quality"),
            ("webp_quality", "webp_quality"),
            ("png_to_jpg", "png_to_jpg"),
            ("png_quantize", "png_quantize"),
        ):
            if image.HasField(wire_name):
                result[internal_name] = getattr(image, wire_name)
        return result
    if kind == "image_conversion":
        image = options.image_conversion
        result = {}
        if image.HasField("quality"):
            result["quality"] = image.quality
        if image.HasField("png_quantize"):
            result["png_quantize"] = image.png_quantize
        return result
    if kind == "chinese_convert":
        if not options.chinese_convert.HasField("direction"):
            raise EngineProtocolError("chineseConvert options 缺少 direction")
        return {"direction": options.chinese_convert.direction}
    if kind == "replace_cover":
        return {"cover_path_by_file": dict(options.replace_cover.cover_path_by_file)}
    raise AssertionError(f"未处理的 options 类型: {kind}")


def task_result_to_wire(result: TaskResult) -> engine_pb2.TaskResult:
    wire = engine_pb2.TaskResult(
        ok=result.ok,
        status=result.status,
        outputs=result.outputs,
        summary=engine_pb2.TaskSummary(**result.summary),
    )
    wire.errors.extend(engine_pb2.FileIssue(**issue) for issue in result.errors)
    wire.skipped.extend(engine_pb2.FileIssue(**issue) for issue in result.skipped)
    if result.log_path is not None:
        wire.log_path = result.log_path
    return wire


def task_event_to_wire(event: TaskEvent) -> engine_pb2.TaskEvent:
    wire = engine_pb2.TaskEvent(
        event=event.event,
        task_id=event.task_id,
        status=event.status,
        progress=event.progress,
        message=event.message,
        level=event.level,
    )
    if event.current_file is not None:
        wire.current_file = event.current_file
    if event.current_index is not None:
        wire.current_index = event.current_index
    if event.total_files is not None:
        wire.total_files = event.total_files
    if event.output_path is not None:
        wire.output_path = event.output_path
    if event.result is not None:
        wire.result.CopyFrom(task_result_to_wire(event.result))
    return wire


def font_target_to_wire(result: Mapping[str, Any]) -> engine_pb2.FontTargetResult:
    wire = engine_pb2.FontTargetResult(
        ok=bool(result["ok"]),
        input_file=str(result["input_file"]),
        font_families=[str(value) for value in result.get("font_families", [])],
    )
    if result.get("error") is not None:
        wire.error = str(result["error"])
    return wire


def message_to_json_dict(message: Any) -> dict[str, Any]:
    """Use protobuf's canonical lower-camel JSON mapping at the wire edge."""
    return MessageToDict(
        message,
        preserving_proto_field_name=False,
        always_print_fields_with_no_presence=True,
    )
