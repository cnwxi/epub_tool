from __future__ import annotations

import json
import re
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

from fontTools.ttLib import TTFont

from python_backend.services.font.encrypt_font import FontEncrypt
from test_font_encrypt import build_test_font_bytes


REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_MANIFEST = REPO_ROOT / "src-tauri" / "Cargo.toml"
FONT_MEMBER = "OEBPS/Fonts/TestFont.ttf"
XHTML_MEMBER = "OEBPS/chapter.xhtml"


def build_font_encrypt_epub(
    epub_path: Path, css: str | None = None, xhtml: str | None = None
) -> None:
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        epub.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        epub.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Test</dc:title></metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="font" href="Fonts/TestFont.ttf" media-type="font/ttf"/>
  </manifest>
</package>""",
        )
        epub.writestr(
            "OEBPS/style.css",
            css
            or """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { font-family: "TestFont"; }""",
        )
        epub.writestr(
            XHTML_MEMBER,
            xhtml
            or """<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p class="body">你好Ａ０。A0</p></body></html>""",
        )
        epub.writestr(FONT_MEMBER, build_test_font_bytes())


def run_rust_font_encrypt(input_path: Path, output_dir: Path) -> Path:
    request = {
        "taskId": "rust-font-encrypt-golden",
        "taskType": "encrypt_font",
        "inputFiles": [str(input_path)],
        "outputDir": str(output_dir),
        "options": {
            "target_font_families_by_file": {str(input_path): ["TestFont"]},
        },
    }
    completed = subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "--offline",
            "--manifest-path",
            str(RUST_MANIFEST),
            "--bin",
            "rust-task-runner",
            "--",
            "--request-json",
            json.dumps(request),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    result = events[-1]["result"]
    assert result["status"] == "success"
    return Path(result["outputs"][0])


def run_python_font_encrypt(input_path: Path, output_dir: Path) -> Path:
    encryptor = FontEncrypt(
        str(input_path),
        str(output_dir),
        target_font_families=["TestFont"],
    )
    encryptor.get_mapping()
    encryptor.clean_text()
    encryptor.encrypt_font()
    encryptor.read_html()
    return output_dir / f"{input_path.stem}_encrypt_font.epub"


def read_epub_member_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as epub:
        return epub.namelist()


def assert_readable_font_mapping(output_path: Path) -> None:
    with zipfile.ZipFile(output_path) as epub:
        html = epub.read(XHTML_MEMBER).decode("utf-8")
        font = TTFont(BytesIO(epub.read(FONT_MEMBER)))
    texts = re.findall(r"<p\b[^>]*>(.*?)</p>", html)
    assert texts
    cmap = font.getBestCmap() or {}

    assert cmap[ord("。")] == "uni3002"
    for text in texts:
        assert len(text) == len("你好Ａ０。A0")
        assert text[4] == "。"
        for source, target, glyph in zip("你好Ａ０A0", text[:4] + text[5:], ["uni4F60", "uni597D", "uniFF21", "uniFF10", "A", "zero"], strict=True):
            assert target != source
            assert cmap[ord(target)] == glyph
    assert font["hmtx"]["uni3002"] == (500, 0)
    assert font["OS/2"].usWinAscent == 950


def test_rust_font_encrypt_matches_python_output_invariants_for_strict_epub_subset(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "book.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(input_path)

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert sorted(read_epub_member_names(rust_output)) == sorted(read_epub_member_names(python_output))
    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_tag_selector_inheritance(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tag-selector.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
body { font-family: "TestFont"; }""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert sorted(read_epub_member_names(rust_output)) == sorted(read_epub_member_names(python_output))
    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_tag_class_selector(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "tag-class-selector.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
p.body { font-family: "TestFont"; }""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert sorted(read_epub_member_names(rust_output)) == sorted(read_epub_member_names(python_output))
    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_screen_media_rules(tmp_path: Path) -> None:
    input_path = tmp_path / "complex.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
@media screen { .body { font-family: "TestFont"; } }""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_overlapping_selectors_preserve_readable_mapping(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "overlapping-selectors.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { font-family: "TestFont"; }
p.body { font-family: "TestFont"; }""",
    )
    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert sorted(read_epub_member_names(rust_output)) == sorted(read_epub_member_names(python_output))
    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_duplicate_and_important_rules(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "cascade.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { font-family: serif; }
.body { font-family: serif; }
p#chapter.body { font-family: "TestFont" !important; }""",
        """<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p id="chapter" class="body">你好Ａ０。A0</p></body></html>""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_inline_font_family(tmp_path: Path) -> None:
    input_path = tmp_path / "inline.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { font-family: serif; }""",
        """<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p class="body" style="font-family: 'TestFont'">你好Ａ０。A0</p></body></html>""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_descendant_and_child_selectors(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "combinators.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
body .wrapper > p.body { font-family: "TestFont"; }""",
        """<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><div class="wrapper"><p class="body">你好Ａ０。A0</p></div></body></html>""",
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_inherited_custom_property(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "inherited-custom-property.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        '''@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.parent { --book-font: "TestFont"; }
.body { font-family: var(--book-font), serif; }''',
        '''<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><div class="parent"><p class="body">你好Ａ０。A0</p></div></body></html>''',
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_inline_custom_property_override(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "inline-custom-property.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        '''@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { --book-font: serif; font-family: var(--book-font); }''',
        '''<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p class="body" style="--book-font: 'TestFont'">你好Ａ０。A0</p></body></html>''',
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_custom_property_fallback_and_important(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "custom-property-priority.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        '''@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.important { --book-font: "TestFont" !important; font-family: var(--book-font); }
.fallback { font-family: var(--missing-font, "TestFont"), serif; }''',
        '''<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p class="important" style="--book-font: serif">你好Ａ０。A0</p>
<p class="fallback">你好Ａ０。A0</p></body></html>''',
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_when_missing_custom_property_inherits(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "missing-custom-property.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        '''@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.parent { font-family: "TestFont"; }
.child { font-family: var(--missing-font); }''',
        '''<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><div class="parent"><p class="child">你好Ａ０。A0</p></div></body></html>''',
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)


def test_rust_font_encrypt_matches_python_for_all_unset_inheritance(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "all-unset.epub"
    rust_output_dir = tmp_path / "rust"
    python_output_dir = tmp_path / "python"
    rust_output_dir.mkdir()
    python_output_dir.mkdir()
    build_font_encrypt_epub(
        input_path,
        '''@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.parent { font-family: "TestFont"; }
.child { all: unset; }''',
        '''<?xml version="1.0" encoding="UTF-8"?>
<html><head><link rel="stylesheet" href="style.css"/></head>
<body><div class="parent"><p class="child">你好Ａ０。A0</p></div></body></html>''',
    )

    rust_output = run_rust_font_encrypt(input_path, rust_output_dir)
    python_output = run_python_font_encrypt(input_path, python_output_dir)

    assert_readable_font_mapping(rust_output)
    assert_readable_font_mapping(python_output)
