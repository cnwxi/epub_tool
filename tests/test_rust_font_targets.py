from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"


def write_font_target_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            b'<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>',
        )
        archive.writestr("OPS/package.opf", b"<package><metadata/></package>")
        archive.writestr(
            "OPS/Styles/fonts.css",
            b"""/* packaged families should be selectable */
/* Python's existing scanner intentionally ignores nested @font-face rules. */
@media screen {
  @font-face {
    font-family: "Body Font";
    src: local('Body Font'), url("../Fonts/body.ttf?#reader");
  }
}
@font-face { font-family: Display; src: url(../Fonts/display.woff2); }
@font-face { font-family: RemoteOnly; src: url(https://example.invalid/remote.ttf); }
""",
        )
        archive.writestr("OPS/Fonts/body.ttf", b"not-a-real-font")
        archive.writestr("OPS/Fonts/display.woff2", b"not-a-real-font")


def run_python(input_file: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "python_backend.cli", "list-fonts", str(input_file)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def run_rust(input_file: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--bin",
            "rust-task-runner",
            "--",
            "--list-font-targets",
            str(input_file),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_rust_font_target_scan_matches_python_for_packaged_css_fonts(tmp_path: Path) -> None:
    input_file = tmp_path / "book.epub"
    write_font_target_epub(input_file)

    python_result = run_python(input_file)
    rust_result = run_rust(input_file)

    assert python_result == rust_result == {
        "ok": True,
        "input_file": str(input_file),
        "font_families": ["Display"],
    }
