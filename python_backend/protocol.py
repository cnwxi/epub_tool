from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


TaskType = str


@dataclass(slots=True)
class TaskRequest:
    task_id: str
    task_type: TaskType
    input_files: list[str]
    output_dir: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskEvent:
    event: str
    task_id: str
    status: str
    progress: float
    message: str
    current_file: str | None = None
    current_index: int | None = None
    total_files: int | None = None
    output_path: str | None = None
    level: str = "info"
    result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskResult:
    ok: bool
    status: str
    outputs: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    log_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

