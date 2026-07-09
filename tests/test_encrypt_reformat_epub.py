import os
import tempfile
import unittest
import zipfile

from utils.encrypt_epub import EpubTool as EncryptEpubTool
from utils.encrypt_epub import run as run_encrypt
from utils.reformat_epub import run as run_reformat


def build_safe_duokan_slim_epub(
    epub_path,
    slim_id="f4",
    second_image_href="Images/base_slim.jpg",
    second_archive_name="OEBPS/Images/base_slim.jpg",
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
  <metadata/>
  <manifest>
    <item id="f2" href="Images/base.jpg" media-type="image/jpeg"/>
    <item id="{slim_id}" href="{second_image_href}" media-type="image/jpeg"/>
    <item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        epub.writestr(
            "OEBPS/Text/chapter.xhtml",
            f"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
<img src="../Images/base.jpg"/>
<img src="../{second_image_href}"/>
</body></html>""",
        )
        epub.writestr("OEBPS/Images/base.jpg", b"base-image")
        epub.writestr(second_archive_name, b"second-image")


def build_safe_fragment_reference_epub(epub_path):
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
  <metadata/>
  <manifest>
    <item id="image" href="Images/base.jpg" media-type="image/jpeg"/>
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
            """<html xmlns="http://www.w3.org/1999/xhtml"><head>
<link href="../Styles/style.css#style-fragment" rel="stylesheet"/>
</head><body>
<a href="../Images/base.jpg#href-fragment">image</a>
<a href="other.xhtml#chapter-fragment">chapter</a>
<a href="#local-fragment">local</a>
<img src="../Images/base.jpg#src-fragment"/>
<img src="../Images/a%23b.jpg#encoded-fragment"/>
<img src="https://example.com/remote.jpg#remote-fragment"/>
<video src="../Video/video.mp4#video-fragment" poster="../Images/base.jpg#poster-fragment"/>
<audio src="../Audio/audio.mp3#audio-fragment"/>
<script src="../Misc/script.js#script-fragment"></script>
<div placeholder="../Images/base.jpg#placeholder-fragment"
     activestate="../Images/base.jpg#active-fragment"
     zy-cover-pic="../Images/base.jpg#cover-fragment"
     style="background:url('../Images/base.jpg#inline-fragment');fill:url(#gradient)"></div>
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
.image { background: url("../Images/base.jpg#css-image-fragment"); }
.font { src: url("../Fonts/font.ttf#font-fragment"); }
.local { fill: url(#paint); }
""",
        )
        epub.writestr("OEBPS/Styles/other.css", "body { color: black; }")
        epub.writestr(
            "OEBPS/toc.ncx",
            """<ncx><navMap><navPoint><content src="Text/chapter.xhtml#toc-fragment"/></navPoint></navMap></ncx>""",
        )
        epub.writestr("OEBPS/Images/base.jpg", b"base-image")
        epub.writestr("OEBPS/Images/a#b.jpg", b"encoded-hash-image")
        epub.writestr("OEBPS/Fonts/font.ttf", b"font")
        epub.writestr("OEBPS/Audio/audio.mp3", b"audio")
        epub.writestr("OEBPS/Video/video.mp4", b"video")
        epub.writestr("OEBPS/Misc/script.js", b"script")


def read_texts(epub, prefix):
    return "\n".join(
        epub.read(name).decode("utf-8")
        for name in epub.namelist()
        if name.startswith(prefix)
    )


class EncryptDuokanSlimTest(unittest.TestCase):
    def test_href_pair_uses_same_encrypted_base_with_canonical_slim_suffix(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_safe_duokan_slim_epub(epub_path)

            epub = EncryptEpubTool(epub_path)
            try:
                filenames = {
                    item_id: new_href for item_id, _, _, new_href in epub.image_list
                }
            finally:
                epub.close_files()

            base_stem, base_ext = os.path.splitext(filenames["f2"])
            self.assertEqual(filenames["f4"], f"{base_stem}~slim{base_ext}")

            self.assertEqual(run_encrypt(epub_path, temp_dir), 0)
            output_path = os.path.join(temp_dir, "book_encrypt.epub")
            with zipfile.ZipFile(output_path) as output:
                names = set(output.namelist())
                self.assertIn(f"OEBPS/Images/{filenames['f2']}", names)
                self.assertIn(f"OEBPS/Images/{filenames['f4']}", names)
                opf = output.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn(f'href="Images/{filenames["f2"]}"', opf)
                self.assertIn(f'href="Images/{filenames["f4"]}"', opf)

    def test_id_suffix_does_not_mark_plain_href_as_slim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_safe_duokan_slim_epub(
                epub_path,
                slim_id="f4_slim",
                second_image_href="Images/other.jpg",
                second_archive_name="OEBPS/Images/other.jpg",
            )

            epub = EncryptEpubTool(epub_path)
            try:
                filenames = {
                    item_id: new_href for item_id, _, _, new_href in epub.image_list
                }
            finally:
                epub.close_files()

            self.assertNotIn("~slim", filenames["f4_slim"])


class FragmentRewriteTest(unittest.TestCase):
    def assert_fragments_preserved(self, output_path):
        with zipfile.ZipFile(output_path) as epub:
            xhtml = read_texts(epub, "OEBPS/Text/")
            for fragment in [
                "#style-fragment",
                "#href-fragment",
                "#chapter-fragment",
                "#local-fragment",
                "#src-fragment",
                "#encoded-fragment",
                "https://example.com/remote.jpg#remote-fragment",
                "#video-fragment",
                "#poster-fragment",
                "#audio-fragment",
                "#script-fragment",
                "#placeholder-fragment",
                "#active-fragment",
                "#cover-fragment",
                "#inline-fragment",
                "url(#gradient)",
            ]:
                self.assertIn(fragment, xhtml)

            css = read_texts(epub, "OEBPS/Styles/")
            self.assertIn("@import", css)
            self.assertIn("#import-fragment", css)
            self.assertIn("#css-image-fragment", css)
            self.assertIn("#font-fragment", css)
            self.assertIn("url(#paint)", css)

            toc = epub.read("OEBPS/toc.ncx").decode("utf-8")
            self.assertIn("#toc-fragment", toc)

            opf = epub.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn("#guide-fragment", opf)

    def test_encrypt_fragment_is_preserved_for_all_resource_rewrite_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_safe_fragment_reference_epub(epub_path)

            self.assertEqual(run_encrypt(epub_path, temp_dir), 0)

            self.assert_fragments_preserved(
                os.path.join(temp_dir, "book_encrypt.epub")
            )

    def test_reformat_fragment_is_preserved_for_all_resource_rewrite_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_path = os.path.join(temp_dir, "book.epub")
            build_safe_fragment_reference_epub(epub_path)

            self.assertEqual(run_reformat(epub_path, temp_dir), 0)

            self.assert_fragments_preserved(
                os.path.join(temp_dir, "book_reformat.epub")
            )
