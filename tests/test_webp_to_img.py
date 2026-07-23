import os
import tempfile
import unittest
import zipfile
from io import BytesIO

from PIL import Image

from python_backend.services.image.webp_to_img import run


def build_webp_bytes(mode):
    color = (255, 0, 0, 128) if mode == "RGBA" else (0, 255, 0)
    image = Image.new(mode, (2, 2), color)
    buffer = BytesIO()
    image.save(buffer, format="WEBP")
    return buffer.getvalue()


def build_same_basename_webp_epub(
    epub_path,
    first_image_mode="RGBA",
    second_image_mode="RGB",
    include_reference_suffixes=False,
):
    html_suffix_references = ""
    css_suffix_references = ""
    if include_reference_suffixes:
        html_suffix_references = """
<img src="../Images/a.webp?rev=1"/>
<image xlink:href="../Other/a.webp#icon"/>
<img srcset="data:image/png;base64,AAAA 1x, ../Images/a.webp 2x"/>
<img srcset="../Images/a.webp,../Other/a.webp"/>
"""
        css_suffix_references = """.query { background-image: url("../Images/a.webp?rev=1"); }
.fragment { background-image: url(../Other/a.webp#cover); }
"""

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
    <item id="img1" href="Images/a.webp" media-type="image/webp"/>
    <item id="img2" href="Other/a.webp" media-type="image/webp"/>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            """<html><body>
<img src="../Images/a.webp"/>
<img src="../Other/a.webp"/>
{html_suffix_references}</body></html>""".format(
                html_suffix_references=html_suffix_references
            ),
        )
        epub.writestr(
            "OEBPS/Styles/style.css",
            """.cover {{ background-image: url("../Images/a.webp"); }}
.other {{ background-image: url("../Other/a.webp"); }}
{css_suffix_references}""".format(
                css_suffix_references=css_suffix_references
            ),
        )
        epub.writestr("OEBPS/Images/a.webp", build_webp_bytes(first_image_mode))
        epub.writestr("OEBPS/Other/a.webp", build_webp_bytes(second_image_mode))


def build_cover_meta_webp_epub(
    epub_path,
    cover_content="cover.webp",
    cover_item_id="cover.webp",
    cover_item_href="Images/cover.webp",
    cover_data=None,
):
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
<package version="2.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata>
    <meta name="cover" content="{cover_content}"/>
  </metadata>
  <manifest>
    <item id="{cover_item_id}" href="{cover_item_href}" media-type="image/webp"/>
  </manifest>
  <spine/>
  <guide><reference type="cover" href="{cover_item_href}"/></guide>
