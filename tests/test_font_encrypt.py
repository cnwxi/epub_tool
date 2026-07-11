import unittest
import unicodedata
import re
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from python_backend.services.encrypt_font import (
    FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS,
    FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS,
    FONT_OBFUSCATION_LAYOUT_CODEPOINTS,
    FontEncrypt,
    list_epub_font_encrypt_targets,
)


def build_test_glyph(width=500, height=500):
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((width, 0))
    pen.lineTo((width, height))
    pen.lineTo((0, height))
    pen.closePath()
    return pen.glyph()


def build_test_font_bytes():
    glyph_order = [
        ".notdef",
        "uni4F60",
        "uni597D",
        "uni3002",
        "zero",
        "A",
        "uniFF10",
        "uniFF21",
    ]
    glyphs = {
        ".notdef": build_test_glyph(0, 0),
        "uni4F60": build_test_glyph(1000, 800),
        "uni597D": build_test_glyph(1000, 700),
        "uni3002": build_test_glyph(500, 300),
        "zero": build_test_glyph(500, 700),
        "A": build_test_glyph(500, 700),
        "uniFF10": build_test_glyph(1000, 700),
        "uniFF21": build_test_glyph(1000, 700),
    }
    metrics = {
        ".notdef": (500, 0),
        "uni4F60": (1000, 0),
        "uni597D": (1000, 0),
        "uni3002": (500, 0),
        "zero": (500, 0),
        "A": (500, 0),
        "uniFF10": (1000, 0),
        "uniFF21": (1000, 0),
    }
    cmap = {
        ord("你"): "uni4F60",
        ord("好"): "uni597D",
        ord("。"): "uni3002",
        ord("0"): "zero",
        ord("A"): "A",
        ord("０"): "uniFF10",
        ord("Ａ"): "uniFF21",
    }
    font_builder = FontBuilder(1000, isTTF=True)
    font_builder.setupGlyphOrder(glyph_order)
    font_builder.setupCharacterMap(cmap)
    font_builder.setupGlyf(glyphs)
    font_builder.setupHorizontalMetrics(metrics)
    font_builder.setupHorizontalHeader(ascent=900, descent=-200)
    font_builder.setupNameTable(
        {
            "familyName": "TestFont",
            "styleName": "Regular",
            "psName": "TestFont-Regular",
        }
    )
    font_builder.setupOS2(
        sTypoAscender=900,
        sTypoDescender=-200,
        usWinAscent=950,
        usWinDescent=250,
    )
    font_builder.setupPost()
    font_stream = BytesIO()
    font_builder.save(font_stream)
    return font_stream.getvalue()


def build_cascade_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head></head><body>
<p class="fs2">甲乙</p>
<p>丙丁</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/base.ttf", b"base-font")
        epub.writestr("OEBPS/Fonts/fs2.ttf", b"fs2-font")


def build_nested_font_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head></head><body>
<p class="base">甲<span class="fs2">乙</span>丙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/base.ttf", b"base-font")
        epub.writestr("OEBPS/Fonts/fs2.ttf", b"fs2-font")


