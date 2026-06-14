import os
import tempfile
import unittest
import zipfile

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


class FontDecryptReferenceCleanupTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
