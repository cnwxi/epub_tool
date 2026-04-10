from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from python_backend.protocol import TaskRequest
from python_backend.task_runner import list_font_targets, run_task


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        stream.reconfigure(encoding="utf-8", errors="replace")


def pick_payload_value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


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


def cmd_run(args: argparse.Namespace) -> int:
    request = load_request_from_args(args)
    result = run_task(request)
    return 0 if result.ok else 1


def cmd_list_fonts(args: argparse.Namespace) -> int:
    payload = list_font_targets(args.input_file)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="epub_tool Tauri bridge CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="执行 EPUB 处理任务")
    run_parser.add_argument("--request-json", help="完整 TaskRequest JSON")
    run_parser.add_argument("--request-file", help="包含 TaskRequest JSON 的文件")
    run_parser.add_argument(
        "--task-type",
        choices=["reformat", "decrypt", "encrypt", "font_encrypt", "transfer_img"],
    )
    run_parser.add_argument("--task-id")
    run_parser.add_argument("--input-file", action="append")
    run_parser.add_argument("--output-dir")
    run_parser.add_argument("--options-json", help="任务选项 JSON")
    run_parser.set_defaults(func=cmd_run)

    fonts_parser = subparsers.add_parser("list-fonts", help="列出可用字体 family")
    fonts_parser.add_argument("input_file")
    fonts_parser.set_defaults(func=cmd_list_fonts)
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
