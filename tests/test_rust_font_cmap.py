from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from fontTools.ttLib import TTFont

from test_font_encrypt import build_test_font_bytes


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"


def python_cmap(font_bytes: bytes) -> list[dict[str, int]]:
    font = TTFont(io.BytesIO(font_bytes))
    cmap = font.getBestCmap() or {}
    return [
        {"codepoint": codepoint, "glyph_id": font.getGlyphID(glyph_name)}
        for codepoint, glyph_name in sorted(cmap.items())
    ]


def rust_cmap(font_path: Path) -> list[dict[str, int]]:
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
            "--read-font-cmap",
            str(font_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)["cmap"]


def rewrite_rust_cmap(
    font_path: Path,
    output_path: Path,
    replacements: dict[int, int],
    removed_codepoints: list[int],
) -> None:
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
            "--rewrite-font-cmap",
            str(font_path),
            "--font-output",
            str(output_path),
            "--cmap-replacements",
            json.dumps(replacements),
            "--remove-cmap-codepoints",
            json.dumps(removed_codepoints),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def obfuscate_rust_font(font_path: Path, output_path: Path, text: str) -> dict[str, object]:
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
            "--obfuscate-font",
            str(font_path),
            "--font-output",
            str(output_path),
            "--font-text",
            text,
            "--rng-seed",
            "42",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_rust_cmap_read_matches_fonttools_for_font_encrypt_fixture(tmp_path: Path) -> None:
    font_bytes = build_test_font_bytes()
    font_path = tmp_path / "test.ttf"
    font_path.write_bytes(font_bytes)

    assert rust_cmap(font_path) == python_cmap(font_bytes)


def test_rust_cmap_rewrite_preserves_font_metrics_and_rebinds_glyphs(
    tmp_path: Path,
) -> None:
    font_path = tmp_path / "input.ttf"
    output_path = tmp_path / "rewritten.ttf"
    font_path.write_bytes(build_test_font_bytes())

    original = TTFont(font_path)
    original_cmap = original.getBestCmap() or {}
    replacements = {
        0xAC00: original.getGlyphID(original_cmap[ord("你")]),
        0xAC01: original.getGlyphID(original_cmap[ord("好")]),
        ord("B"): original.getGlyphID(original_cmap[ord("A")]),
    }
    rewrite_rust_cmap(
        font_path,
        output_path,
        replacements,
        [ord("你"), ord("好"), ord("A")],
    )

    rewritten = TTFont(output_path)
    cmap = rewritten.getBestCmap() or {}
    assert ord("你") not in cmap
    assert ord("好") not in cmap
    assert ord("A") not in cmap
    assert cmap[0xAC00] == "uni4F60"
    assert cmap[0xAC01] == "uni597D"
    assert cmap[ord("B")] == "A"
    assert rewritten["hmtx"]["uni4F60"] == (1000, 0)
    assert rewritten["hmtx"]["uni597D"] == (1000, 0)
    assert rewritten["OS/2"].usWinAscent == 950


def test_rust_font_obfuscation_rebinds_mapped_glyphs_and_preserves_punctuation(
    tmp_path: Path,
) -> None:
    font_path = tmp_path / "input.ttf"
    output_path = tmp_path / "obfuscated.ttf"
    font_path.write_bytes(build_test_font_bytes())

    original = TTFont(font_path)
    original_cmap = original.getBestCmap() or {}
    result = obfuscate_rust_font(font_path, output_path, "你好吗。A0Ａ０")

    assert result["obfuscated_text"] == "你好A0Ａ０"
    assert result["passthrough_text"] == "。"
    replacements = {
        item["source"]: item["entity"] for item in result["replacements"]
    }
    assert set(replacements) == set(result["obfuscated_text"])

    rewritten = TTFont(output_path)
    rewritten_cmap = rewritten.getBestCmap() or {}
    for source, entity in replacements.items():
        target = int(entity[3:], 16)
        assert ord(source) not in rewritten_cmap
        assert rewritten_cmap[target] == original_cmap[ord(source)]
    assert rewritten_cmap[ord("。")] == original_cmap[ord("。")]
    assert rewritten["hmtx"]["uni4F60"] == (1000, 0)
