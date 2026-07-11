import os
import tempfile
import unittest
import zipfile

from python_backend.services.decrypt_epub import EpubTool, run


BASE_IMAGE_HREF = "Images/%2A%3A.jpg"
SLIM_IMAGE_HREF = "Images/%2A%3A~slim.jpg"


def build_duokan_slim_epub(
    epub_path,
    slim_id="f4",
    slim_properties="cover-image",
    second_image_href=SLIM_IMAGE_HREF,
    second_archive_name="OEBPS/Images/*:~slim.jpg",
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
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata>
    <meta refines="#{slim_id}" property="title-type">cover</meta>
  </metadata>
  <manifest>
    <item id="f2" href="{BASE_IMAGE_HREF}" media-type="image/jpeg" properties="cover-image"/>
    <item id="{slim_id}" href="{second_image_href}" media-type="image/jpeg" properties="{slim_properties}"/>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<img src="../{BASE_IMAGE_HREF}"/>
<img src="../{second_image_href}"/>
</body></html>""",
        )
        epub.writestr("OEBPS/Images/*:.jpg", b"base-image")
        epub.writestr(second_archive_name, b"second-image")


def build_fragment_reference_epub(epub_path):
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
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf">
  <metadata/>
  <manifest>
    <item id="f2" href="{BASE_IMAGE_HREF}" media-type="image/jpeg"/>
    <item id="hash-image" href="Images/a%23b.jpg" media-type="image/jpeg"/>
    <item id="style" href="Styles/style.css" media-type="text/css"/>
    <item id="other-style" href="Styles/other.css" media-type="text/css"/>
    <item id="font" href="Fonts/font.ttf" media-type="font/ttf"/>
    <item id="audio" href="Audio/audio.mp3" media-type="audio/mpeg"/>
    <item id="video" href="Video/video.mp4" media-type="video/mp4"/>
    <item id="script" href="Misc/script.js" media-type="application/javascript"/>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="other" href="Text/other.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
  <guide><reference type="text" href="Text/chapter.xhtml#guide-fragment"/></guide>
</package>""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<a href="../{BASE_IMAGE_HREF}#href-fragment">image</a>
<a href="other.xhtml#chapter-fragment">chapter</a>
<a href="#local-fragment">local</a>
<img src="../{BASE_IMAGE_HREF}#src-fragment"/>
<img src="../Images/a%23b.jpg#encoded-fragment"/>
<img src="https://example.com/remote.jpg#remote-fragment"/>
<video src="../Video/video.mp4#video-fragment" poster="../{BASE_IMAGE_HREF}#poster-fragment"/>
<audio src="../Audio/audio.mp3#audio-fragment"/>
<script src="../Misc/script.js#script-fragment"></script>
<div placeholder="../{BASE_IMAGE_HREF}#placeholder-fragment"
     activestate="../{BASE_IMAGE_HREF}#active-fragment"
     zy-cover-pic="../{BASE_IMAGE_HREF}#cover-fragment"
     style="background:url('../{BASE_IMAGE_HREF}#inline-fragment');fill:url(#gradient)"></div>
</body></html>""",
        )
        epub.writestr(
            "OEBPS/Text/other.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body id="chapter-fragment"/></html>',
        )
        epub.writestr(
            "OEBPS/Styles/style.css",
            """@import "other.css";
@import "other.css#import-fragment";
.image { background: url("../Images/*:.jpg#css-image-fragment"); }
.font { src: url("../Fonts/font.ttf#font-fragment"); }
.local { fill: url(#paint); }
""",
        )
        epub.writestr("OEBPS/Styles/other.css", "body { color: black; }")
        epub.writestr(
            "OEBPS/toc.ncx",
            """<ncx><navMap><navPoint><content src="Text/chapter.xhtml#toc-fragment"/></navPoint></navMap></ncx>""",
        )
        epub.writestr("OEBPS/Images/*:.jpg", b"base-image")
        epub.writestr("OEBPS/Images/a#b.jpg", b"encoded-hash-image")
        epub.writestr("OEBPS/Fonts/font.ttf", b"font")
        epub.writestr("OEBPS/Audio/audio.mp3", b"audio")
        epub.writestr("OEBPS/Video/video.mp4", b"video")
        epub.writestr("OEBPS/Misc/script.js", b"script")


