from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from python_backend.engine_protocol import (
    PROTOCOL_VERSION,
    EngineProtocolError,
    font_target_to_wire,
    message_to_json_dict,
    parse_engine_request,
    task_event_to_wire,
    task_request_from_wire,
    task_result_to_wire,
)
from python_backend.generated.epub_tool.v1 import engine_pb2
from python_backend.json_output import dumps_json_line
from python_backend.task_runner import iter_font_targets, run_task


PARENT_LIVENESS_ADDR_ENV = "EPUB_TOOL_PARENT_LIVENESS_ADDR"
PARENT_LIVENESS_TOKEN_ENV = "EPUB_TOOL_PARENT_LIVENESS_TOKEN"
LOGGER = logging.getLogger(__name__)

ERROR_CODE_INVALID_ARGUMENT = "INVALID_ARGUMENT"
ERROR_CODE_IO = "IO_ERROR"
ERROR_CODE_DEPENDENCY = "DEPENDENCY_ERROR"
ERROR_CODE_INTERNAL = "INTERNAL"


def configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        errors = "strict" if stream_name == "stdin" else "replace"
        stream.reconfigure(encoding="utf-8", errors=errors)


def monitor_parent_liveness(address: str, token: str) -> None:
    """Exit when Rust closes its liveness connection, regardless of process ancestry."""
    try:
        host, raw_port = address.rsplit(":", 1)
        with socket.create_connection((host, int(raw_port)), timeout=10) as connection:
            connection.sendall(f"{token}\n".encode("utf-8"))
            # create_connection keeps its connect timeout; liveness reads must wait indefinitely.
            connection.settimeout(None)
            while connection.recv(4096):
                pass
    except (OSError, ValueError):
        # Without the liveness connection, keeping the worker running risks an orphan.
        pass
    os._exit(0)


def start_parent_monitor() -> None:
    address = os.environ.get(PARENT_LIVENESS_ADDR_ENV, "").strip()
    token = os.environ.get(PARENT_LIVENESS_TOKEN_ENV, "")
    if not address or not token:
        return
    threading.Thread(
        target=monitor_parent_liveness,
        args=(address, token),
        name="epub-tool-parent-monitor",
        daemon=True,
    ).start()


def load_engine_request_from_args(args: argparse.Namespace) -> engine_pb2.EngineRequest:
    if args.request_file:
        raw = Path(args.request_file).read_text(encoding="utf-8")
        payload = json.loads(raw)
    elif args.request_json:
        payload = json.loads(args.request_json)
    else:
        payload = {
            "protocolVersion": "PROTOCOL_VERSION_V1",
            "requestId": args.request_id,
            "runTask": {
                "taskId": args.task_id,
                "taskType": args.task_type,
                "inputFiles": args.input_file or [],
                "options": json.loads(args.options_json or '{"empty": {}}'),
            },
        }
        if args.output_dir:
            payload["runTask"]["outputDir"] = args.output_dir

    if not isinstance(payload, dict):
        raise EngineProtocolError("引擎请求必须是 JSON 对象")
    return parse_engine_request(payload)


class EngineEventEmitter:
    def __init__(self, request_id: str):
        self.request_id = request_id

    def emit(self, event) -> None:
        envelope = engine_pb2.EngineEvent(
            protocol_version=PROTOCOL_VERSION,
            request_id=self.request_id,
            task_event=task_event_to_wire(event),
        )
        emit_json_message(envelope)


def emit_json_message(message: Any) -> None:
    sys.stdout.write(dumps_json_line(message_to_json_dict(message)) + "\n")
    sys.stdout.flush()


def engine_error_response(
    request_id: str,
    code: str,
    message: str,
) -> engine_pb2.EngineResponse:
    return engine_pb2.EngineResponse(
        protocol_version=PROTOCOL_VERSION,
        request_id=request_id,
        error=engine_pb2.EngineError(code=code, message=message),
    )


