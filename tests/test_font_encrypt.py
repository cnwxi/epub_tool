import unittest
import unicodedata
import re
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from utils.encrypt_font import (
    FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS,
    FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS,
    FONT_OBFUSCATION_LAYOUT_CODEPOINTS,
    FontEncrypt,
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