class DuokanSlimImageTest(unittest.TestCase):
    def test_href_pair_renames_slim_manifest_id_and_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_duokan_slim_epub(epub_path)

            self.assertEqual(run(epub_path, temp_dir), 0)

            output_path = os.path.join(temp_dir, "book_decrypt.epub")
            with zipfile.ZipFile(output_path) as epub:
                names = set(epub.namelist())
                self.assertIn("OEBPS/Images/f2.jpg", names)
                self.assertIn("OEBPS/Images/f2~slim.jpg", names)

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn(
                    'id="f2" href="Images/f2.jpg" media-type="image/jpeg" '
                    'properties="cover-image"',
                    opf,
                )
                self.assertIn(
                    'id="f2~slim" href="Images/f2~slim.jpg" '
                    'media-type="image/jpeg" properties="cover-image"',
                    opf,
                )
                self.assertIn('refines="#f2~slim"', opf)
                self.assertNotIn('id="f4"', opf)

                xhtml = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                self.assertIn("../Images/f2.jpg", xhtml)
                self.assertIn("../Images/f2~slim.jpg", xhtml)

    def test_id_suffix_does_not_mark_plain_href_as_slim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_duokan_slim_epub(
                epub_path,
                slim_id="f4_slim",
                slim_properties="cover-image",
                second_image_href="Images/other.jpg",
                second_archive_name="OEBPS/Images/other.jpg",
            )

            epub = EpubTool(epub_path)
            try:
                filenames = {
                    item_id: new_href
                    for item_id, _, _, new_href in epub.image_list
                }
            finally:
                epub.close_files()

            self.assertEqual(filenames["f2"], "f2.jpg")
            self.assertEqual(filenames["f4_slim"], "f4_slim.jpg")

    def test_underscore_slim_href_uses_canonical_tilde_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_duokan_slim_epub(
                epub_path,
                second_image_href="Images/%2A%3A_slim.jpg",
                second_archive_name="OEBPS/Images/*:_slim.jpg",
            )

            epub = EpubTool(epub_path)
            try:
                filenames = {
                    item_id: new_href
                    for item_id, _, _, new_href in epub.image_list
                }
                manifest_id_renames = epub.manifest_id_renames.copy()
            finally:
                epub.close_files()

            self.assertEqual(filenames["f4"], "f2~slim.jpg")
            self.assertEqual(manifest_id_renames["f4"], "f2~slim")

    def test_unpaired_slim_id_does_not_append_slim_twice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_duokan_slim_epub(
                epub_path,
                slim_id="f4~slim",
                second_image_href="Images/other~slim.jpg",
                second_archive_name="OEBPS/Images/other~slim.jpg",
            )

            epub = EpubTool(epub_path)
            try:
                filenames = {
                    item_id: new_href
                    for item_id, _, _, new_href in epub.image_list
                }
                manifest_id_renames = epub.manifest_id_renames.copy()
            finally:
                epub.close_files()

            self.assertEqual(filenames["f4~slim"], "f4~slim.jpg")
            self.assertEqual(manifest_id_renames["f4~slim"], "f4~slim")

    def test_fragment_is_preserved_for_all_resource_rewrite_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_fragment_reference_epub(epub_path)

            self.assertEqual(run(epub_path, temp_dir), 0)

            output_path = os.path.join(temp_dir, "book_decrypt.epub")
            with zipfile.ZipFile(output_path) as epub:
                xhtml = epub.read("OEBPS/Text/chapter.xhtml").decode("utf-8")
                for reference in [
                    "../Images/f2.jpg#href-fragment",
                    "other.xhtml#chapter-fragment",
                    "#local-fragment",
                    "../Images/f2.jpg#src-fragment",
                    "../Images/hash-image.jpg#encoded-fragment",
                    "https://example.com/remote.jpg#remote-fragment",
                    "../Video/video.mp4#video-fragment",
                    "../Images/f2.jpg#poster-fragment",
                    "../Audio/audio.mp3#audio-fragment",
                    "../Misc/script.js#script-fragment",
                    "../Images/f2.jpg#placeholder-fragment",
                    "../Images/f2.jpg#active-fragment",
                    "../Images/f2.jpg#cover-fragment",
                    "../Images/f2.jpg#inline-fragment",
                    "url(#gradient)",
                ]:
                    self.assertIn(reference, xhtml)

                css = epub.read("OEBPS/Styles/style.css").decode("utf-8")
                self.assertIn('@import "other-style.css";', css)
                self.assertIn('other-style.css#import-fragment', css)
                self.assertIn("../Images/f2.jpg#css-image-fragment", css)
                self.assertIn("../Fonts/font.ttf#font-fragment", css)
                self.assertIn("url(#paint)", css)

                toc = epub.read("OEBPS/toc.ncx").decode("utf-8")
                self.assertIn("Text/chapter.xhtml#toc-fragment", toc)

                opf = epub.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn(
                    'href="Text/chapter.xhtml#guide-fragment"',
                    opf,
                )
