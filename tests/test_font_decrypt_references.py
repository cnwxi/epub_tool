import os
import tempfile
import unittest
import zipfile

from bs4 import BeautifulSoup

from python_backend.services.font.decrypt_font import FontDecrypt


def build_reference_test_epub(epub_path):
    with zipfile.ZipFile(epub_path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
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
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <manifest>
    <item id="style" href="Styles/style.css" media-type="text/css"/>
    <item id="font" href="Fonts/obf.ttf" media-type="font/ttf"/>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@font-face {
  font-family: "Obf";
  src: url("../Fonts/obf.ttf");
}
.obf { font-family: "Obf", serif; }
""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <style>.inline { font-family: "Obf", sans-serif; }</style>
  </head>
  <body>
    <p class="obf">가각</p>
    <p style='font-family: "Obf", serif;'>가</p>
  </body>
</html>""",
        )
        epub.writestr("OEBPS/Fonts/obf.ttf", b"dummy-font")


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


class FontDecryptReferenceCleanupTest(unittest.TestCase):
    def test_find_char_mapping_uses_effective_font_without_selector_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cascade_test_epub(epub_path)

            task = FontDecrypt(epub_path, output_path=temp_dir)
            base_font = "OEBPS/Fonts/base.ttf"
            fs2_font = "OEBPS/Fonts/fs2.ttf"
            task.css_selector_to_font_mapping = {
                ".fs2": fs2_font,
                "p": base_font,
            }
            task.css_selector_font_rules = [
                {
                    "selector": "p",
                    "font_file": base_font,
                    "specificity": task.calculate_selector_specificity("p"),
                    "order": 1,
                },
                {
                    "selector": ".fs2",
                    "font_file": fs2_font,
                    "specificity": task.calculate_selector_specificity(".fs2"),
                    "order": 2,
                },
            ]

            task.find_char_mapping()
            task.close_file()

            self.assertEqual(task.font_to_char_mapping[fs2_font], "甲乙")
            self.assertEqual(task.font_to_char_mapping[base_font], "丙丁")

    def test_find_char_mapping_skips_nested_non_target_font_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_nested_font_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["base"],
            )
            base_font = "OEBPS/Fonts/base.ttf"
            fs2_font = "OEBPS/Fonts/fs2.ttf"
            task.font_to_font_family_mapping = {
                "base": base_font,
                "fs2": fs2_font,
            }
            task.css_selector_to_font_mapping = {
                ".base": base_font,
            }
            task.css_selector_font_rules = [
                {
                    "selector": ".base",
                    "font_file": base_font,
                    "specificity": task.calculate_selector_specificity(".base"),
                    "order": 1,
                },
                {
                    "selector": ".fs2",
                    "font_file": fs2_font,
                    "specificity": task.calculate_selector_specificity(".fs2"),
                    "order": 2,
                },
            ]

            task.find_char_mapping()
            task.close_file()

            self.assertEqual(task.font_to_char_mapping[base_font], "甲丙")
            self.assertNotIn(fs2_font, task.font_to_char_mapping)

    def test_find_char_mapping_skips_generic_and_unresolved_font_overrides(self):
        cases = [
            "serif",
            '"SomeSystemFont"',
        ]
        for override_family in cases:
            with self.subTest(override_family=override_family):
                with tempfile.TemporaryDirectory() as temp_dir:
                    epub_path = os.path.join(temp_dir, "book.epub")
                    build_unresolved_override_test_epub(epub_path, override_family)

                    task = FontDecrypt(
                        epub_path,
                        output_path=temp_dir,
                        target_font_families=["TargetFont"],
                    )
                    task.get_mapping()
                    task.close_file()

                    self.assertEqual(
                        task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                        "外",
                    )

    def test_find_char_mapping_respects_important_target_font(self):
        cases = [
            "serif",
            '"SomeSystemFont"',
        ]
        for override_family in cases:
            with self.subTest(override_family=override_family):
                with tempfile.TemporaryDirectory() as temp_dir:
                    epub_path = os.path.join(temp_dir, "book.epub")
                    build_important_target_font_test_epub(epub_path, override_family)

                    task = FontDecrypt(
                        epub_path,
                        output_path=temp_dir,
                        target_font_families=["TargetFont"],
                    )
                    task.get_mapping()
                    task.close_file()

                    self.assertEqual(
                        task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
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
                with tempfile.TemporaryDirectory() as temp_dir:
                    epub_path = os.path.join(temp_dir, "book.epub")
                    build_inline_cascade_test_epub(
                        epub_path,
                        css_declaration,
                        inline_style,
                    )

                    task = FontDecrypt(
                        epub_path,
                        output_path=temp_dir,
                        target_font_families=["TargetFont"],
                    )
                    task.get_mapping()
                    task.close_file()

                    target_font = "OEBPS/Fonts/target.ttf"
                    if expected_text is None:
                        self.assertNotIn(target_font, task.font_to_char_mapping)
                    else:
                        self.assertEqual(
                            task.font_to_char_mapping[target_font],
                            expected_text,
                        )

    def test_find_char_mapping_uses_document_stylesheet_scope_and_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_document_stylesheet_order_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_reads_imported_media_css_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_imported_media_css_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_keeps_function_selector_commas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_function_selector_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
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
                with tempfile.TemporaryDirectory() as temp_dir:
                    epub_path = os.path.join(temp_dir, "book.epub")
                    build_specificity_function_test_epub(epub_path, css_text, body_html)

                    task = FontDecrypt(
                        epub_path,
                        output_path=temp_dir,
                        target_font_families=["TargetFont"],
                    )
                    task.get_mapping()
                    task.close_file()

                    target_font = "OEBPS/Fonts/target.ttf"
                    if expected_text is None:
                        self.assertNotIn(target_font, task.font_to_char_mapping)
                    else:
                        self.assertEqual(
                            task.font_to_char_mapping[target_font],
                            expected_text,
                        )

    def test_find_char_mapping_uses_cssselect2_selector_matching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_complex_selector_match_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲丙",
            )

    def test_find_char_mapping_uses_xhtml_elementtree_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_xhtml_namespace_selector_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "乙",
            )

    def test_cssselect2_context_parses_marked_xhtml_source(self):
        html = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<body><br /><p class="target">甲</p></body>
</html>"""
        task = FontDecrypt.__new__(FontDecrypt)
        marked_html, marker_attr = task.inject_cssselect2_markers(html)
        soup = BeautifulSoup(marked_html, "html.parser")

        match_context = task.build_cssselect2_match_context(
            soup,
            marked_html,
            marker_attr,
        )
        task.remove_cssselect2_markers(soup, marker_attr)

        self.assertIsNotNone(match_context)
        self.assertEqual(len(match_context), len(soup.find_all(True)))
        self.assertNotIn(marker_attr, soup.decode())

    def test_find_char_mapping_filters_non_screen_media_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_media_filtered_blocker_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_ignores_print_only_target_font(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_print_only_target_font_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertNotIn(
                "OEBPS/Fonts/target.ttf",
                task.font_to_char_mapping,
            )

    def test_find_char_mapping_limits_scoped_rules_to_scope_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_scope_selector_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "内直",
            )

    def test_find_char_mapping_excludes_scope_limit_subtree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_scope_limit_selector_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "内直深",
            )

    def test_find_char_mapping_prefers_closer_scope_before_source_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_scope_proximity_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "近",
            )

    def test_find_char_mapping_blocks_initial_font_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_initial_font_keyword_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "外",
            )

    def test_find_char_mapping_inherits_font_keywords_from_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_inherit_font_keyword_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "丙丁",
            )

    def test_find_char_mapping_applies_all_font_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_all_font_keyword_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "戊己",
            )

    def test_find_char_mapping_applies_revert_layer_font_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_revert_layer_font_keyword_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲丙戊己庚",
            )

    def test_find_char_mapping_resolves_css_custom_property_fonts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_css_custom_property_font_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙丁丙己庚辛壬癸",
            )

    def test_find_char_mapping_inherits_invalid_var_font_from_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_invalid_var_font_inheritance_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "丁戊己壬癸丑卯",
            )

    def test_find_char_mapping_accepts_woff2_font_face_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_woff2_font_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["WoffTwo"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.woff2"],
                "甲乙",
            )

    def test_find_char_mapping_respects_import_supports_conditions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_import_supports_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_parses_font_shorthand_after_numeric_weight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_font_shorthand_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_ignores_late_import_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_late_import_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                "甲乙",
            )

    def test_find_char_mapping_filters_false_supports_and_container_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_supports_container_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["TargetFont"],
            )
            task.get_mapping()
            task.close_file()

            self.assertEqual(
                task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
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
                with tempfile.TemporaryDirectory() as temp_dir:
                    epub_path = os.path.join(temp_dir, "book.epub")
                    build_layer_cascade_test_epub(epub_path, css_text)

                    task = FontDecrypt(
                        epub_path,
                        output_path=temp_dir,
                        target_font_families=["TargetFont"],
                    )
                    task.get_mapping()
                    task.close_file()

                    self.assertEqual(
                        task.font_to_char_mapping["OEBPS/Fonts/target.ttf"],
                        expected_text,
                    )

    def test_write_epub_removes_references_for_skipped_target_fonts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_reference_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["Obf"],
            )
            task.css_selector_to_font_mapping = {
                ".obf": "OEBPS/Fonts/obf.ttf",
            }
            task.font_to_font_family_mapping = {
                "obf": "OEBPS/Fonts/obf.ttf",
            }
            task.font_to_char_mapping = {
                "OEBPS/Fonts/obf.ttf": "가각",
            }
            task.font_to_replace_mapping = {
                "OEBPS/Fonts/obf.ttf": {
                    "가": "你",
                    "각": "好",
                },
            }

            task.write_epub()

            output_path = os.path.join(temp_dir, "book_decrypt_font.epub")
            with zipfile.ZipFile(output_path) as epub:
                names = set(epub.namelist())
                self.assertNotIn("OEBPS/Fonts/obf.ttf", names)

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertNotIn("Fonts/obf.ttf", opf)
                self.assertNotIn('id="font"', opf)

                css = epub.read("OEBPS/Styles/style.css").decode("utf-8")
                self.assertNotIn("@font-face", css)
                self.assertNotIn("Fonts/obf.ttf", css)
                self.assertNotIn("Obf", css)
                self.assertIn("font-family: serif;", css)

                html = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn("你好", html)
                self.assertIn("你", html)
                self.assertNotIn("Obf", html)
                self.assertIn("font-family: sans-serif;", html)
                self.assertIn("font-family: serif;", html)

    def test_write_epub_embeds_ocr_failure_glyph_image_markup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_reference_test_epub(epub_path)

            task = FontDecrypt(
                epub_path,
                output_path=temp_dir,
                target_font_families=["Obf"],
            )
            font_path = "OEBPS/Fonts/obf.ttf"
            image_path = "OEBPS/Images/ocr-failures/a13f9c2b_U-AC00_OCR_LOW_CONF.png"
            task.css_selector_to_font_mapping = {
                ".obf": font_path,
            }
            task.font_to_font_family_mapping = {
                "obf": font_path,
            }
            task.font_to_char_mapping = {
                font_path: "가각",
            }
            task.font_to_replace_mapping = {
                font_path: {
                    "가": "[U+AC00 OCR_LOW_CONF]",
                    "각": "好",
                },
            }
            task.font_to_ocr_failure_mapping = {
                font_path: {
                    "가": {
                        "codepoint": "U+AC00",
                        "original_char": "가",
                        "status_code": "OCR_LOW_CONF",
                        "font_path": font_path,
                        "reason": "OCR 置信度过低",
                        "placeholder": "[U+AC00 OCR_LOW_CONF]",
                        "image_path": image_path,
                        "image_alt": "U+AC00 가 OCR_LOW_CONF",
                    },
                },
            }
            task.ocr_failure_image_bytes = {
                image_path: b"\x89PNG\r\n\x1a\n",
            }

            task.write_epub()

            output_path = os.path.join(temp_dir, "book_decrypt_font.epub")
            with zipfile.ZipFile(output_path) as epub:
                names = set(epub.namelist())
                self.assertIn(image_path, names)

                html = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn('class="ocr-failure"', html)
                self.assertIn('class="ocr-failure-glyph"', html)
                self.assertIn("a13f9c2b_U-AC00_OCR_LOW_CONF.png", html)
                self.assertIn('data-codepoint="U+AC00"', html)
                self.assertIn('data-original-char="가"', html)
                self.assertIn("OCR_LOW_CONF", html)
                self.assertIn("好", html)
                self.assertIn(
                    ".ocr-failure{font-size:1em;white-space:nowrap;line-height:1;}",
                    html,
                )
                self.assertIn(".ocr-failure img.ocr-failure-glyph{", html)
                self.assertIn("height:1.18em!important;", html)
                self.assertIn("width:auto!important;", html)
                self.assertIn("max-width:none!important;", html)
                self.assertIn("max-height:none!important;", html)
                self.assertIn("vertical-align:-0.22em!important;", html)
                self.assertIn("display:inline-block!important;", html)
                self.assertNotIn("[<img", html)
                self.assertNotIn(" OCR_LOW_CONF]</span>", html)

                failure_span = BeautifulSoup(html, "html.parser").find(
                    "span",
                    class_="ocr-failure",
                )
                self.assertIsNotNone(failure_span)
                self.assertEqual(failure_span.get_text(), "")
                failure_image = failure_span.find("img")
                self.assertIsNotNone(failure_image)
                self.assertEqual(failure_image["alt"], "U+AC00 가 OCR_LOW_CONF")

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn(
                    'href="Images/ocr-failures/a13f9c2b_U-AC00_OCR_LOW_CONF.png"',
                    opf,
                )
                self.assertIn('media-type="image/png"', opf)


if __name__ == "__main__":
    unittest.main()
