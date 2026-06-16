import os
import tempfile
import unittest
import zipfile

from bs4 import BeautifulSoup

from utils.decrypt_font import FontDecrypt


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

            output_path = os.path.join(temp_dir, "book_font_decrypt.epub")
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

            output_path = os.path.join(temp_dir, "book_font_decrypt.epub")
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
                self.assertIn(".ocr-failure{font-size:1em;", html)
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
