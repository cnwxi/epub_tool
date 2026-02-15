import zipfile
import os
import posixpath
from bs4 import BeautifulSoup
from tinycss2 import parse_stylesheet, serialize, parse_declaration_list
# import emoji
import re
from fontTools.ttLib import TTFont
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from io import BytesIO
import random
import traceback
from datetime import datetime

try:
    from utils.log import logwriter
except:
    from log import logwriter

logger = logwriter()


def list_epub_font_encrypt_targets(epub_path):
    if not os.path.exists(epub_path):
        raise Exception("EPUB文件不存在")

    with zipfile.ZipFile(epub_path) as epub:
        html_files = sorted(
            [
                item
                for item in epub.namelist()
                if item.lower().endswith(".html") or item.lower().endswith(".xhtml")
            ],
            key=str.lower,
        )
        css_files = [item for item in epub.namelist() if item.lower().endswith(".css")]
        font_file_names = {
            os.path.basename(item).lower()
            for item in epub.namelist()
            if item.lower().endswith((".ttf", ".otf", ".woff"))
        }
        font_families = set()

        for css in css_files:
            try:
                content = epub.read(css).decode("utf-8")
                rules = parse_stylesheet(content)
            except Exception:
                continue

            for rule in rules:
                if rule.type != "at-rule" or rule.lower_at_keyword != "font-face":
                    continue
                declarations = parse_declaration_list(rule.content)
                font_family = None
                src_value = None
                for declaration in declarations:
                    if declaration.type != "declaration":
                        continue
                    if declaration.lower_name == "font-family":
                        values = [
                            token.value
                            for token in declaration.value
                            if token.type == "string" or token.type == "ident"
                        ]
                        if values:
                            font_family = " ".join(values).strip().strip("'\"")
                    elif declaration.lower_name == "src":
                        src_value = serialize(declaration.value)

                if not font_family or not src_value:
                    continue

                font_urls = re.findall(r"url\((.*?)\)", src_value, flags=re.IGNORECASE)
                for one_url in font_urls:
                    cleaned = (
                        one_url.strip().strip("'\"").split("#")[0].split("?")[0]
                    )
                    if os.path.basename(cleaned).lower() in font_file_names:
                        font_families.add(font_family)
                        break

    return {
        "font_families": sorted(font_families, key=str.lower),
        "xhtml_files": html_files,
    }