</package>""".format(
                cover_content=cover_content,
                cover_item_id=cover_item_id,
                cover_item_href=cover_item_href,
            ),
        )
        epub.writestr(
            "OEBPS/Images/cover.webp",
            build_webp_bytes("RGB") if cover_data is None else cover_data,
        )


def build_no_webp_epub(epub_path):
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
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr("OEBPS/chapter.xhtml", "<html><body>text</body></html>")


class TransferImagePathMappingTest(unittest.TestCase):
    def test_epub_without_webp_is_skipped_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_no_webp_epub(epub_path)

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, "skip")
            self.assertFalse(
                os.path.exists(os.path.join(temp_dir, "book_webp_to_img.epub"))
            )

    def test_invalid_webp_fails_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(epub_path, cover_data=b"not a webp image")

            with self.assertRaisesRegex(RuntimeError, "WebP 图片转换失败"):
                run(epub_path, temp_dir, options={"quality": 82})

            self.assertFalse(
                os.path.exists(os.path.join(temp_dir, "book_webp_to_img.epub"))
            )

    def test_transparent_webp_png_quantization_is_optional(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(epub_path)

            self.assertEqual(
                run(epub_path, temp_dir, options={"quality": 82, "png_quantize": True}),
                0,
            )

            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                with Image.open(BytesIO(epub.read("OEBPS/Images/a.png"))) as image:
                    self.assertEqual(image.mode, "P")
                    self.assertIn("transparency", image.info)

    def test_same_basename_webp_uses_full_book_path_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(epub_path)

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                names = set(epub.namelist())
                self.assertIn("OEBPS/Images/a.png", names)
                self.assertIn("OEBPS/Other/a.jpg", names)
                self.assertNotIn("OEBPS/Images/a.jpg", names)

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('href="Images/a.png"', opf)
                self.assertIn('href="Other/a.jpg"', opf)
                self.assertIn('id="img1" href="Images/a.png" media-type="image/png"', opf)
                self.assertIn('id="img2" href="Other/a.jpg" media-type="image/jpeg"', opf)
                self.assertNotIn('href="Images/a.jpg"', opf)

                html = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn("../Images/a.png", html)
                self.assertIn("../Other/a.jpg", html)
                self.assertNotIn("../Images/a.jpg", html)

                css = epub.read("OEBPS/Styles/style.css").decode("utf-8")
                self.assertIn('url("../Images/a.png")', css)
                self.assertIn('url("../Other/a.jpg")', css)
                self.assertNotIn('url("../Images/a.jpg")', css)

    def test_html_svg_and_css_references_preserve_url_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(epub_path, include_reference_suffixes=True)

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                html = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn("../Images/a.png?rev=1", html)
                self.assertIn("../Other/a.jpg#icon", html)
                self.assertNotIn("../Images/a.webp?rev=1", html)
                self.assertNotIn("../Other/a.webp#icon", html)
                self.assertIn(
                    'srcset="data:image/png;base64,AAAA 1x, ../Images/a.png 2x"',
                    html,
                )
                self.assertIn(
                    'srcset="../Images/a.png,../Other/a.jpg"',
                    html,
                )

                css = epub.read("OEBPS/Styles/style.css").decode("utf-8")
                self.assertIn('url("../Images/a.png?rev=1")', css)
                self.assertIn("url(../Other/a.jpg#cover)", css)
                self.assertNotIn("../Images/a.webp?rev=1", css)
                self.assertNotIn("../Other/a.webp#cover", css)

    def test_same_basename_same_output_extension_preserves_manifest_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(
                epub_path,
                first_image_mode="RGB",
                second_image_mode="RGB",
            )

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('id="img1"', opf)
                self.assertIn('id="img2"', opf)
                self.assertNotIn('id="a.jpg"', opf)
                self.assertNotIn('id="a_2.jpg"', opf)
                self.assertIn('href="Images/a.jpg"', opf)
                self.assertIn('href="Other/a.jpg"', opf)

    def test_cover_manifest_id_and_epub2_meta_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(epub_path)

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="cover.webp"', opf)
                self.assertIn('id="cover.webp"', opf)
                self.assertIn('href="Images/cover.jpg"', opf)
                self.assertIn('media-type="image/jpeg"', opf)
                self.assertIn(
                    '<reference type="cover" href="Images/cover.jpg"', opf
                )

    def test_manifest_id_ignores_href_query_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(
                epub_path,
                cover_content="cover-image",
                cover_item_id="cover-image",
                cover_item_href="Images/cover.webp?rev=1",
            )

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="cover-image"', opf)
                self.assertIn('id="cover-image"', opf)
                self.assertIn('href="Images/cover.jpg?rev=1"', opf)
                self.assertIn('media-type="image/jpeg"', opf)
                self.assertNotIn('content="cover-image?rev=1"', opf)
                self.assertNotIn('id="cover-image?rev=1"', opf)

    def test_cover_meta_path_tracks_replaced_image_href(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(
                epub_path,
                cover_content="Images/cover.webp",
                cover_item_id="cover-image",
            )

            result = run(epub_path, temp_dir, options={"quality": 82})

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_webp_to_img.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="Images/cover.webp"', opf)
                self.assertIn('id="cover-image"', opf)
                self.assertIn('href="Images/cover.jpg"', opf)
                self.assertIn('media-type="image/jpeg"', opf)


if __name__ == "__main__":
    unittest.main()
