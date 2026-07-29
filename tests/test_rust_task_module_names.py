from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_TASK_ROOT = REPO_ROOT / "python_backend" / "services"
RUST_TASK_ROOT = REPO_ROOT / "src-tauri" / "src" / "rust_backend"


MIGRATED_TASK_MODULES = (
    "epub/reformat_epub",
    "epub/encrypt_epub",
    "epub/decrypt_epub",
    "image/image_compress",
    "image/image_to_webp",
    "image/webp_to_img",
    "image/replace_cover",
    "text/chinese_convert",
    "font/encrypt_font",
    "font/decrypt_font",
)


def test_rust_task_modules_follow_python_service_categories() -> None:
    for python_module in MIGRATED_TASK_MODULES:
        python_path = PYTHON_TASK_ROOT / f"{python_module}.py"
        rust_path = RUST_TASK_ROOT / f"{python_module}.rs"

        assert python_path.is_file(), f"缺少 Python 任务实现: {python_path}"
        assert rust_path.is_file(), f"Rust 迁移文件必须位于对应服务分类: {rust_path}"
