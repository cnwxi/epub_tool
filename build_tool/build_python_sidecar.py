from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "python_backend" / "cli.py"
DIST_DIR = REPO_ROOT / "src-tauri" / "binaries"
WORK_ROOT = REPO_ROOT / "build" / "python-sidecar"
CONFIG_DIR = WORK_ROOT / "cache"
SIDECAR_STEM = "epub-tool-python"
SIDE_CAR_NAME = f"{SIDECAR_STEM}.exe" if sys.platform == "win32" else SIDECAR_STEM
REQUIRED_MODULES = [
    "bs4",
    "emoji",
    "fontTools",
    "tinycss2",
    "tqdm",
    "PIL",
    "utils.reformat_epub",
    "utils.decrypt_epub",
    "utils.encrypt_epub",
    "utils.encrypt_font",
    "utils.transfer_img",
]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def ensure_pyinstaller() -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "PyInstaller is required. Run `python -m pip install pyinstaller` first."
        ) from exc


def ensure_runtime_dependencies() -> None:
    missing = [
        module_name
        for module_name in REQUIRED_MODULES
        if importlib.util.find_spec(module_name) is None
    ]
    if missing:
        raise SystemExit(
            "Missing Python dependencies required for the sidecar build: "
            + ", ".join(missing)
            + ". Run `python -m pip install -r requirements.txt pyinstaller` with the same interpreter."
        )


def build_sidecar() -> Path:
    ensure_pyinstaller()
    ensure_runtime_dependencies()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = WORK_ROOT / "work"
    spec_dir = WORK_ROOT / "spec"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    target_path = DIST_DIR / SIDE_CAR_NAME
    if target_path.exists():
        target_path.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        SIDECAR_STEM,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(REPO_ROOT),
        "--collect-submodules",
        "python_backend",
        "--collect-submodules",
        "utils",
        str(ENTRYPOINT),
    ]

    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(CONFIG_DIR)

    subprocess.run(command, cwd=REPO_ROOT, check=True, env=env)

    if not target_path.exists():
        raise SystemExit(f"Sidecar build finished but the output file was not found: {target_path}")

    if sys.platform != "win32":
        target_path.chmod(target_path.stat().st_mode | 0o755)

    return target_path


def main() -> int:
    target_path = build_sidecar()
    print(f"Python sidecar built: {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
