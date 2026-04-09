from __future__ import annotations

import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "src-tauri" / "binaries"
STAGE_DIR = REPO_ROOT / "src-tauri" / "bundle-resources" / "binaries"
SIDECAR_NAME = "epub-tool-python.exe" if sys.platform == "win32" else "epub-tool-python"


def prepare_bundle_resources() -> Path:
    source_path = SOURCE_DIR / SIDECAR_NAME
    if not source_path.is_file():
        raise SystemExit(f"Sidecar file not found: {source_path}")

    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    target_path = STAGE_DIR / SIDECAR_NAME
    shutil.copy2(source_path, target_path)

    if sys.platform != "win32":
        target_path.chmod(target_path.stat().st_mode | 0o755)

    print(f"Bundle resources prepared: {target_path}")
    return target_path


def main() -> int:
    prepare_bundle_resources()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