def is_dependency_error(error: Exception) -> bool:
    """Return whether an exception was caused by a missing Python dependency."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, ImportError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def engine_error_from_exception(
    request_id: str,
    error: Exception,
) -> engine_pb2.EngineResponse:
    """Convert a request failure into a stable response without killing the worker."""
    if isinstance(error, (EngineProtocolError, TypeError, ValueError)):
        return engine_error_response(request_id, ERROR_CODE_INVALID_ARGUMENT, str(error))
    if isinstance(error, OSError):
        return engine_error_response(request_id, ERROR_CODE_IO, str(error))
    if is_dependency_error(error):
        return engine_error_response(request_id, ERROR_CODE_DEPENDENCY, str(error))

    LOGGER.error(
        "Engine request %s failed unexpectedly",
        request_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    return engine_error_response(
        request_id,
        ERROR_CODE_INTERNAL,
        "处理引擎发生未预期错误，请查看日志。",
    )


def process_engine_request(
    request: engine_pb2.EngineRequest,
) -> engine_pb2.EngineResponse:
    operation = request.WhichOneof("operation")
    if operation == "run_task":
        result = run_task(
            task_request_from_wire(request.run_task),
            emitter=EngineEventEmitter(request.request_id),
        )
        return engine_pb2.EngineResponse(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            task_result=task_result_to_wire(result),
        )
    if operation == "scan_fonts":
        results = []
        for event in iter_font_targets(list(request.scan_fonts.input_files)):
            progress = engine_pb2.FontScanProgress(
                current_index=event["current_index"],
                total_files=event["total_files"],
                result=font_target_to_wire(event["result"]),
            )
            emit_json_message(
                engine_pb2.EngineEvent(
                    protocol_version=PROTOCOL_VERSION,
                    request_id=request.request_id,
                    font_scan_progress=progress,
                )
            )
            results.append(progress.result)
        return engine_pb2.EngineResponse(
            protocol_version=PROTOCOL_VERSION,
            request_id=request.request_id,
            font_scan_result=engine_pb2.FontScanResult(results=results),
        )
    raise EngineProtocolError("不支持的 operation")


def execute_engine_request(request: engine_pb2.EngineRequest) -> engine_pb2.EngineResponse:
    """Contain execution failures at the worker protocol boundary.

    Service code is extensible and may raise exceptions outside per-file handling
    (for example while preparing an output directory or importing a task module).
    The persistent worker must answer the request and remain usable in those cases.
    """
    try:
        return process_engine_request(request)
    except (EngineProtocolError, OSError, ImportError, TypeError, ValueError) as exc:
        return engine_error_from_exception(request.request_id, exc)
    except Exception as exc:  # Protocol boundary: preserve worker availability for plugin failures.
        return engine_error_from_exception(request.request_id, exc)


def cmd_run(args: argparse.Namespace) -> int:
    request_id = "invalid-request"
    try:
        request = load_engine_request_from_args(args)
        request_id = request.request_id
    except (EngineProtocolError, OSError, TypeError, ValueError) as exc:
        response = engine_error_from_exception(request_id, exc)
    else:
        response = execute_engine_request(request)
    emit_json_message(response)
    if response.WhichOneof("payload") == "task_result":
        return 0 if response.task_result.ok else 1
    return 0 if response.WhichOneof("payload") != "error" else 1


def cmd_serve(_args: argparse.Namespace) -> int:
    """Process serialized EngineRequest messages in request order."""
    start_parent_monitor()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id = "invalid-request"
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise EngineProtocolError("引擎请求必须是 JSON 对象")
            request_id = str(payload.get("requestId") or request_id)
            request = parse_engine_request(payload)
        except (EngineProtocolError, TypeError, ValueError) as exc:
            response = engine_error_from_exception(request_id, exc)
        else:
            response = execute_engine_request(request)
        emit_json_message(response)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="epub_tool Tauri bridge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行 EPUB 处理任务")
    run_parser.add_argument("--requestJson", dest="request_json", help="完整 EngineRequest JSON")
    run_parser.add_argument("--requestFile", dest="request_file", help="包含 EngineRequest JSON 的文件")
    run_parser.add_argument(
        "--taskType",
        dest="task_type",
        choices=[
            "TASK_TYPE_REFORMAT_EPUB",
            "TASK_TYPE_DECRYPT_EPUB",
            "TASK_TYPE_ENCRYPT_EPUB",
            "TASK_TYPE_ENCRYPT_FONT",
            "TASK_TYPE_DECRYPT_FONT",
            "TASK_TYPE_WEBP_TO_IMG",
            "TASK_TYPE_IMAGE_COMPRESS",
            "TASK_TYPE_IMAGE_TO_WEBP",
            "TASK_TYPE_CHINESE_CONVERT",
            "TASK_TYPE_REPLACE_COVER",
        ],
    )
    run_parser.add_argument("--requestId", dest="request_id")
    run_parser.add_argument("--taskId", dest="task_id")
    run_parser.add_argument("--inputFile", dest="input_file", action="append")
    run_parser.add_argument("--outputDir", dest="output_dir")
    run_parser.add_argument("--optionsJson", dest="options_json", help="TaskOptions JSON")
    run_parser.set_defaults(func=cmd_run)

    serve_parser = subparsers.add_parser("serve", help="作为常驻 JSON Lines worker 运行")
    serve_parser.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
