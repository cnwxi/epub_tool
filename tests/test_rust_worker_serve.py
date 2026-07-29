from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_RUNNER = REPO_ROOT / "src-tauri" / "target" / "debug" / "rust-task-runner"
REAL_INPUT = REPO_ROOT / "fixtures" / "解密后.epub"


def read_response(worker: subprocess.Popen[str], request_id: str) -> tuple[list[dict], dict]:
    events: list[dict] = []
    assert worker.stdout is not None
    while True:
        line = worker.stdout.readline()
        assert line, worker.stderr.read() if worker.stderr is not None else "Worker 已退出"
        response = json.loads(line)
        assert response["requestId"] == request_id
        if response["kind"] == "event":
            events.append(response["event"])
            continue
        return events, response


@pytest.mark.skipif(not REAL_INPUT.is_file(), reason="本地 fixtures 中没有真实 EPUB 样本")
def test_persistent_rust_worker_runs_task_and_survives_request_error(tmp_path: Path) -> None:
    assert RUST_RUNNER.is_file(), "请先构建 rust-task-runner"
    worker = subprocess.Popen(
        [str(RUST_RUNNER), "serve"],
        cwd=REPO_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdin is not None
        request_id = "worker-success"
        worker.stdin.write(
            json.dumps(
                {
                    "requestId": request_id,
                    "request": {
                        "taskId": request_id,
                        "taskType": "reformat_epub",
                        "inputFiles": [str(REAL_INPUT)],
                        "outputDir": str(tmp_path / "output"),
                        "options": {},
                    },
                    "logPath": str(tmp_path / "worker.log"),
                }
            )
            + "\n"
        )
        worker.stdin.flush()
        events, response = read_response(worker, request_id)
        assert response["kind"] == "result"
        assert response["result"]["ok"] is True
        assert any(event["event"] == "task.started" for event in events)
        assert worker.poll() is None

        error_request_id = "worker-error"
        worker.stdin.write(
            json.dumps(
                {
                    "requestId": error_request_id,
                    "request": {
                        "taskId": error_request_id,
                        "taskType": "unsupported_task",
                        "inputFiles": [],
                        "outputDir": None,
                        "options": {},
                    },
                    "logPath": str(tmp_path / "worker.log"),
                }
            )
            + "\n"
        )
        worker.stdin.flush()
        _, response = read_response(worker, error_request_id)
        assert response["kind"] == "error"
        assert "暂不支持" in response["error"]
        assert worker.poll() is None
    finally:
        worker.terminate()
        worker.wait(timeout=10)
