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
    "paddle",
    "paddleocr",
    "paddlex",
    "chardet",
    "bidi",
    "cv2",
    "pypdfium2",
    "pyclipper",
    "shapely",
    "imagesize",
    "utils.reformat_epub",
    "utils.decrypt_epub",
    "utils.encrypt_epub",
    "utils.encrypt_font",
    "utils.decrypt_font",
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


def sidecar_output_path() -> Path:
    return DIST_DIR / SIDE_CAR_NAME


def sidecar_exists() -> bool:
    return sidecar_output_path().is_file()


def iter_sidecar_inputs():
    for package_dir in (REPO_ROOT / "python_backend", REPO_ROOT / "utils"):
        yield from package_dir.rglob("*.py")
    yield REPO_ROOT / "requirements.txt"
    yield Path(__file__).resolve()


def sidecar_is_current() -> bool:
    target_path = sidecar_output_path()
    if not target_path.is_file():
        return False

    target_mtime = target_path.stat().st_mtime
    for source_path in iter_sidecar_inputs():
        if source_path.is_file() and source_path.stat().st_mtime > target_mtime:
            return False
    return True


def build_sidecar() -> Path:
    ensure_pyinstaller()
    ensure_runtime_dependencies()

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = WORK_ROOT / "work"
    spec_dir = WORK_ROOT / "spec"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    target_path = sidecar_output_path()
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
        "--collect-all",
        "paddle",
        "--collect-all",
        "paddleocr",
        "--collect-all",
        "paddlex",
        "--collect-submodules",
        "bidi",
        "--copy-metadata",
        "python-bidi",
        "--copy-metadata",
        "pypdfium2",
        "--copy-metadata",
        "opencv-contrib-python",
        "--copy-metadata",
        "pyclipper",
        "--copy-metadata",
        "shapely",
        "--copy-metadata",
        "imagesize",
        str(ENTRYPOINT),
    ]

    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(CONFIG_DIR)
    env["PADDLE_PDX_CACHE_HOME"] = str(CONFIG_DIR / "paddlex")
    env["MPLCONFIGDIR"] = str(CONFIG_DIR / "matplotlib")
    Path(env["PADDLE_PDX_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(env["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

    subprocess.run(command, cwd=REPO_ROOT, check=True, env=env)

    if not target_path.exists():
        raise SystemExit(f"Sidecar build finished but the output file was not found: {target_path}")

    if sys.platform != "win32":
        target_path.chmod(target_path.stat().st_mode | 0o755)

    return target_path


def main() -> int:
    ensure_only = "--ensure" in sys.argv[1:]
    if ensure_only and sidecar_is_current():
        print(f"Python sidecar reused: {sidecar_output_path()}")
        return 0

    target_path = build_sidecar()
    print(f"Python sidecar built: {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
