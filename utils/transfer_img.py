import zipfile
import re
import os
import codecs
from PIL import Image
from io import BytesIO

try:
    from utils.log import logwriter
except:
    from log import logwriter

logger = logwriter()
from xml.etree import ElementTree


class ImageTransfer:
    def __init__(self, epub_path, output_path):
        if not os.path.exists(epub_path):
            raise Exception("EPUB文件不存在")

        self.epub_path = os.path.normpath(epub_path)
        self.epub = zipfile.ZipFile(epub_path)
        if output_path and os.path.exists(output_path):
            if os.path.isfile(output_path):
                raise Exception("输出路径不能是文件")
            if not os.path.exists(output_path):
                raise Exception(f"输出路径{output_path}不存在")
        else:
            output_path = os.path.dirname(epub_path)
            logger.write(f"输出路径不存在，使用默认路径: {output_path}")
        self.output_path = os.path.normpath(output_path)
        self.file_write_path = os.path.join(
            self.output_path,
            os.path.basename(self.epub_path).replace(".epub", "_transfer.epub"),
        )
        if os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)
        self.htmls = []
        self.css = []
        self.images = []
        self.opf = ""
        self.ori_files = []
        self.img_dict = {}
        # self.font_to_unchanged_file_mapping = {}
        self.target_epub = zipfile.ZipFile(
            self.file_write_path,
            "w",
            zipfile.ZIP_STORED,
            zipfile.ZIP_STORED,
        )
        for file in self.epub.namelist():
            if file.lower().endswith(".html") or file.endswith(".xhtml"):
                self.htmls.append(file)
            elif file.lower().endswith(".css"):
                self.css.append(file)
            elif file.lower().endswith((".webp")):
                self.images.append(file)
            elif file.lower().endswith(".opf"):
                self.opf = file
            else:
                self.ori_files.append(file)
        self._init_opf_path()

    def _decode_xml_bytes(self, data: bytes, default="utf-8") -> str:
        decode_order = [default, "utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
        if data.startswith(codecs.BOM_UTF8):
            decode_order = ["utf-8-sig"] + decode_order
        elif data.startswith(codecs.BOM_UTF16_LE):
            decode_order = ["utf-16", "utf-16le"] + decode_order
        elif data.startswith(codecs.BOM_UTF16_BE):
            decode_order = ["utf-16", "utf-16be"] + decode_order
        seen = set()
        for enc in decode_order:
            if not enc or enc in seen:
                continue
            seen.add(enc)
            try:
                text = data.decode(enc)
                return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            except UnicodeDecodeError:
                continue
        try:
            logger.write("XML decode fallback: utf decode failed, try gb18030")
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("gb18030"))
        except UnicodeDecodeError:
            logger.write("XML decode fallback: gb18030 failed, use latin-1")
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("latin-1"))

    def _read_xml_text(self, zip_path: str) -> str:
        try:
            data = self.epub.read(zip_path)
        except KeyError:
            raise FileNotFoundError(f"zip内缺少XML文件: {zip_path}")
        return self._decode_xml_bytes(data)

    def _sanitize_attr_value(self, value: str) -> str:
        value = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][\w.-]*;)", "&amp;", value)
        value = value.replace("<", "&lt;").replace(">", "&gt;")
        return value

    def _sanitize_xml_attr_text(self, xml_text: str) -> str:
        pattern = re.compile(
            r"(<[^>]+?)((?:\s+[^\s=>/]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'))?)+)(\s*/?>)",
            re.DOTALL,
        )

        def repl_tag(match):
            prefix, attrs, suffix = match.groups()
            attrs = re.sub(
                r"(=\s*\")([^\"]*)(\")",
                lambda m: m.group(1) + self._sanitize_attr_value(m.group(2)) + m.group(3),
                attrs,
                flags=re.DOTALL,
            )
            attrs = re.sub(
                r"(=\s*')([^']*)(')",
                lambda m: m.group(1) + self._sanitize_attr_value(m.group(2)) + m.group(3),
                attrs,
                flags=re.DOTALL,
            )
            return prefix + attrs + suffix

        return pattern.sub(repl_tag, xml_text)

    def _xml_parse_error_with_context(self, xml_text: str, label: str, err: Exception):
        logger.write(f"XML parse error [{label}]: {err}")
        pos = getattr(err, "position", None)
        if not pos:
            return
        line_no, col_no = pos
        logger.write(f"位置: line={line_no}, column={col_no}")
        lines = xml_text.splitlines()
        start = max(1, line_no - 2)
        end = min(len(lines), line_no + 2)
        logger.write(f"{label} 出错上下文:")
        for idx in range(start, end + 1):
            logger.write(f"{idx:>6}: {lines[idx - 1]}")

    def _parse_xml_safe(self, xml_text: str, label: str):
        first_err = None
        try:
            return ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as err:
            first_err = err
            self._xml_parse_error_with_context(xml_text, label, err)
        sanitized = self._sanitize_xml_attr_text(xml_text)
        if sanitized == xml_text:
            raise first_err
        try:
            logger.write(f"XML sanitize retry: {label}")
            return ElementTree.fromstring(sanitized)
        except ElementTree.ParseError as err2:
            self._xml_parse_error_with_context(sanitized, f"{label}[sanitized]", err2)
            raise

    def _init_opf_path(self):
        try:
            container_xml = self._read_xml_text("META-INF/container.xml")
            rf = re.search(
                r'<rootfile[^>]*full-path\s*=\s*([\'"])(?i:(.*?\.opf))\1',
                container_xml,
            )
            if rf:
                self.opf = rf.group(2)
                return
        except Exception as e:
            logger.write(f"读取 container.xml 失败，将回退扫描opf: {e}")
        if self.opf:
            return
        for file in self.epub.namelist():
            if file.lower().endswith(".opf"):
                self.opf = file
                return
        raise RuntimeError("无法发现opf文件")

    def _rewrite_opf_by_regex_fallback(self, opf_text: str) -> str:
        logger.write("opf_malformed_fallback_used: transfer_img")

        def re_meta_cover(match):
            attrs = match.group(1)
            c_match = re.search(r'content=(["\'])(.*?)\1', attrs, flags=re.IGNORECASE)
            if not c_match:
                return match.group(0)
            content = c_match.group(2)
            if not content.endswith(".webp"):
                return match.group(0)
            cover_basename = os.path.basename(content)
            if cover_basename not in self.img_dict:
                return match.group(0)
            replace_name = self.img_dict[cover_basename][0]
            new_content = content.replace(cover_basename, replace_name)
            attrs2 = re.sub(
                r'(content=(["\']))(.*?)(\2)',
                r"\g<1>" + new_content + r"\4",
                attrs,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            logger.write(
                f'{self.opf} 降级替换：<meta ... content="{content}"> -> content="{new_content}"'
            )
            return f"<meta{attrs2}>"

        opf_text = re.sub(
            r'<meta\b([^>]*\bname\s*=\s*(["\'])cover\2[^>]*)>',
            re_meta_cover,
            opf_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        def re_item_webp(match):
            attrs = match.group(1)
            media_match = re.search(
                r'media-type\s*=\s*(["\'])(.*?)\1', attrs, flags=re.IGNORECASE
            )
            href_match = re.search(r'href\s*=\s*(["\'])(.*?)\1', attrs, flags=re.IGNORECASE)
            if not media_match or not href_match:
                return match.group(0)
            if media_match.group(2).lower() != "image/webp":
                return match.group(0)
            href = href_match.group(2)
            href_basename = os.path.basename(href)
            if href_basename not in self.img_dict:
                return match.group(0)
            replace_name, replace_media_type = self.img_dict[href_basename]
            new_href = href.replace(href_basename, replace_name)
            attrs2 = re.sub(
                r'(href\s*=\s*(["\']))(.*?)(\2)',
                r"\g<1>" + new_href + r"\4",
                attrs,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            attrs2 = re.sub(
                r'(media-type\s*=\s*(["\']))(.*?)(\2)',
                r"\g<1>" + replace_media_type + r"\4",
                attrs2,
                count=1,
                flags=re.IGNORECASE | re.DOTALL,
            )
            id_match = re.search(r'id\s*=\s*(["\'])(.*?)\1', attrs2, flags=re.IGNORECASE)
            if id_match:
                attrs2 = re.sub(
                    r'(id\s*=\s*(["\']))(.*?)(\2)',
                    r"\g<1>" + replace_name + r"\4",
                    attrs2,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            logger.write(f'{self.opf} 降级替换：<item href="{href}" ...> -> href="{new_href}"')
            if match.group(0).endswith("/>"):
                return f"<item{attrs2}/>"
            return f"<item{attrs2}>"

        opf_text = re.sub(
            r"<item\b([^>]*?)\/?>",
            re_item_webp,
            opf_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return opf_text

    def read_files(self):
        for img_path in self.images:
            img_data = self.epub.read(img_path)
            # 将二进制数据转换为BytesIO对象，以便Pillow可以读取
            img_file = BytesIO(img_data)
            try:
                # 使用Pillow打开图片
                image = Image.open(img_file)
                img_basename = os.path.basename(img_path)  # xxx.webp
                if image.mode in ("RGBA", "LA") or (
                    image.mode == "P" and "transparency" in image.info
                ):
                    new_name = img_basename.replace(".webp", ".png")
                    self.img_dict[img_basename] = [new_name, "image/png"]
                    # 写入新epub
                    buffer = BytesIO()
                    image = image.quantize(colors=256, method=2)
                    image.save(buffer, format="PNG", optimize=True)
                    img_path = img_path.replace(img_basename, new_name)
                else:
                    new_name = img_basename.replace(".webp", ".jpg")
                    self.img_dict[img_basename] = [new_name, "image/jpeg"]
                    buffer = BytesIO()
                    image.save(buffer, format="JPEG")
                    img_path = img_path.replace(img_basename, new_name)

                    # 写入新epub
                self.target_epub.writestr(
                    img_path, buffer.getvalue(), zipfile.ZIP_DEFLATED
                )

            except Exception as e:
                # logger.write(f"无法处理图片 {img}: {str(e)}")
                logger.write(f"无法处理图片 {img_path}: {str(e)}")
                self.close_files()

        for item in self.ori_files:
            if item in self.epub.namelist():
                with self.epub.open(item) as f:
                    content = f.read()
                self.target_epub.writestr(item, content, zipfile.ZIP_DEFLATED)

    def replace(self):
        ns = {"opf": "http://www.idpf.org/2007/opf"}
        opf_file = self._read_xml_text(self.opf)
        try:
            root = self._parse_xml_safe(opf_file, label=f"OPF:{self.opf}")
        except Exception:
            modified_opf = self._rewrite_opf_by_regex_fallback(opf_file).encode("utf-8")
            self.target_epub.writestr(self.opf, modified_opf, zipfile.ZIP_DEFLATED)
            root = None
        if root is not None:
            for meta in root.findall('.//opf:meta[@name="cover"]', ns):
                content = meta.get("content")
                if content and content.endswith(".webp"):
                    logger.write(f"meta 替换cover：{content}")
                    cover_basename = os.path.basename(content)
                    if os.path.basename(content) in self.img_dict:
                        replace_name = self.img_dict[cover_basename][0]
                        new_content = content.replace(cover_basename, replace_name)
                        meta.set("content", new_content)
                        logger.write(
                            f'{self.opf} 替换：<meta name="cover" content="{content}" /> -> <meta name="cover" content="{new_content}"'
                        )
            # 遍历所有 <item> 标签
            for item in root.findall(".//opf:item", ns):
                media_type = item.get("media-type")
                if media_type == "image/webp":
                    id = item.get("id")
                    href = item.get("href")
                    href_basename = os.path.basename(href)
                    if href_basename in self.img_dict:
                        replace_name, replace_media_type = self.img_dict[href_basename]
                        item.set("id", replace_name)
                        item.set(
                            "href", item.get("href").replace(href_basename, replace_name)
                        )
                        item.set("media-type", replace_media_type)
                    logger.write(
                        f'{self.opf} 替换：<item id="{id}" href="{href}" media-type="{media_type}"/> -> <item id="{item.get("id")}" href="{item.get("href")}" media-type="{item.get("media-type")}"/>'
                    )
            modified_opf = ElementTree.tostring(
                root, encoding="utf-8", xml_declaration=True
            )
            self.target_epub.writestr(self.opf, modified_opf, zipfile.ZIP_DEFLATED)
        # logger.write(tree)

        for html_path in self.htmls:
            html_content = self.epub.open(html_path).read().decode("utf-8")

            def replace_match(match):
                original_src = match.group(2) + ".webp"
                img_basename = os.path.basename(original_src)
                if img_basename in self.img_dict:
                    new_name = self.img_dict[img_basename][0]  # 获取新文件名
                    new_src = original_src.replace(img_basename, new_name)
                    logger.write(f"{html_path} 替换: {original_src} -> {new_src}")
                    return match.group(0).replace(img_basename, new_name)
                else:
                    return match.group(0)

            # 使用正则表达式匹配 <img src="...webp">
            pattern = r'<img\b[^>]*?\bsrc\s*=\s*(["\'])(.*?)\.webp\1'
            pattern2 = r'<image\b[^>]*?\bxlink:href\s*=\s*(["\'])(.*?)\.webp\1'  # 处理SVG中的image标签
            updated_content = re.sub(pattern, lambda m: replace_match(m), html_content)
            updated_content = re.sub(
                pattern2, lambda m: replace_match(m), updated_content
            )
            self.target_epub.writestr(
                html_path, updated_content.encode("utf-8"), zipfile.ZIP_DEFLATED
            )

        for css_path in self.css:
            css_content = self.epub.open(css_path).read().decode("utf-8")

            def replace_match(match):
                quote = match.group(1) or ""  # 可能是 '', "'", 或 '"'
                path_with_webp = match.group(2) + ".webp"  # 比如 "../Images/cover.webp"
                img_basename = os.path.basename(path_with_webp)
                if img_basename in self.img_dict:
                    new_name = self.img_dict[img_basename][0]
                    new_path = path_with_webp.replace(img_basename, new_name)
                    logger.write(f"{css_path}替换 : {path_with_webp} -> {new_path}")
                    return f"url({quote}{new_path}{quote})"
                else:
                    return match.group(0)  # 不匹配就原样返回

            pattern = r'url\(\s*([\'"]?)\s*(.*?)\.webp\s*(?:\?\S*)?\s*\1\s*\)'
            updated_css = re.sub(
                pattern, replace_match, css_content, flags=re.IGNORECASE
            )
            self.target_epub.writestr(
                css_path, updated_css.encode("utf-8"), zipfile.ZIP_DEFLATED
            )
        logger.write(f"EPUB文件处理完成，输出文件路径: {self.file_write_path}")

    def close_files(self):
        if self.target_epub:
            self.target_epub.close()
        if self.epub:
            self.epub.close()

    def fail_del_target(self):
        if self.file_write_path and os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)
            logger.write(f"删除临时文件: {self.file_write_path}")
        else:
            logger.write("临时文件不存在或已被删除。")


def run_epub_img_transfer(epub_path, output_path):
    logger.write(f"\n正在尝试转换epub中webp格式图片: {epub_path}")
    it = ImageTransfer(epub_path, output_path)
    try:
        it.read_files()
        if len(it.img_dict.keys()) == 0:
            logger.write("没有找到需要转换的webp图片")
            it.close_files()
            it.fail_del_target()
            return "skip"
        it.replace()
        logger.write("EPUB文件处理成功")
        return 0
    except Exception as e:
        logger.write(f"处理EPUB文件时发生错误: {str(e)}")
        it.close_files()
        it.fail_del_target()
        return f"处理EPUB文件时发生错误: {str(e)}"


if __name__ == "__main__":
    it = ImageTransfer("test/demo.epub", "./test/")
    it.read_files()
    it.replace()
    it.close_files()