def build_unresolved_override_test_epub(epub_path, override_family):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            f"""@font-face {{ font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }}
.target {{ font-family: "TargetFont"; }}
.sys {{ font-family: {override_family}; }}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">外<span class="sys">内</span></p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_important_target_font_test_epub(epub_path, override_family):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            f"""@font-face {{ font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }}
.target {{ font-family: "TargetFont" !important; }}
.sys {{ font-family: {override_family}; }}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target sys">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_inline_cascade_test_epub(epub_path, css_declaration, inline_style):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            f"""@font-face {{ font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }}
.target {{ {css_declaration} }}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target" style='{inline_style}'>甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_document_stylesheet_order_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/target.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.target { font-family: "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Styles/block.css",
            """.target { font-family: serif; }
""",
        )
        epub.writestr(
            "OEBPS/Styles/unused.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head>
<link rel="stylesheet" href="../Styles/block.css"/>
<link rel="stylesheet" href="../Styles/target.css"/>
</head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_imported_media_css_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@import url("imported.css");
""",
        )
        epub.writestr(
            "OEBPS/Styles/imported.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@media all {
  .target { font-family: "TargetFont"; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_function_selector_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.wrapper :is(.target, .other) { font-family: "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<div class="wrapper"><p class="target">甲乙</p></div>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_specificity_function_test_epub(epub_path, css_text, body_html):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            f"""@font-face {{ font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }}
{css_text}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
{body_html}
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_complex_selector_match_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
[lang|="zh"] .target:nth-child(2n+1) { font-family: "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html lang="zh-Hans"><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲</p>
<p class="target">乙</p>
<p class="target">丙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_xhtml_namespace_selector_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
body > p.target:nth-of-type(2) { font-family: "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><link rel="stylesheet" href="../Styles/style.css"/></head>
<body>
<br />
<p class="target">甲</p>
<p class="target">乙</p>
</body>
</html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_media_filtered_blocker_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@import url("print-import.css") print;
.target { font-family: "TargetFont"; }
@media print {
  .target { font-family: serif !important; }
}
@media not screen {
  .target { font-family: serif !important; }
}
""",
        )
        epub.writestr(
            "OEBPS/Styles/print-import.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Styles/print-link.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Styles/alternate.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head>
<link rel="stylesheet" href="../Styles/style.css"/>
<link rel="stylesheet" href="../Styles/print-link.css" media="print"/>
<link rel="alternate stylesheet" href="../Styles/alternate.css"/>
<style media="print">.target { font-family: serif !important; }</style>
</head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_print_only_target_font_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@media print {
  .target { font-family: "TargetFont"; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head>
<link rel="stylesheet" href="../Styles/style.css"/>
<style media="print">.target { font-family: "TargetFont"; }</style>
</head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_scope_selector_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@scope (.chapter) {
  .target { font-family: "TargetFont"; }
}
@scope (.direct) {
  :scope > .target { font-family: "TargetFont"; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">外</p>
<section class="chapter"><p class="target">内</p></section>
<section class="direct">
  <div><p class="target">深</p></div>
  <p class="target">直</p>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_scope_limit_selector_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@scope (.chapter) to (.stop) {
  .target { font-family: "TargetFont"; }
}
@scope (.direct) to (:scope > .stop) {
  .target { font-family: "TargetFont"; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<section class="chapter">
  <p class="target">内</p>
  <section class="stop"><p class="target">停</p></section>
</section>
<section class="direct">
  <p class="target">直</p>
  <section><p class="target">深</p></section>
  <section class="stop"><p class="target">除</p></section>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_scope_proximity_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@scope (.inner) {
  .target { font-family: "TargetFont"; }
}
@scope (.outer) {
  .target { font-family: serif; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<section class="outer">
  <section class="inner">
    <p class="target">近</p>
  </section>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_initial_font_keyword_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.parent { font-family: "TargetFont"; }
.reset-family { font-family: initial; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="parent">外<span class="reset-family">族</span><span style="font: initial;">简</span></p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_inherit_font_keyword_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.outer { font-family: serif; }
.parent-target { font-family: "TargetFont"; }
.target { font-family: "TargetFont"; }
.inherit { font-family: inherit; }
.unset { font-family: unset; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<section class="outer">
  <p class="target inherit">甲</p>
  <p class="target unset">乙</p>
  <p class="target" style="font-family: inherit;">戊</p>
</section>
<section class="parent-target">
  <p class="target inherit">丙</p>
  <p class="target" style="font: inherit;">丁</p>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_all_font_keyword_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.outer { font-family: serif; }
.parent-target { font-family: "TargetFont"; }
.target { font-family: "TargetFont"; }
.reset-all { all: initial; }
.unset-all { all: unset; }
.inherit-all { all: inherit; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<section class="outer">
  <p class="target reset-all">甲</p>
  <p class="target unset-all">乙</p>
  <p class="target inherit-all">丙</p>
</section>
<section class="parent-target">
  <p class="target reset-all">丁</p>
  <p class="target unset-all">戊</p>
  <p class="target inherit-all">己</p>
  <p class="target" style="all: initial;">庚</p>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_revert_layer_font_keyword_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
@layer base {
  .layer-reveal { font-family: "TargetFont"; }
  .layer-block { font-family: serif; }
  .inline-reveal { font-family: "TargetFont"; }
  .var-reveal { font-family: "TargetFont"; }
  .all-reveal { font-family: "TargetFont"; }
  .unlayered-reveal { font-family: "TargetFont"; }
}
@layer override {
  .layer-reveal { font-family: serif; }
  .layer-reveal { font-family: revert-layer; }
  .layer-block { font-family: "TargetFont"; }
  .layer-block { font-family: revert-layer; }
  .var-reveal { --rollback-font: revert-layer; font-family: var(--rollback-font); }
  .all-reveal { all: revert-layer; }
}
@layer important-base, important-override;
@layer important-base {
  .important-reveal { font-family: revert-layer !important; }
}
@layer important-override {
  .important-reveal { font-family: "TargetFont" !important; }
}
.unlayered-reveal { font-family: revert-layer; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="layer-reveal">甲</p>
<p class="layer-block">乙</p>
<p class="important-reveal">丙</p>
<p class="var-reveal">丁</p>
<p class="all-reveal">戊</p>
<p class="inline-reveal" style="font-family: revert-layer;">己</p>
<p class="unlayered-reveal">庚</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_css_custom_property_font_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
:root { --target-font: "TargetFont"; --block-font: serif; }
.parent { --inherited-font: "TargetFont"; font-family: "TargetFont"; }
.parent-custom { --custom-font: "TargetFont"; }
.var-target { font-family: var(--target-font), serif; }
.var-inherited { font-family: var(--inherited-font), serif; }
.var-fallback { font-family: var(--missing-font, "TargetFont", serif); }
.var-block { font-family: var(--block-font); }
.missing-var { font-family: var(--missing-font); }
.custom-inherit { --custom-font: inherit; font-family: var(--custom-font); }
.custom-initial { --custom-font: initial; font-family: var(--custom-font, "TargetFont"); }
.same-element-important {
  --same-font: "TargetFont" !important;
  font-family: var(--same-font);
}
@layer custom-base {
  .custom-revert-layer { --custom-font: "TargetFont"; }
}
@layer custom-override {
  .custom-revert-layer { --custom-font: serif; --custom-font: revert-layer; font-family: var(--custom-font); }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="var-target">甲</p>
<section class="parent"><p class="var-inherited">乙</p><span class="missing-var">丁</span></section>
<p class="var-fallback">丙</p>
<p class="var-block">戊</p>
<p style="--inline-font: TargetFont; font-family: var(--inline-font);">己</p>
<p class="same-element-important" style="--same-font: serif;">庚</p>
<section class="parent-custom"><p class="custom-inherit">辛</p></section>
<p class="custom-initial">壬</p>
<p class="custom-revert-layer">癸</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_invalid_var_font_inheritance_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
.parent-system { font-family: serif; }
.parent-target { font-family: "TargetFont"; }
.var-low-target { font-family: "TargetFont"; }
.var-low-target { font-family: var(--missing-font); }
.inline-low-target { font-family: "TargetFont"; }
.shorthand-low-target { font-family: "TargetFont"; }
.shorthand-low-target { font: var(--missing-font); }
.invalid-token-low-target { --bad-font: 1px; font-family: "TargetFont"; }
.invalid-token-low-target { font-family: var(--bad-font); }
.font-invalid-token-low-target { --bad-font: 1px; font-family: "TargetFont"; }
.font-invalid-token-low-target { font: var(--bad-font); }
.invalid-list-low-target { --bad-list: "TargetFont", 1px; font-family: serif; }
.invalid-list-low-target { font-family: var(--bad-list); }
.nested-size-var-low-target { font-family: "TargetFont"; }
.nested-size-var-low-target { font: calc(var(--missing-size) * 1px) "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<section class="parent-system">
  <p class="var-low-target">甲</p>
  <p class="inline-low-target" style="font-family: var(--missing-font);">乙</p>
  <p class="shorthand-low-target">丙</p>
  <p class="invalid-token-low-target">庚</p>
  <p class="font-invalid-token-low-target">辛</p>
  <p class="invalid-list-low-target">子</p>
  <p class="nested-size-var-low-target">寅</p>
</section>
<section class="parent-target">
  <p class="var-low-target">丁</p>
  <p class="inline-low-target" style="font-family: var(--missing-font);">戊</p>
  <p class="shorthand-low-target">己</p>
  <p class="invalid-token-low-target">壬</p>
  <p class="font-invalid-token-low-target">癸</p>
  <p class="invalid-list-low-target">丑</p>
  <p class="nested-size-var-low-target">卯</p>
</section>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_woff2_font_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: "WoffTwo"; src: url("../Fonts/target.woff2"); }
.target { font-family: "WoffTwo"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.woff2", b"dummy-woff2")


def build_import_supports_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@import url("false-block.css") supports(unknown-epub-tool-property: value);
@import url("true-target.css") supports(font-family: serif);
@font-face { font-family: "TargetFont"; src: url("../Fonts/target.ttf"); }
""",
        )
        epub.writestr(
            "OEBPS/Styles/false-block.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Styles/true-target.css",
            """.target { font-family: "TargetFont"; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_font_shorthand_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: TargetFont; src: url("../Fonts/target.ttf"); }
.target { font: italic 700 1em/1.4 TargetFont, serif; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_late_import_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: TargetFont; src: url("../Fonts/target.ttf"); }
.target { font-family: TargetFont; }
@import url("late-block.css");
""",
        )
        epub.writestr(
            "OEBPS/Styles/late-block.css",
            """.target { font-family: serif !important; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_supports_container_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face { font-family: TargetFont; src: url("../Fonts/target.ttf"); }
.target { font-family: TargetFont; }
@supports not (font-family: serif) {
  .target { font-family: serif !important; }
}
@supports (unknown-epub-tool-property: value) {
  .target { font-family: serif !important; }
}
@supports selector(:unsupported-pseudo-for-epub-tool) {
  .target { font-family: serif !important; }
}
@supports (font-family: serif) and (selector(.target)) {
  .supported { font-family: TargetFont; }
}
@container (min-width: 1px) {
  .target { font-family: serif !important; }
}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
<p class="supported">丙丁</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


def build_layer_cascade_test_epub(epub_path, css_text):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr(
            "OEBPS/Styles/style.css",
            f"""@font-face {{ font-family: TargetFont; src: url("../Fonts/target.ttf"); }}
{css_text}
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><head><link rel="stylesheet" href="../Styles/style.css"/></head><body>
<p class="target">甲乙</p>
</body></html>""",
        )
        epub.writestr("OEBPS/Fonts/target.ttf", b"target-font")


class FontEncryptObfuscationPolicyTest(unittest.TestCase):
    def test_find_char_mapping_uses_effective_font_without_selector_duplicates(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_cascade_test_epub(epub_path)

            font_encrypt = FontEncrypt(str(epub_path), str(temp_path))
            base_font = "OEBPS/Fonts/base.ttf"
            fs2_font = "OEBPS/Fonts/fs2.ttf"
            font_encrypt.css_selector_to_font_mapping = {
                ".fs2": fs2_font,
                "p": base_font,
            }
            font_encrypt.css_selector_font_rules = [
                {
                    "selector": "p",
                    "font_file": base_font,
                    "specificity": font_encrypt.calculate_selector_specificity("p"),
                    "order": 1,
                },
                {
                    "selector": ".fs2",
                    "font_file": fs2_font,
                    "specificity": font_encrypt.calculate_selector_specificity(".fs2"),
                    "order": 2,
                },
            ]

            font_encrypt.find_char_mapping()
            font_encrypt.close_file()

            self.assertEqual(font_encrypt.font_to_char_mapping[fs2_font], "甲乙")
            self.assertEqual(font_encrypt.font_to_char_mapping[base_font], "丙丁")

    def test_find_char_mapping_skips_nested_non_target_font_override(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_nested_font_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["base"],
            )
            base_font = "OEBPS/Fonts/base.ttf"
            fs2_font = "OEBPS/Fonts/fs2.ttf"
            font_encrypt.font_to_font_family_mapping = {
                "base": base_font,
                "fs2": fs2_font,
            }
            font_encrypt.css_selector_to_font_mapping = {
                ".base": base_font,
            }
            font_encrypt.css_selector_font_rules = [
                {
                    "selector": ".base",
                    "font_file": base_font,
                    "specificity": font_encrypt.calculate_selector_specificity(".base"),
                    "order": 1,
                },
                {
                    "selector": ".fs2",
                    "font_file": fs2_font,
                    "specificity": font_encrypt.calculate_selector_specificity(".fs2"),
                    "order": 2,
                },
            ]

            font_encrypt.find_char_mapping()
            font_encrypt.close_file()

            self.assertEqual(font_encrypt.font_to_char_mapping[base_font], "甲丙")
            self.assertNotIn(fs2_font, font_encrypt.font_to_char_mapping)

    def test_find_char_mapping_skips_generic_and_unresolved_font_overrides(self):
        cases = [
            "serif",
            '"SomeSystemFont"',
        ]
        for override_family in cases:
            with self.subTest(override_family=override_family):
                with TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    epub_path = temp_path / "book.epub"
                    build_unresolved_override_test_epub(epub_path, override_family)

                    font_encrypt = FontEncrypt(
                        str(epub_path),
                        str(temp_path),
                        target_font_families=["TargetFont"],
                    )
                    font_encrypt.get_mapping()
                    font_encrypt.close_file()

                    self.assertEqual(
                        font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                        "外",
                    )

    def test_find_char_mapping_respects_important_target_font(self):
        cases = [
            "serif",
            '"SomeSystemFont"',
        ]
        for override_family in cases:
            with self.subTest(override_family=override_family):
                with TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    epub_path = temp_path / "book.epub"
                    build_important_target_font_test_epub(epub_path, override_family)

                    font_encrypt = FontEncrypt(
                        str(epub_path),
                        str(temp_path),
                        target_font_families=["TargetFont"],
                    )
                    font_encrypt.get_mapping()
                    font_encrypt.close_file()

                    self.assertEqual(
                        font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                        "甲乙",
                    )

    def test_find_char_mapping_applies_inline_and_important_cascade(self):
        cases = [
            (
                'font-family: "TargetFont" !important;',
                "font-family: serif;",
                "甲乙",
            ),
            (
                "font-family: serif !important;",
                'font-family: "TargetFont";',
                None,
            ),
            (
                "font-family: serif !important;",
                'font-family: "TargetFont" !important;',
                "甲乙",
            ),
            (
                "font-family: serif;",
                'font-family: "TargetFont";',
                "甲乙",
            ),
        ]
        for css_declaration, inline_style, expected_text in cases:
            with self.subTest(css_declaration=css_declaration, inline_style=inline_style):
                with TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    epub_path = temp_path / "book.epub"
                    build_inline_cascade_test_epub(
                        epub_path,
                        css_declaration,
                        inline_style,
                    )

                    font_encrypt = FontEncrypt(
                        str(epub_path),
                        str(temp_path),
                        target_font_families=["TargetFont"],
                    )
                    font_encrypt.get_mapping()
                    font_encrypt.close_file()

                    target_font = "OEBPS/Fonts/target.ttf"
                    if expected_text is None:
                        self.assertNotIn(target_font, font_encrypt.font_to_char_mapping)
                    else:
                        self.assertEqual(
                            font_encrypt.font_to_char_mapping[target_font],
                            expected_text,
                        )

    def test_find_char_mapping_uses_document_stylesheet_scope_and_order(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_document_stylesheet_order_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_reads_imported_media_css_rules(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_imported_media_css_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_keeps_function_selector_commas(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_function_selector_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_uses_cssselect2_specificity(self):
        cases = [
            (
                """.target:where(#book) { font-family: "TargetFont"; }
.target.override { font-family: serif; }""",
                '<p id="book" class="target override">甲乙</p>',
                None,
            ),
            (
                """.target { font-family: serif; }
.target:is(#book, .fallback) { font-family: "TargetFont"; }""",
                '<p id="book" class="target">甲乙</p>',
                "甲乙",
            ),
            (
                """.target:nth-child(1 of #book) { font-family: "TargetFont"; }
.target.override { font-family: serif; }""",
                '<p id="book" class="target override">甲乙</p>',
                "甲乙",
            ),
        ]
        for css_text, body_html, expected_text in cases:
            with self.subTest(css_text=css_text):
                with TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    epub_path = temp_path / "book.epub"
                    build_specificity_function_test_epub(epub_path, css_text, body_html)

                    font_encrypt = FontEncrypt(
                        str(epub_path),
                        str(temp_path),
                        target_font_families=["TargetFont"],
                    )
                    font_encrypt.get_mapping()
                    font_encrypt.close_file()

                    target_font = "OEBPS/Fonts/target.ttf"
                    if expected_text is None:
                        self.assertNotIn(target_font, font_encrypt.font_to_char_mapping)
                    else:
                        self.assertEqual(
                            font_encrypt.font_to_char_mapping[target_font],
                            expected_text,
                        )

    def test_find_char_mapping_uses_cssselect2_selector_matching(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_complex_selector_match_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲丙",
            )

    def test_find_char_mapping_uses_xhtml_elementtree_context(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_xhtml_namespace_selector_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "乙",
            )

    def test_cssselect2_context_parses_marked_xhtml_source(self):
        html = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body><br /><p class="target">甲</p></body>
</html>"""
        font_encrypt = FontEncrypt.__new__(FontEncrypt)
        marked_html, marker_attr = font_encrypt.inject_cssselect2_markers(html)
        soup = BeautifulSoup(marked_html, "html.parser")

        match_context = font_encrypt.build_cssselect2_match_context(
            soup,
            marked_html,
            marker_attr,
        )
        font_encrypt.remove_cssselect2_markers(soup, marker_attr)

        self.assertIsNotNone(match_context)
        self.assertEqual(len(match_context), len(soup.find_all(True)))
        self.assertNotIn(marker_attr, soup.decode())

    def test_find_char_mapping_filters_non_screen_media_rules(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_media_filtered_blocker_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_ignores_print_only_target_font(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_print_only_target_font_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertNotIn(
                "OEBPS/Fonts/target.ttf",
                font_encrypt.font_to_char_mapping,
            )

    def test_find_char_mapping_limits_scoped_rules_to_scope_root(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_scope_selector_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "内直",
            )

    def test_find_char_mapping_excludes_scope_limit_subtree(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_scope_limit_selector_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "内直深",
            )

    def test_find_char_mapping_prefers_closer_scope_before_source_order(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_scope_proximity_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "近",
            )

    def test_find_char_mapping_blocks_initial_font_keywords(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_initial_font_keyword_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "外",
            )

    def test_find_char_mapping_inherits_font_keywords_from_parent(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_inherit_font_keyword_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "丙丁",
            )

    def test_find_char_mapping_applies_all_font_keywords(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_all_font_keyword_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "戊己",
            )

    def test_find_char_mapping_applies_revert_layer_font_keywords(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_revert_layer_font_keyword_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲丙戊己庚",
            )

    def test_find_char_mapping_resolves_css_custom_property_fonts(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_css_custom_property_font_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙丁丙己庚辛壬癸",
            )

    def test_find_char_mapping_inherits_invalid_var_font_from_parent(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_invalid_var_font_inheritance_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "丁戊己壬癸丑卯",
            )

    def test_find_char_mapping_accepts_woff2_font_face_sources(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_woff2_font_test_epub(epub_path)

            self.assertEqual(
                list_epub_font_encrypt_targets(str(epub_path)),
                {"font_families": ["WoffTwo"]},
            )

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["WoffTwo"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.woff2"],
                "甲乙",
            )

    def test_find_char_mapping_respects_import_supports_conditions(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_import_supports_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_parses_font_shorthand_after_numeric_weight(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_font_shorthand_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_ignores_late_import_rules(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_late_import_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_filters_false_supports_and_container_rules(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            build_supports_container_test_epub(epub_path)

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TargetFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.close_file()

            self.assertEqual(
                font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙丙丁",
            )

    def test_find_char_mapping_applies_css_layer_cascade(self):
        cases = [
            (
                """.target { font-family: TargetFont; }
@layer late {
  .target { font-family: serif; }
}""",
                "甲乙",
            ),
            (
                """@layer base, theme;
@layer theme {
  .target { font-family: TargetFont; }
}
@layer base {
  .target { font-family: serif; }
}""",
                "甲乙",
            ),
            (
                """@layer base, theme;
@layer base {
  .target { font-family: TargetFont !important; }
}
@layer theme {
  .target { font-family: serif !important; }
}""",
                "甲乙",
            ),
        ]
        for css_text, expected_text in cases:
            with self.subTest(css_text=css_text):
                with TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    epub_path = temp_path / "book.epub"
                    build_layer_cascade_test_epub(epub_path, css_text)

                    font_encrypt = FontEncrypt(
                        str(epub_path),
                        str(temp_path),
                        target_font_families=["TargetFont"],
                    )
                    font_encrypt.get_mapping()
                    font_encrypt.close_file()

                    self.assertEqual(
                        font_encrypt.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                        expected_text,
                    )

    def test_should_obfuscate_text_and_alnum_but_skip_symbols(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        self.assertTrue(font_encrypt.should_obfuscate_char("你"))
        self.assertTrue(font_encrypt.should_obfuscate_char("０"))
        self.assertTrue(font_encrypt.should_obfuscate_char("Ａ"))
        self.assertTrue(font_encrypt.should_obfuscate_char("ｚ"))
        self.assertTrue(font_encrypt.should_obfuscate_char("0"))
        self.assertTrue(font_encrypt.should_obfuscate_char("A"))
        self.assertTrue(font_encrypt.should_obfuscate_char("z"))
        self.assertFalse(font_encrypt.should_obfuscate_char("❶"))
        self.assertFalse(font_encrypt.should_obfuscate_char("。"))

    def test_obfuscation_codepoints_use_layout_safe_hangul_syllables(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        codepoints = font_encrypt.sample_obfuscation_codepoints(64)

        self.assertEqual(len(codepoints), 64)
        self.assertTrue(
            all(codepoint in FONT_OBFUSCATION_LAYOUT_CODEPOINTS for codepoint in codepoints)
        )
        self.assertTrue(
            all(
                unicodedata.category(chr(codepoint)).startswith("L")
                for codepoint in codepoints
            )
        )
        self.assertTrue(
            all(
                unicodedata.east_asian_width(chr(codepoint)) in {"W", "F"}
                for codepoint in codepoints
            )
        )
        self.assertFalse(any(0xE000 <= codepoint <= 0xF8FF for codepoint in codepoints))
        self.assertTrue(all(0xAC00 <= codepoint <= 0xD7AF for codepoint in codepoints))

    def test_obfuscation_codepoint_pool_reports_capacity_overflow(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        with self.assertRaisesRegex(ValueError, "可用布局安全混淆码位不足"):
            font_encrypt.sample_obfuscation_codepoints(
                len(FONT_OBFUSCATION_LAYOUT_CODEPOINTS) + 1
            )

    def test_obfuscation_codepoints_skip_source_text(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        codepoints = font_encrypt.sample_obfuscation_codepoints(
            32,
            excluded_codepoints=set(FONT_OBFUSCATION_LAYOUT_CODEPOINTS[:64]),
        )

        self.assertFalse(
            any(codepoint in FONT_OBFUSCATION_LAYOUT_CODEPOINTS[:64] for codepoint in codepoints)
        )

    def test_alnum_obfuscation_stays_inside_same_width_pool(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        mapping = font_encrypt.build_obfuscation_codepoint_mapping(
            "A0Ａ０",
            "。",
        )

        self.assertIn(mapping[ord("A")], FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS)
        self.assertIn(mapping[ord("0")], FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS)
        self.assertIn(mapping[ord("Ａ")], FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS)
        self.assertIn(mapping[ord("０")], FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS)
        self.assertNotEqual(mapping[ord("A")], ord("A"))
        self.assertNotEqual(mapping[ord("0")], ord("0"))
        self.assertNotEqual(mapping[ord("Ａ")], ord("Ａ"))
        self.assertNotEqual(mapping[ord("０")], ord("０"))

    def test_encrypt_font_rewrites_cmap_without_rebuilding_metrics(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            epub_path = temp_path / "book.epub"
            font_path = "OEBPS/Fonts/TestFont.ttf"
            with zipfile.ZipFile(epub_path, "w") as epub:
                epub.writestr(
                    "META-INF/container.xml",
                    """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
                )
                epub.writestr(
                    "OEBPS/content.opf",
                    """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="font" href="Fonts/TestFont.ttf" media-type="font/ttf"/>
  </manifest>
</package>""",
                )
                epub.writestr(
                    "OEBPS/style.css",
                    """@font-face { font-family: "TestFont"; src: url("Fonts/TestFont.ttf"); }
.body { font-family: "TestFont"; }""",
                )
                epub.writestr(
                    "OEBPS/chapter.xhtml",
                    """<html><head><link rel="stylesheet" href="style.css"/></head>
<body><p class="body">你好Ａ０。A0</p></body></html>""",
                )
                epub.writestr(font_path, build_test_font_bytes())

            font_encrypt = FontEncrypt(
                str(epub_path),
                str(temp_path),
                target_font_families=["TestFont"],
            )
            font_encrypt.get_mapping()
            font_encrypt.clean_text()
            font_encrypt.encrypt_font()
            font_encrypt.read_html()

            output_path = temp_path / "book_font_encrypt.epub"
            with zipfile.ZipFile(output_path) as output_epub:
                html = output_epub.read("OEBPS/chapter.xhtml").decode("utf-8")
                encrypted_font = TTFont(BytesIO(output_epub.read(font_path)))

            best_cmap = encrypted_font.getBestCmap()
            body_text = re.search(r'<p class="body">(.*?)</p>', html).group(1)

            self.assertEqual(len(body_text), len("你好Ａ０。A0"))
            self.assertNotEqual(body_text[0], "你")
            self.assertNotEqual(body_text[1], "好")
            self.assertNotEqual(body_text[2], "Ａ")
            self.assertNotEqual(body_text[3], "０")
            self.assertEqual(body_text[4], "。")
            self.assertNotEqual(body_text[5], "A")
            self.assertNotEqual(body_text[6], "0")
            self.assertEqual(best_cmap[ord("。")], "uni3002")
            self.assertEqual(best_cmap[ord(body_text[0])], "uni4F60")
            self.assertEqual(best_cmap[ord(body_text[1])], "uni597D")
            self.assertEqual(best_cmap[ord(body_text[2])], "uniFF21")
            self.assertEqual(best_cmap[ord(body_text[3])], "uniFF10")
            self.assertEqual(best_cmap[ord(body_text[5])], "A")
            self.assertEqual(best_cmap[ord(body_text[6])], "zero")
            self.assertEqual(encrypted_font["hmtx"]["uni3002"], (500, 0))
            self.assertEqual(encrypted_font["OS/2"].usWinAscent, 950)
            self.assertTrue(all(0xAC00 <= ord(char) <= 0xD7AF for char in body_text[:2]))
            self.assertTrue(
                all(
                    ord(char) in FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS
                    for char in body_text[2:4]
                )
            )
            self.assertTrue(
                all(
                    ord(char) in FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS
                    for char in body_text[5:]
                )
            )


if __name__ == "__main__":
    unittest.main()
