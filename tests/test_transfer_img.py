import os
import tempfile
import unittest
import zipfile
from io import BytesIO

from PIL import Image

from utils.transfer_img import run_epub_img_transfer


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
</package>""".format(
                cover_content=cover_content,
                cover_item_id=cover_item_id,
                cover_item_href=cover_item_href,
            ),
        )
        epub.writestr("OEBPS/Images/cover.webp", build_webp_bytes("RGB"))


class TransferImagePathMappingTest(unittest.TestCase):
    def test_same_basename_webp_uses_full_book_path_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(epub_path)

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                names = set(epub.namelist())
                self.assertIn("OEBPS/Images/a.png", names)
                self.assertIn("OEBPS/Other/a.jpg", names)
                self.assertNotIn("OEBPS/Images/a.jpg", names)

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('href="Images/a.png"', opf)
                self.assertIn('href="Other/a.jpg"', opf)
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

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                html = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn("../Images/a.png?rev=1", html)
                self.assertIn("../Other/a.jpg#icon", html)
                self.assertNotIn("../Images/a.webp?rev=1", html)
                self.assertNotIn("../Other/a.webp#icon", html)

                css = epub.read("OEBPS/Styles/style.css").decode("utf-8")
                self.assertIn('url("../Images/a.png?rev=1")', css)
                self.assertIn("url(../Other/a.jpg#cover)", css)
                self.assertNotIn("../Images/a.webp?rev=1", css)
                self.assertNotIn("../Other/a.webp#cover", css)

    def test_same_basename_same_output_extension_uses_unique_manifest_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_same_basename_webp_epub(
                epub_path,
                first_image_mode="RGB",
                second_image_mode="RGB",
            )

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('id="a.jpg"', opf)
                self.assertIn('id="a_2.jpg"', opf)
                self.assertIn('href="Images/a.jpg"', opf)
                self.assertIn('href="Other/a.jpg"', opf)

    def test_cover_meta_tracks_renamed_manifest_item_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(epub_path)

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="cover.jpg"', opf)
                self.assertIn('id="cover.jpg"', opf)
                self.assertIn('href="Images/cover.jpg"', opf)
                self.assertNotIn('content="cover.webp"', opf)

    def test_manifest_id_ignores_href_query_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(
                epub_path,
                cover_content="cover-image",
                cover_item_id="cover-image",
                cover_item_href="Images/cover.webp?rev=1",
            )

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="cover.jpg"', opf)
                self.assertIn('id="cover.jpg"', opf)
                self.assertIn('href="Images/cover.jpg?rev=1"', opf)
                self.assertNotIn('content="cover.jpg?rev=1"', opf)
                self.assertNotIn('id="cover.jpg?rev=1"', opf)

    def test_cover_meta_path_tracks_replaced_image_href(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_cover_meta_webp_epub(
                epub_path,
                cover_content="Images/cover.webp",
                cover_item_id="cover-image",
            )

            result = run_epub_img_transfer(epub_path, temp_dir)

            self.assertEqual(result, 0)
            output_path = os.path.join(temp_dir, "book_transfer.epub")
            with zipfile.ZipFile(output_path) as epub:
                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('content="Images/cover.jpg"', opf)
                self.assertIn('href="Images/cover.jpg"', opf)
                self.assertNotIn('content="Images/cover.webp"', opf)


if __name__ == "__main__":
    unittest.main()
