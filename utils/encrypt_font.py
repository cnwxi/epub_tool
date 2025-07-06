import zipfile
import os
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
import html
from datetime import datetime

try:
    from utils.log import logwriter
except:
    from log import logwriter

logger = logwriter()


class FontEncrypt:

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

    def create_target_epub(self):
        self.target_epub = zipfile.ZipFile(
            self.file_write_path,
            "w",
            zipfile.ZIP_STORED,
            zipfile.ZIP_STORED,
        )

    def find_local_fonts_mapping(self):
        font_face_rules = []
        for css in self.css:
            with self.epub.open(css) as f:
                content = f.read().decode("utf-8")
                rules = parse_stylesheet(content)
                # 遍历所有规则，查找 @font-face
                for rule in rules:
                    all_count = 0
                    if rule.type == "at-rule" and rule.lower_at_keyword == "font-face":
                        tmp_font_face = serialize(rule.content)

                        local_count, url_count = tmp_font_face.count(
                            "local"
                        ), tmp_font_face.count("url")
                        all_count += local_count + url_count
                        if all_count == 1:
                            tmp_list = []

                            for item in tmp_font_face.split(";"):
                                if len(item.strip()) > 0:
                                    tmp_list.append(item.strip())
                            font_face_rules.append(tmp_list)
        mapping = {}
        for font in self.fonts:
            font_name = os.path.basename(font)
            for j in font_face_rules:
                if font_name in j[1]:
                    font_family = (
                        j[0].split(":")[1].strip().replace('"', "").replace("'", "")
                    )
                    mapping[font_family] = font
        self.font_to_font_family_mapping = mapping

    def find_selector_to_font_mapping(self):
        mapping = {}
        for css in self.css:
            with self.epub.open(css) as f:
                content = f.read().decode("utf-8")
                rules = parse_stylesheet(content)
                for rule in rules:
                    if rule.type == "qualified-rule":  # 确保是样式规则
                        # 获取选择器
                        selector = serialize(rule.prelude).strip()
                        declarations = parse_declaration_list(rule.content)
                        for declaration in declarations:
                            if (
                                declaration.type == "declaration"
                                and declaration.lower_name == "font-family"
                            ):
                                # 提取 font-family 的值
                                font_family_values = [
                                    token.value
                                    for token in declaration.value
                                    if token.type == "string" or token.type == "ident"
                                ]

                                # 提取第一个字体名称
                                primary_font = font_family_values[0].strip("'\"")

                                # 如果该字体在映射中
                                if primary_font in self.font_to_font_family_mapping:
                                    # print(
                                    #     f"选择器 '{selector}' 使用了字体文件: {self.font_to_font_family_mapping[primary_font]}"
                                    # )
                                    if primary_font not in mapping:
                                        mapping[selector] = (
                                            self.font_to_font_family_mapping[
                                                primary_font
                                            ]
                                        )
        self.css_selector_to_font_mapping = dict(sorted(mapping.items(), reverse=True))

    def remove_duplicates(self, s):
        seen = set()
        result = []
        for char in s:
            if char not in seen:
                seen.add(char)
                result.append(char)
        return "".join(result)

    def find_char_mapping(self):
        mapping = {}
        for one_html in self.htmls:
            with self.epub.open(one_html) as f:
                content = f.read().decode("utf-8")
                soup = BeautifulSoup(content, "html.parser")
                for (
                    css_selector,
                    font_file,
                ) in self.css_selector_to_font_mapping.items():
                    # 使用 CSS 选择器查找对应的标签
                    elements = soup.select(css_selector)

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

            for css_selector in self.css_selector_to_font_mapping.keys():
                font_file = self.css_selector_to_font_mapping[css_selector]
                replace_table = self.font_to_char_mapping[font_file]
                trans_table = str.maketrans(replace_table)
                if "." in css_selector:
                    selector, selector_class = css_selector.split(".", 1)
                    selector_tags = soup.find_all(selector, class_=selector_class)
                else:
                    selector, selector_class = css_selector, None
                    # print(selector, selector_class)
                    selector_tags = soup.find_all(selector)
                for tag in selector_tags:
                    ori_text = "".join(str(item) for item in tag.contents)
                    new_text = ori_text.translate(trans_table)
                    parsed_new_text = BeautifulSoup(
                        html.unescape(new_text), "html.parser"
                    )
                    # print(f"ori_text:{ori_text}\nnew_text:{new_text}")
                    tag.clear()  # 清空当前标签内容
                    tag.append(parsed_new_text)  # 插入新的内容
                    # print(tag.get_text(strip=True))
            formatted_html = soup.prettify(formatter="html")
            self.target_epub.writestr(
                one_html, formatted_html.encode("utf-8"), zipfile.ZIP_DEFLATED
            )
        for item in self.ori_files:
            if item in self.epub.namelist():
                with self.epub.open(item) as f:
                    content = f.read()
                self.target_epub.writestr(item, content, zipfile.ZIP_DEFLATED)
        self.close_file()
        logger.write(f"EPUB文件处理完成，输出文件路径: {self.file_write_path}")

    # def read_unchanged_fonts(self,font_file_mapping=None):
    #    self.font_to_unchanged_file_mapping = font_file_mapping if font_file_mapping else {}


def run_epub_font_encrypt(epub_path, output_path=None):
    logger.write(f"\n正在尝试加密EPUB字体: {epub_path}")
    fe = FontEncrypt(epub_path, output_path)
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
