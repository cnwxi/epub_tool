from __future__ import annotations

import json
import os
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
DMG_VOLUME_ICON = REPO_ROOT / "assets" / "img" / "icon.icns"


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


def resolve_setfile() -> str:
    setfile = shutil.which("SetFile")
    if setfile:
        return setfile

    xcrun = shutil.which("xcrun")
    if xcrun:
        result = subprocess.run(
            [xcrun, "--find", "SetFile"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

    raise SystemExit("macOS DMG 自定义卷图标需要 Xcode Command Line Tools 中的 SetFile。")


def create_macos_dmg() -> Path:
    hdiutil = shutil.which("hdiutil")
    ditto = shutil.which("ditto")
    if not hdiutil or not ditto:
        raise SystemExit("macOS DMG 构建需要 hdiutil 和 ditto。")
    if not DMG_VOLUME_ICON.is_file():
        raise SystemExit(f"DMG 卷图标不存在: {DMG_VOLUME_ICON}")

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

    # 先生成可写镜像，才能为挂载卷写入 Finder 自定义图标标记；随后转换为压缩 DMG。
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        staging_path = tmp_path / "staging"
        staging_path.mkdir()
        staged_app = staging_path / f"{name}.app"
        run_command([ditto, str(app_path), str(staged_app)])
        # 在 DMG 中提供标准的“Applications”快捷方式，供用户将 .app 拖入应用程序目录。
        (staging_path / "Applications").symlink_to("/Applications")
        shutil.copy2(DMG_VOLUME_ICON, staging_path / ".VolumeIcon.icns")

        writable_dmg_path = tmp_path / "writable.dmg"
        mount_path = tmp_path / "mounted"
        mount_path.mkdir()
        run_command(
            [
                hdiutil,
                "create",
                "-volname",
                name,
                "-srcfolder",
                str(staging_path),
                "-ov",
                "-format",
                "UDRW",
                str(writable_dmg_path),
            ]
        )

        attached = False
        try:
            run_command(
                [
                    hdiutil,
                    "attach",
                    "-readwrite",
                    "-noverify",
                    "-noautoopen",
                    "-mountpoint",
                    str(mount_path),
                    str(writable_dmg_path),
                ]
            )
            attached = True
            run_command([resolve_setfile(), "-a", "C", str(mount_path)])
        finally:
            if attached:
                run_command([hdiutil, "detach", str(mount_path)])

        run_command(
            [
                hdiutil,
                "convert",
                str(writable_dmg_path),
                "-format",
                "UDZO",
                "-o",
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
        npm_command = "npm.cmd" if os.name == "nt" else "npm"
        run_command([npm_command, "run", "tauri", "--", "build"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
