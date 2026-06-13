from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_TAURI_DIR = REPO_ROOT / "src-tauri"
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
TAURI_CONF = SRC_TAURI_DIR / "tauri.conf.json"
BUNDLE_DIR = SRC_TAURI_DIR / "target" / "release" / "bundle"


def run_command(command: list[str]) -> None:
    print(f"Running: {' '.join(command)}")
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def app_version() -> str:
    with CARGO_TOML.open("rb") as f:
        cargo = tomllib.load(f)
    return cargo["package"]["version"]


def product_name() -> str:
    with TAURI_CONF.open("r", encoding="utf-8") as f:
        config = json.load(f)
    return config["productName"]


def macos_arch_label() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "aarch64"
    if machine in {"x86_64", "amd64"}:
        return "x64"
    return machine


def create_macos_dmg() -> Path:
    hdiutil = shutil.which("hdiutil")
    ditto = shutil.which("ditto")
    if not hdiutil or not ditto:
        raise SystemExit("macOS DMG 构建需要 hdiutil 和 ditto。")

    name = product_name()
    version = app_version()
    app_path = BUNDLE_DIR / "macos" / f"{name}.app"
    if not app_path.is_dir():
        raise SystemExit(f"Tauri 未生成 .app，无法创建 DMG: {app_path}")

    dmg_dir = BUNDLE_DIR / "dmg"
    dmg_dir.mkdir(parents=True, exist_ok=True)
    dmg_path = dmg_dir / f"{name}_{version}_{macos_arch_label()}.dmg"
    if dmg_path.exists():
        dmg_path.unlink()

    # 使用系统 hdiutil 直接从 .app 生成压缩 DMG，避免 Tauri 内置 create-dmg
    # 脚本在部分 macOS runner 上残留临时读写镜像后导致整次构建失败。
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        staged_app = tmp_path / f"{name}.app"
        run_command([ditto, str(app_path), str(staged_app)])
        run_command(
            [
                hdiutil,
                "create",
                "-volname",
                name,
                "-srcfolder",
                str(tmp_path),
                "-ov",
                "-format",
                "UDZO",
                str(dmg_path),
            ]
        )
        run_command([hdiutil, "verify", str(dmg_path)])

    print(f"macOS DMG created: {dmg_path}")
    return dmg_path


def main() -> int:
    if sys.platform == "darwin":
        run_command(["npm", "run", "tauri", "--", "build", "--bundles", "app"])
        create_macos_dmg()
    else:
        run_command(["npm", "run", "tauri", "--", "build"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
