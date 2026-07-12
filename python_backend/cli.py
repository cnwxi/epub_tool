from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from python_backend.protocol import TaskRequest
from python_backend.task_runner import iter_font_targets, list_font_targets, run_task


PARENT_LIVENESS_ADDR_ENV = "EPUB_TOOL_PARENT_LIVENESS_ADDR"
PARENT_LIVENESS_TOKEN_ENV = "EPUB_TOOL_PARENT_LIVENESS_TOKEN"


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        stream.reconfigure(encoding="utf-8", errors="replace")


def monitor_parent_liveness(address: str, token: str) -> None:
    """Exit when Rust closes its liveness connection, regardless of process ancestry."""
    try:
        host, raw_port = address.rsplit(":", 1)
        with socket.create_connection((host, int(raw_port)), timeout=10) as connection:
            connection.sendall(f"{token}\n".encode("utf-8"))
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


def pick_payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def load_request_from_payload(payload: dict[str, Any]) -> TaskRequest:
    task_type = pick_payload_value(payload, "task_type", "taskType")
    if not task_type:
        raise ValueError("任务请求缺少 task_type/taskType")

    return TaskRequest(
        task_id=pick_payload_value(payload, "task_id", "taskId", default=str(uuid.uuid4())),
        task_type=task_type,
        input_files=pick_payload_value(payload, "input_files", "inputFiles", default=[])
        or [],
        output_dir=pick_payload_value(payload, "output_dir", "outputDir"),
        options=pick_payload_value(payload, "options", default={}) or {},
    )


def load_request_from_args(args: argparse.Namespace) -> TaskRequest:
    if args.request_file:
        raw = Path(args.request_file).read_text(encoding="utf-8")
        payload = json.loads(raw)
    elif args.request_json:
        payload = json.loads(args.request_json)
    else:
        payload = {
            "task_id": args.task_id or str(uuid.uuid4()),
            "task_type": args.task_type,
            "input_files": args.input_file or [],
            "output_dir": args.output_dir,
            "options": json.loads(args.options_json or "{}"),
        }

    return load_request_from_payload(payload)


def cmd_run(args: argparse.Namespace) -> int:
    request = load_request_from_args(args)
    result = run_task(request)
    return 0 if result.ok else 1


def cmd_list_fonts_batch(args: argparse.Namespace) -> int:
    results = []
    for event in iter_font_targets(args.input_files):
        results.append(event["result"])
        sys.stdout.write(json.dumps(event, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    sys.stdout.write(
        json.dumps(
            {"event": "font-targets.finished", "font_targets": results},
            ensure_ascii=True,
        )
        + "\n"
    )
    sys.stdout.flush()
    return 0


def cmd_list_fonts(args: argparse.Namespace) -> int:
    payload = list_font_targets(args.input_file)
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return 0


def emit_worker_response(request_id: str, *, result: Any = None, error: str | None = None) -> None:
    payload: dict[str, Any] = {
        "event": "worker.response",
        "request_id": request_id,
        "ok": error is None,
    }
    if error is None:
        payload["result"] = result
    else:
        payload["error"] = error
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def cmd_serve(_args: argparse.Namespace) -> int:
    """处理来自 Tauri 的长连接 JSON Lines 请求。

    请求按顺序执行，保证现有服务模块的全局 logger 替换和日志文件写入不发生
    并发冲突。任务事件会直接复用既有 stdout 协议，最后再发送 worker.response。
    """
    start_parent_monitor()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        request_id = "unknown"
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("worker 请求必须是 JSON 对象")
            request_id = str(payload.get("request_id") or uuid.uuid4())
            command = payload.get("command")

            if command == "run":
                request_payload = payload.get("request")
                if not isinstance(request_payload, dict):
                    raise ValueError("run 请求缺少 request 对象")
                result = run_task(load_request_from_payload(request_payload))
                emit_worker_response(request_id, result=result.to_dict())
            elif command == "list-fonts-batch":
                input_files = payload.get("input_files")
                if not isinstance(input_files, list):
                    raise ValueError("list-fonts-batch 请求缺少 input_files 数组")
                results = []
                for event in iter_font_targets([str(path) for path in input_files]):
                    results.append(event["result"])
                    sys.stdout.write(json.dumps(event, ensure_ascii=True) + "\n")
                    sys.stdout.flush()
                emit_worker_response(request_id, result=results)
            else:
                raise ValueError(f"不支持的 worker 命令: {command}")
        except Exception as exc:
            emit_worker_response(request_id, error=str(exc))

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="epub_tool Tauri bridge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行 EPUB 处理任务")
    run_parser.add_argument("--request-json", help="完整 TaskRequest JSON")
    run_parser.add_argument("--request-file", help="包含 TaskRequest JSON 的文件")
    run_parser.add_argument(
        "--task-type",
        choices=[
            "reformat",
            "decrypt",
            "encrypt",
            "font_encrypt",
            "font_decrypt",
            "transfer_img",
        ],
    )
    run_parser.add_argument("--task-id")
    run_parser.add_argument("--input-file", action="append")
    run_parser.add_argument("--output-dir")
    run_parser.add_argument("--options-json", help="任务选项 JSON")
    run_parser.set_defaults(func=cmd_run)

    fonts_parser = subparsers.add_parser("list-fonts", help="列出可用字体 family")
    fonts_parser.add_argument("input_file")
    fonts_parser.set_defaults(func=cmd_list_fonts)

    fonts_batch_parser = subparsers.add_parser(
        "list-fonts-batch", help="批量列出可用字体 family"
    )
    fonts_batch_parser.add_argument("input_files", nargs="+")
    fonts_batch_parser.set_defaults(func=cmd_list_fonts_batch)

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