class FontEncrypt:

    def __init__(
        self,
        epub_path,
        output_path,
        target_font_families=None,
        target_xhtml_files=None,
    ):
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
            os.path.basename(self.epub_path).replace(".epub", "_font_encrypt.epub"),
        )
        if os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)
        self.htmls = []
        self.css = []
        self.fonts = []
        self.ori_files = []
        self.missing_chars = []
        self.font_to_font_family_mapping = {}
        self.css_selector_to_font_mapping = {}
        self.font_to_char_mapping = {}
        self.target_font_families = (
            {
                item.strip().strip("'\"").lower()
                for item in target_font_families
                if item and item.strip()
            }
            if target_font_families
            else None
        )
        self.target_xhtml_files = (
            {
                item.replace("\\", "/").strip().strip("'\"").lower()
                for item in target_xhtml_files
                if item and item.strip()
            }
            if target_xhtml_files
            else None
        )
        # self.font_to_unchanged_file_mapping = {}
        self.target_epub = None
        for file in self.epub.namelist():
            if file.lower().endswith(".html") or file.endswith(".xhtml"):
                self.htmls.append(file)
            elif file.lower().endswith(".css"):
                self.ori_files.append(file)
                self.css.append(file)
            elif file.lower().endswith((".ttf", ".otf", ".woff")):
                self.fonts.append(file)
            else:
                self.ori_files.append(file)

    def is_target_html(self, html_path):
        if not self.target_xhtml_files:
            return True
        normalized = html_path.replace("\\", "/").lower()
        basename = os.path.basename(normalized)
        return normalized in self.target_xhtml_files or basename in self.target_xhtml_files

    def normalize_font_name(self, name):
        return re.sub(r"\s+", " ", (name or "").strip().strip("'\"")).lower()

    def resolve_book_path(self, base_path, href):
        href = (href or "").strip().strip("'\"").split("#")[0].split("?")[0]
        if not href or "://" in href:
            return ""
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_path), href))

    def extract_font_candidates_from_declaration(self, declaration):
        if declaration.type != "declaration":
            return []

        candidates = []
        for token in declaration.value:
            if token.type == "string":
                value = token.value.strip()
                if value:
                    candidates.append(value)

        if declaration.lower_name == "font-family":
            raw = serialize(declaration.value)
            for part in raw.split(","):
                part = part.strip().strip("'\"")
                if part:
                    candidates.append(part)
        elif declaration.lower_name == "font":
            raw = serialize(declaration.value)
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            if parts:
                first = re.sub(r"^.*?(\d[^ ]*(\s*/\s*[^ ]+)?)\s+", "", parts[0]).strip()
                if first:
                    candidates.append(first.strip("'\""))
                for part in parts[1:]:
                    candidates.append(part.strip("'\""))

        generic = {
            "serif",
            "sans-serif",
            "monospace",
            "cursive",
            "fantasy",
            "system-ui",
            "emoji",
            "math",
            "fangsong",
            "inherit",
            "initial",
            "unset",
            "normal",
        }
        dedup = []
        seen = set()
        for item in candidates:
            normalized = self.normalize_font_name(item)
            if not normalized or normalized in generic or normalized in seen:
                continue
            seen.add(normalized)
            dedup.append(item)
        return dedup

    def build_font_name_to_file_mapping(self):
        mapping = {}
        for font in self.fonts:
            aliases = {self.normalize_font_name(os.path.splitext(os.path.basename(font))[0])}
            try:
                tt = TTFont(BytesIO(self.epub.read(font)))
                for record in tt["name"].names:
                    if record.nameID in (1, 4, 6):
                        try:
                            value = record.toUnicode()
                        except Exception:
                            value = record.string.decode(record.getEncoding(), errors="ignore")
                        normalized = self.normalize_font_name(value)
                        if normalized:
                            aliases.add(normalized)
            except Exception:
                pass
            for alias in aliases:
                if alias and alias not in mapping:
                    mapping[alias] = font
        return mapping

    def pick_font_file_by_candidates(self, candidates):
        for candidate in candidates:
            normalized = self.normalize_font_name(candidate)
            if self.target_font_families and normalized not in self.target_font_families:
                continue
            if normalized in self.font_to_font_family_mapping:
                return self.font_to_font_family_mapping[normalized]
        return None

    def parse_css_selector_mapping(self, css_text, source_path, mapping):
        rules = parse_stylesheet(css_text)
        for rule in rules:
            if rule.type != "qualified-rule":
                continue
            selector = serialize(rule.prelude).strip()
            if not selector:
                continue
            declarations = parse_declaration_list(rule.content)
            candidates = []
            for declaration in declarations:
                if declaration.type != "declaration":
                    continue
                if declaration.lower_name in ("font-family", "font"):
                    candidates.extend(
                        self.extract_font_candidates_from_declaration(declaration)
                    )
            font_file = self.pick_font_file_by_candidates(candidates)
            if not font_file:
                continue
            for one_selector in selector.split(","):
                one_selector = one_selector.strip()
                if one_selector:
                    mapping[one_selector] = font_file

    def create_target_epub(self):
        self.target_epub = zipfile.ZipFile(
            self.file_write_path,
            "w",
            zipfile.ZIP_STORED,
            zipfile.ZIP_STORED,
        )

    def find_local_fonts_mapping(self):
        mapping = self.build_font_name_to_file_mapping()
        for css in self.css:
            with self.epub.open(css) as f:
                content = f.read().decode("utf-8")
                rules = parse_stylesheet(content)
                for rule in rules:
                    if rule.type != "at-rule" or rule.lower_at_keyword != "font-face":
                        continue
                    declarations = parse_declaration_list(rule.content)
                    font_family = None
                    src_urls = []
                    for declaration in declarations:
                        if declaration.type != "declaration":
                            continue
                        if declaration.lower_name == "font-family":
                            candidates = self.extract_font_candidates_from_declaration(
                                declaration
                            )
                            if candidates:
                                font_family = candidates[0]
                        elif declaration.lower_name == "src":
                            src_text = serialize(declaration.value)
                            src_urls.extend(
                                re.findall(r"url\((.*?)\)", src_text, flags=re.IGNORECASE)
                            )
                    if not font_family:
                        continue
                    normalized = self.normalize_font_name(font_family)
                    for one_url in src_urls:
                        font_path = self.resolve_book_path(css, one_url)
                        if font_path in self.fonts:
                            mapping[normalized] = font_path
                            break
        self.font_to_font_family_mapping = mapping

    def find_selector_to_font_mapping(self):
        mapping = {}
        for css in self.css:
            with self.epub.open(css) as f:
                content = f.read().decode("utf-8")
            self.parse_css_selector_mapping(content, css, mapping)

        for one_html in self.htmls:
            if not self.is_target_html(one_html):
                continue
            with self.epub.open(one_html) as f:
                html_content = f.read().decode("utf-8")
            soup = BeautifulSoup(html_content, "html.parser")
            for style_tag in soup.find_all("style"):
                css_text = style_tag.get_text() or ""
                if css_text.strip():
                    self.parse_css_selector_mapping(css_text, one_html, mapping)

        self.css_selector_to_font_mapping = dict(
            sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True)
        )

    def remove_duplicates(self, s):
        seen = set()
        result = []
        for char in s:
            if char not in seen:
                seen.add(char)
                result.append(char)
        return "".join(result)

    def decode_hex_entity(self, value):
        match = re.fullmatch(r"&#x([0-9a-fA-F]+)", value or "")
        if not match:
            return value
        codepoint = int(match.group(1), 16)
        if 0 <= codepoint <= 0x10FFFF:
            return chr(codepoint)
        return value

    def find_char_mapping(self):
        mapping = {}
        for one_html in self.htmls:
            if not self.is_target_html(one_html):
                continue
            with self.epub.open(one_html) as f:
                content = f.read().decode("utf-8")
                soup = BeautifulSoup(content, "html.parser")
                for (
                    css_selector,
                    font_file,
                ) in self.css_selector_to_font_mapping.items():
                    # 使用 CSS 选择器查找对应的标签
                    try:
                        elements = soup.select(css_selector)
                    except Exception:
                        continue

                    # 提取每个标签的文字内容
                    text_contents = [
                        element.get_text(strip=True) for element in elements
                    ]
                    combined_sentence = "".join(text_contents)
                    if font_file not in mapping:
                        mapping[font_file] = self.remove_duplicates(combined_sentence)
                    else:
                        mapping[font_file] = self.remove_duplicates(
                            "".join([mapping[font_file], combined_sentence])
                        )
                for tag in soup.find_all(style=True):
                    declarations = parse_declaration_list(tag.get("style", ""))
                    candidates = []
                    for declaration in declarations:
                        if (
                            declaration.type == "declaration"
                            and declaration.lower_name in ("font-family", "font")
                        ):
                            candidates.extend(
                                self.extract_font_candidates_from_declaration(declaration)
                            )
                    font_file = self.pick_font_file_by_candidates(candidates)
                    if not font_file:
                        continue
                    text = tag.get_text(strip=True)
                    if font_file not in mapping:
                        mapping[font_file] = self.remove_duplicates(text)
                    else:
                        mapping[font_file] = self.remove_duplicates(
                            "".join([mapping[font_file], text])
                        )
        self.font_to_char_mapping = mapping

    def get_mapping(self):
        self.find_local_fonts_mapping()
        self.find_selector_to_font_mapping()
        self.find_char_mapping()
        logger.write(f"字体文件映射: {self.font_to_font_family_mapping}")
        logger.write(f"CSS选择器映射: {self.css_selector_to_font_mapping}")
        logger.write(f"字体文件到字符映射: {self.font_to_char_mapping}")
        return (
            self.font_to_font_family_mapping,
            self.css_selector_to_font_mapping,
            self.font_to_char_mapping,
        )

    def clean_text(self):
        for key in self.font_to_char_mapping:
            text = self.font_to_char_mapping[key]
            # 去除转义字符和换行符
            # self.font_to_char_mapping[key] = (
            #     text.replace("\n", "").replace("\r", "").replace("\t", "")
            # )
            # 去除标点符号和特殊字符
            self.font_to_char_mapping[key] = re.sub(r"[^\u4e00-\u9fa5]", "", text)
            # self.font_to_char_mapping[key] = emoji.replace_emoji(text, replace="")
        logger.write(f"清理后的文本: {self.font_to_char_mapping}")

    # 修改自https://github.com/solarhell/fontObfuscator
    def ensure_cmap_has_all_text(self, cmap: dict, s: str) -> bool:
        missing_chars = []
        exsit_chars = []
        for char in s:
            if ord(char) not in cmap:
                # raise Exception(f'字库缺少{char}这个字 {ord(char)}')
                missing_chars.append(char)
            else:
                exsit_chars.append(char)
        return missing_chars, "".join(exsit_chars)

    def set_timestamps(self, font):
        # 设置 'head' 表的时间戳
        head_table = font["head"]
        current_time = int(datetime.now().timestamp())
        # print(f"原始时间戳: {head_table.created}, {head_table.modified}")
        created_datetime = datetime.fromtimestamp(head_table.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        modified_datetime = datetime.fromtimestamp(head_table.modified).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        logger.write(f"原始时间戳: {created_datetime}, {modified_datetime}")
        # print(f"转换UTC时间，: {created_datetime}")
        # print(f"转换UTC时间，: {modified_datetime}")
        head_table.created = current_time
        head_table.modified = current_time
        logger.write(
            f"转换后时间戳 {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    # 修改自https://github.com/solarhell/fontObfuscator
    def encrypt_font(self):
        self.create_target_epub()
        for i, (font_path, plain_text) in enumerate(self.font_to_char_mapping.items()):
            original_font = TTFont(BytesIO(self.epub.read(font_path)))
            name_table = original_font["name"]
            family_name = None
            style_name = None
            for record in name_table.names:
                if record.nameID == 1:
                    family_name = record.string.decode(record.getEncoding())
                elif record.nameID == 2:
                    style_name = record.string.decode(record.getEncoding())

                if family_name and style_name:
                    break
            if family_name is None:
                family_name = f"ETFamily_{i}"
            if style_name is None:
                style_name = "Regular"

            NAME_STRING = {
                "familyName": family_name,
                "styleName": style_name,
                "psName": family_name + "-" + style_name,
                "copyright": "Created by EpubTool",
                "version": "Version 1.0",
                "vendorURL": "https://EpubTool.com/",
            }
            original_cmap: dict = original_font.getBestCmap()
            miss_char, plain_text = self.ensure_cmap_has_all_text(
                original_cmap, plain_text
            )
            if len(miss_char) > 0:
                logger.write(f"字体文件{font_path}缺少字符{miss_char}")
            available_ranges = [ord(char) for char in plain_text]
            glyphs, metrics, cmap = {}, {}, {}
            private_codes = random.sample(range(0xAC00, 0xD7AF), len(plain_text))
            cjk_codes = random.sample(available_ranges, len(plain_text))

            glyph_set = original_font.getGlyphSet()
            pen = TTGlyphPen(glyph_set)
            glyph_order = original_font.getGlyphOrder()
            final_shadow_text: list = []
            spescial_glyphs = [
                "null",
                ".notdef",
                "minus",
                "dotlessi",
                "uni0307",
                "quotesingle",
                "zero.dnom",
                "fraction",
                "uni0237",
            ]

            for special_glyph in spescial_glyphs:
                if special_glyph in glyph_order:
                    glyph_set[special_glyph].draw(pen)
                    glyphs[special_glyph] = pen.glyph()
                    metrics[special_glyph] = original_font["hmtx"][special_glyph]
                    final_shadow_text += [special_glyph]

            html_entities = []

            for index, plain in enumerate(plain_text):
                try:
                    shadow_cmap_name = original_cmap[cjk_codes[index]]
                except KeyError:
                    logger.write(
                        f"字体文件缺少字符，unicode:{cjk_codes[index]}，请检查"
                    )

                final_shadow_text += [shadow_cmap_name]
                glyph_set[original_cmap[ord(plain)]].draw(pen)
                glyphs[shadow_cmap_name] = pen.glyph()
                metrics[shadow_cmap_name] = original_font["hmtx"][
                    original_cmap[ord(plain)]
                ]
                cmap[private_codes[index]] = shadow_cmap_name
                html_entities += [hex(private_codes[index]).replace("0x", "&#x")]

            horizontal_header = {
                "ascent": original_font["hhea"].ascent,
                "descent": original_font["hhea"].descent,
            }
            missing_glyphs = [
                glyph for glyph in final_shadow_text if glyph not in glyphs
            ]
            if missing_glyphs:
                logger.write(f"以下字形在 glyphs 中缺失: {missing_glyphs}")
                for glyph in missing_glyphs:
                    glyphs[glyph] = pen.glyph()
                    metrics[glyph] = (0, 0)

            glyf_table = original_font["glyf"]
            glyphs_to_keep = set(glyphs.keys())
            new_glyph_order = [
                glyph for glyph in glyph_order if glyph in glyphs_to_keep
            ]
            original_font.setGlyphOrder(new_glyph_order)

            # 删除不必要的字形
            for glyph in glyph_order:
                if glyph not in glyphs_to_keep:
                    if glyph in glyf_table.glyphs:
                        del glyf_table.glyphs[glyph]
                    if glyph in original_font["hmtx"].metrics:
                        del original_font["hmtx"].metrics[glyph]
                    loca_index = glyph_order.index(glyph)
                    if 0 <= loca_index < len(original_font["loca"].locations):
                        original_font["loca"].locations[loca_index] = 0

            # 更新 maxp 表
            original_font["maxp"].numGlyphs = len(new_glyph_order)

            self.set_timestamps(original_font)

            fb = FontBuilder(original_font["head"].unitsPerEm, isTTF=True)
            fb.setupGlyphOrder(new_glyph_order)
            fb.setupCharacterMap(cmap)
            fb.setupGlyf(glyphs)
            fb.setupHorizontalMetrics(metrics)
            fb.setupHorizontalHeader(**horizontal_header)
            fb.setupNameTable(NAME_STRING)
            fb.setupOS2()
            fb.setupPost()
            font_stream = BytesIO()
            fb.save(font_stream)
            # print(plain_text, html_entities)
            # print(f"write {font_path}")

            self.target_epub.writestr(
                font_path, font_stream.getvalue(), zipfile.ZIP_DEFLATED
            )
            text_list = list(plain_text)
            replace_table = {}
            for a0, a1 in zip(text_list, html_entities):
                replace_table[a0] = a1
            self.font_to_char_mapping[font_path] = replace_table
            logger.write(f"字体文件{font_path}的加密映射: \n{replace_table}")

    def close_file(self):
        self.epub.close()
        self.target_epub.close()

    def fail_del_target(self):
        if self.file_write_path and os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)
            logger.write(f"删除临时文件: {self.file_write_path}")
        else:
            logger.write("临时文件不存在或已被删除。")

    def read_html(self):
        for one_html in self.htmls:
            with self.epub.open(one_html) as f:
                content = f.read().decode("utf-8")
            soup = BeautifulSoup(content, "html.parser")

            if not self.is_target_html(one_html):
                self.target_epub.writestr(
                    one_html, content.encode("utf-8"), zipfile.ZIP_DEFLATED
                )
                continue

            for css_selector in self.css_selector_to_font_mapping.keys():
                font_file = self.css_selector_to_font_mapping[css_selector]
                replace_table = self.font_to_char_mapping.get(font_file, {})
                if not replace_table:
                    continue
                char_replace_table = {
                    source: self.decode_hex_entity(target)
                    for source, target in replace_table.items()
                }
                trans_table = str.maketrans(char_replace_table)
                try:
                    selector_tags = soup.select(css_selector)
                except Exception:
                    continue
                for tag in selector_tags:
                    for text_node in list(tag.find_all(string=True)):
                        text_node.replace_with(text_node.translate(trans_table))
            for tag in soup.find_all(style=True):
                declarations = parse_declaration_list(tag.get("style", ""))
                candidates = []
                for declaration in declarations:
                    if (
                        declaration.type == "declaration"
                        and declaration.lower_name in ("font-family", "font")
                    ):
                        candidates.extend(
                            self.extract_font_candidates_from_declaration(declaration)
                        )
                font_file = self.pick_font_file_by_candidates(candidates)
                if not font_file:
                    continue
                replace_table = self.font_to_char_mapping.get(font_file, {})
                if not replace_table:
                    continue
                char_replace_table = {
                    source: self.decode_hex_entity(target)
                    for source, target in replace_table.items()
                }
                trans_table = str.maketrans(char_replace_table)
                for text_node in list(tag.find_all(string=True)):
                    text_node.replace_with(text_node.translate(trans_table))
            # formatter="html" 会把部分标点转成 &hellip; / &mdash; 等实体，
            # 这里保持字符本身，避免正文被意外替换。
            formatted_html = soup.decode(formatter=None)
            self.target_epub.writestr(
                one_html, formatted_html.encode("utf-8"), zipfile.ZIP_DEFLATED
            )
        # 保留未参与混淆的字体文件，避免被遗漏导致阅读器缺字
        untouched_fonts = [
            font_file
            for font_file in self.fonts
            if font_file not in self.font_to_char_mapping
        ]
        for font_file in untouched_fonts:
            with self.epub.open(font_file) as f:
                content = f.read()
            self.target_epub.writestr(font_file, content, zipfile.ZIP_DEFLATED)
        for item in self.ori_files:
            if item in self.epub.namelist():
                with self.epub.open(item) as f:
                    content = f.read()
                self.target_epub.writestr(item, content, zipfile.ZIP_DEFLATED)
        self.close_file()
        logger.write(f"EPUB文件处理完成，输出文件路径: {self.file_write_path}")

    # def read_unchanged_fonts(self,font_file_mapping=None):
    #    self.font_to_unchanged_file_mapping = font_file_mapping if font_file_mapping else {}


def run_epub_font_encrypt(
    epub_path,
    output_path=None,
    target_font_families=None,
    target_xhtml_files=None,
):
    logger.write(f"\n正在尝试加密EPUB字体: {epub_path}")
    fe = FontEncrypt(
        epub_path,
        output_path,
        target_font_families=target_font_families,
        target_xhtml_files=target_xhtml_files,
    )
    if len(fe.fonts) == 0:
        logger.write("没有找到字体文件，退出")
        return "skip"
    logger.write(f"此EPUB文件包含{len(fe.fonts)}个字体文件: {', '.join(fe.fonts)}")
    fe.get_mapping()
    fe.clean_text()
    try:
        fe.encrypt_font()
        logger.write("字体加密成功")
    except Exception as e:
        logger.write(f"字体加密失败，错误信息: {e}")
        traceback.print_exc()
        fe.close_file()
        fe.fail_del_target()
        return e
    try:
        fe.read_html()
        logger.write("EPUB文件处理成功")
        fe.close_file()
    except Exception as e:
        logger.write(f"EPUB文件处理失败，错误信息: {e}")
        fe.close_file()
        fe.fail_del_target()
        return e
    return 0


if __name__ == "__main__":
    epub_read_path = input("1、请输入EPUB文件路径（如: ./test.epub）: ")

    file_write_dir = input("2、请输入输出文件夹路径（如: ./dist）: ")

    # epub_read_path= './crazy.epub'
    # file_write_dir = './dist'

    fe = FontEncrypt(epub_read_path, file_write_dir)
    fe.get_mapping()
    # the_font_file_mapping = {}
    print(f"3、此EPUB文件包含{len(fe.fonts)}个字体文件:")
    print("\n".join(fe.fonts))
    fe.clean_text()
    try:
        fe.encrypt_font()
        print("4、字体加密成功")
    except Exception as e:
        print(f"4、字体加密失败，错误信息: {e}")
        traceback.print_exc()
        fe.close_file()
        exit(1)
    try:
        fe.read_html()
        print("5、EPUB文件处理成功")
    except Exception as e:
        print(f"5、EPUB文件处理失败，错误信息: {e}")
        fe.close_file()
        exit(1)
