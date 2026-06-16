import zipfile
import os
import posixpath
import codecs
from bs4 import BeautifulSoup, Comment, NavigableString
from tinycss2 import parse_stylesheet, serialize, parse_declaration_list
# import emoji
import re
from xml.etree import ElementTree
from fontTools.ttLib import TTFont
from io import BytesIO
import random
import traceback
from datetime import datetime
import unicodedata
import uuid

try:
    from utils.log import logwriter
except:
    from log import logwriter

logger = logwriter()

FONT_OBFUSCATION_EAST_ASIAN_WIDTHS = frozenset({"W", "F"})
FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS = tuple(
    ord(char) for char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS = tuple(
    list(range(ord("０"), ord("９") + 1))
    + list(range(ord("Ａ"), ord("Ｚ") + 1))
    + list(range(ord("ａ"), ord("ｚ") + 1))
)
FONT_OBFUSCATION_HANGUL_RANGE = (0xAC00, 0xD7AF)
FONT_OBFUSCATION_LAYOUT_CODEPOINTS = tuple(
    codepoint
    for codepoint in range(
        FONT_OBFUSCATION_HANGUL_RANGE[0],
        FONT_OBFUSCATION_HANGUL_RANGE[1] + 1,
    )
    if unicodedata.category(chr(codepoint)).startswith(("L", "N"))
    and unicodedata.east_asian_width(chr(codepoint))
    in FONT_OBFUSCATION_EAST_ASIAN_WIDTHS
)


def is_ascii_latin_alnum(char):
    return (
        "0" <= char <= "9"
        or "A" <= char <= "Z"
        or "a" <= char <= "z"
    )


def is_fullwidth_latin_alnum(char):
    return (
        "０" <= char <= "９"
        or "Ａ" <= char <= "Ｚ"
        or "ａ" <= char <= "ｚ"
    )


def list_epub_font_encrypt_targets(epub_path):
    if not os.path.exists(epub_path):
        raise Exception("EPUB文件不存在")

    with zipfile.ZipFile(epub_path) as epub:
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

    return {"font_families": sorted(font_families, key=str.lower)}


class FontEncrypt:

    def __init__(
        self,
        epub_path,
        output_path,
        target_font_families=None,
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
        self.css_selector_font_rules = []
        self._css_selector_rule_order = 0
        self.font_to_char_mapping = {}
        self.font_to_passthrough_char_mapping = {}
        self.target_font_families = (
            {
                item.strip().strip("'\"").lower()
                for item in target_font_families
                if item and item.strip()
            }
            if target_font_families
            else None
        )
        if self.target_font_families:
            logger.write("本次目标字体 family 列表:")
            for font_family in sorted(self.target_font_families):
                logger.write(f" - {font_family}")
        else:
            logger.write("未指定目标字体 family，将按规则处理全部可匹配字体")
        # self.font_to_unchanged_file_mapping = {}
        self.target_epub = None
        self.opf_path = None
        self._init_opf_path()
        self._validate_opf_safely()
        for file in self.epub.namelist():
            if file.lower().endswith(".html") or file.lower().endswith(".xhtml"):
                self.htmls.append(file)
            elif file.lower().endswith(".css"):
                self.ori_files.append(file)
                self.css.append(file)
            elif file.lower().endswith((".ttf", ".otf", ".woff")):
                self.fonts.append(file)
            else:
                self.ori_files.append(file)

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
                self.opf_path = rf.group(2)
                return
        except Exception as e:
            logger.write(f"读取 container.xml 失败，将回退扫描opf: {e}")
        for file in self.epub.namelist():
            if file.lower().endswith(".opf"):
                self.opf_path = file
                return

    def _validate_opf_safely(self):
        if not self.opf_path:
            return
        try:
            opf_text = self._read_xml_text(self.opf_path)
            self._parse_xml_safe(opf_text, label=f"OPF:{self.opf_path}")
        except Exception as e:
            logger.write(f"opf_malformed_fallback_used: encrypt_font ({e})")

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

    def resolve_font_candidate(self, candidates):
        for candidate in candidates:
            normalized = self.normalize_font_name(candidate)
            if normalized in self.font_to_font_family_mapping:
                return self.font_to_font_family_mapping[normalized], normalized
        return None, None

    def is_target_font_file(self, font_file, matched_family=None):
        if not self.target_font_families:
            return True
        if matched_family and matched_family in self.target_font_families:
            return True
        return any(
            family_name in self.target_font_families and mapped_file == font_file
            for family_name, mapped_file in self.font_to_font_family_mapping.items()
        )

    def pick_font_file_by_candidates(self, candidates):
        font_file, matched_family = self.resolve_font_candidate(candidates)
        if font_file and self.is_target_font_file(font_file, matched_family):
            return font_file
        return None

    def calculate_selector_specificity(self, selector):
        cleaned = re.sub(r"(['\"]).*?\1", "", selector or "")
        id_count = len(re.findall(r"#[\w-]+", cleaned))
        class_count = len(re.findall(r"\.[\w-]+", cleaned))
        class_count += len(re.findall(r"\[[^\]]+\]", cleaned))
        class_count += len(re.findall(r":(?!:)[\w-]+(?:\([^)]*\))?", cleaned))
        type_text = re.sub(
            r"#[\w-]+|\.[\w-]+|\[[^\]]+\]|:{1,2}[\w-]+(?:\([^)]*\))?",
            " ",
            cleaned,
        )
        type_count = sum(
            1
            for token in re.split(r"[\s>+~]+", type_text)
            if token and token != "*" and re.match(r"^[a-zA-Z][\w-]*$", token)
        )
        return id_count, class_count, type_count

    def record_css_selector_font_rule(
        self,
        selector,
        font_file,
        mapping,
        order,
        matched_family=None,
    ):
        if self.is_target_font_file(font_file, matched_family):
            mapping[selector] = font_file
        self.css_selector_font_rules.append(
            {
                "selector": selector,
                "font_file": font_file,
                "specificity": self.calculate_selector_specificity(selector),
                "order": order,
            }
        )

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
            font_file, matched_family = self.resolve_font_candidate(candidates)
            if not font_file:
                continue
            self._css_selector_rule_order += 1
            rule_order = self._css_selector_rule_order
            for one_selector in selector.split(","):
                one_selector = one_selector.strip()
                if one_selector:
                    self.record_css_selector_font_rule(
                        one_selector,
                        font_file,
                        mapping,
                        rule_order,
                        matched_family=matched_family,
                    )

    def create_target_epub(self):
        self.target_epub = zipfile.ZipFile(
            self.file_write_path,
            "w",
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
        self.css_selector_font_rules = []
        self._css_selector_rule_order = 0
        for css in self.css:
            with self.epub.open(css) as f:
                content = f.read().decode("utf-8")
            self.parse_css_selector_mapping(content, css, mapping)

        for one_html in self.htmls:
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


    def protect_escaped_angle_entities(self, html_text):
        placeholder_map = {}

        def create_unique_placeholder(index):
            while True:
                placeholder = (
                    f"__EPUB_TOOL_ESCAPED_ANGLE_{uuid.uuid4().hex}_{index}__"
                )
                if placeholder not in html_text and placeholder not in placeholder_map:
                    return placeholder

        def replace_entity(match):
            entity = match.group(0)
            placeholder = create_unique_placeholder(len(placeholder_map))
            placeholder_map[placeholder] = entity
            return placeholder

        protected_html = re.sub(r"&lt;|&gt;", replace_entity, html_text)
        return protected_html, placeholder_map

    def restore_escaped_angle_entities(self, html_text, placeholder_map):
        if not placeholder_map:
            return html_text
        restored = html_text
        for placeholder, entity in placeholder_map.items():
            restored = restored.replace(placeholder, entity)
        return restored

    def iter_direct_text_nodes(self, tag):
        for child in tag.children:
            if not isinstance(child, NavigableString):
                continue
            if isinstance(child, Comment):
                continue
            if not child.strip():
                continue
            yield child

    def add_text_to_font_mapping(self, mapping, font_file, text):
        if not font_file or not text:
            return
        if font_file not in mapping:
            mapping[font_file] = self.remove_duplicates(text)
            return
        mapping[font_file] = self.remove_duplicates("".join([mapping[font_file], text]))

    def pick_inline_font_file(self, tag):
        if not tag or not tag.has_attr("style"):
            return None
        declarations = parse_declaration_list(tag.get("style", ""))
        candidates = []
        for declaration in declarations:
            if (
                declaration.type == "declaration"
                and declaration.lower_name in ("font-family", "font")
            ):
                candidates.extend(self.extract_font_candidates_from_declaration(declaration))
        font_file, _ = self.resolve_font_candidate(candidates)
        return font_file

    def build_css_font_rule_index(self, soup):
        index = {}
        rules = getattr(self, "css_selector_font_rules", [])
        if not rules:
            rules = [
                {
                    "selector": selector,
                    "font_file": font_file,
                    "specificity": self.calculate_selector_specificity(selector),
                    "order": order,
                }
                for order, (selector, font_file) in enumerate(
                    getattr(self, "css_selector_to_font_mapping", {}).items(),
                    1,
                )
            ]
        for rule in rules:
            try:
                elements = soup.select(rule["selector"])
            except Exception:
                continue
            precedence = (rule["specificity"], rule["order"])
            for element in elements:
                current = index.get(id(element))
                if current is None or precedence > current["precedence"]:
                    index[id(element)] = {
                        "font_file": rule["font_file"],
                        "precedence": precedence,
                    }
        return index

    def get_effective_font_file(self, tag, css_font_rule_index):
        current = tag
        while current is not None and getattr(current, "name", None):
            inline_font_file = self.pick_inline_font_file(current)
            if inline_font_file:
                return inline_font_file
            rule_record = css_font_rule_index.get(id(current))
            if rule_record:
                return rule_record["font_file"]
            current = current.parent
        return None

    def find_char_mapping(self):
        mapping = {}
        for one_html in self.htmls:
            with self.epub.open(one_html) as f:
                content = f.read().decode("utf-8")
                soup = BeautifulSoup(content, "html.parser")
                css_font_rule_index = self.build_css_font_rule_index(soup)
                for tag in soup.find_all(True):
                    font_file = self.get_effective_font_file(tag, css_font_rule_index)
                    if not font_file or not self.is_target_font_file(font_file):
                        continue
                    text = "".join(
                        text_node.strip()
                        for text_node in self.iter_direct_text_nodes(tag)
                    )
                    self.add_text_to_font_mapping(mapping, font_file, text)
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

    def should_obfuscate_char(self, char):
        if is_ascii_latin_alnum(char) or is_fullwidth_latin_alnum(char):
            return True
        category = unicodedata.category(char)
        east_asian_width = unicodedata.east_asian_width(char)
        return (
            category.startswith(("L", "N"))
            and east_asian_width in FONT_OBFUSCATION_EAST_ASIAN_WIDTHS
        )

    def get_obfuscation_codepoint_pool(self, char):
        if is_ascii_latin_alnum(char):
            return FONT_OBFUSCATION_ASCII_ALNUM_CODEPOINTS
        if is_fullwidth_latin_alnum(char):
            return FONT_OBFUSCATION_FULLWIDTH_ALNUM_CODEPOINTS
        return FONT_OBFUSCATION_LAYOUT_CODEPOINTS

    def sample_obfuscation_codepoints(self, count, excluded_codepoints=None):
        excluded_codepoints = set(excluded_codepoints or set())
        candidates = [
            codepoint
            for codepoint in FONT_OBFUSCATION_LAYOUT_CODEPOINTS
            if codepoint not in excluded_codepoints
        ]
        if count > len(candidates):
            raise ValueError(
                "可用布局安全混淆码位不足，"
                f"需要 {count} 个，最多 {len(candidates)} 个"
            )
        return random.sample(candidates, count)

    def sample_codepoints_from_pool(
        self,
        source_codepoints,
        candidate_pool,
        excluded_codepoints=None,
    ):
        source_codepoints = list(source_codepoints)
        excluded_codepoints = set(excluded_codepoints or set())
        candidates = [
            codepoint
            for codepoint in candidate_pool
            if codepoint not in excluded_codepoints
        ]
        if len(source_codepoints) > len(candidates):
            raise ValueError(
                "可用同类混淆码位不足，"
                f"需要 {len(source_codepoints)} 个，最多 {len(candidates)} 个"
            )

        targets = []
        used = set()
        shuffled_candidates = random.sample(candidates, len(candidates))
        for source_codepoint in source_codepoints:
            available = [
                codepoint
                for codepoint in shuffled_candidates
                if codepoint not in used and codepoint != source_codepoint
            ]
            if not available:
                available = [
                    codepoint
                    for codepoint in shuffled_candidates
                    if codepoint not in used
                ]
            if not available:
                raise ValueError("可用同类混淆码位不足")
            target_codepoint = available[0]
            targets.append(target_codepoint)
            used.add(target_codepoint)

        for index, source_codepoint in enumerate(source_codepoints):
            if targets[index] != source_codepoint:
                continue
            for swap_index, swap_target in enumerate(targets):
                if index == swap_index:
                    continue
                if (
                    swap_target != source_codepoint
                    and targets[index] != source_codepoints[swap_index]
                ):
                    targets[index], targets[swap_index] = (
                        targets[swap_index],
                        targets[index],
                    )
                    break
        return targets

    def build_obfuscation_codepoint_mapping(self, plain_text, preserved_text):
        preserved_codepoints = {ord(char) for char in preserved_text}
        grouped_chars = {}
        for char in plain_text:
            pool = self.get_obfuscation_codepoint_pool(char)
            grouped_chars.setdefault(pool, []).append(char)

        codepoint_mapping = {}
        for pool, chars in grouped_chars.items():
            source_codepoints = [ord(char) for char in chars]
            target_codepoints = self.sample_codepoints_from_pool(
                source_codepoints,
                pool,
                excluded_codepoints=preserved_codepoints,
            )
            codepoint_mapping.update(zip(source_codepoints, target_codepoints))
        return codepoint_mapping

    def clean_text(self):
        passthrough_mapping = {}
        for key in self.font_to_char_mapping:
            text = self.font_to_char_mapping[key]
            # 标点、符号、空格等字符保持原码位，避免破坏阅读器的中英文换行与标点避头规则。
            obfuscation_chars = []
            passthrough_chars = []
            for char in text:
                category = unicodedata.category(char)
                if category.startswith("C"):
                    continue
                if self.should_obfuscate_char(char):
                    obfuscation_chars.append(char)
                else:
                    passthrough_chars.append(char)
            self.font_to_char_mapping[key] = self.remove_duplicates("".join(obfuscation_chars))
            passthrough_mapping[key] = self.remove_duplicates("".join(passthrough_chars))
        self.font_to_passthrough_char_mapping = passthrough_mapping
        logger.write(f"清理后的文本: {self.font_to_char_mapping}")
        logger.write(f"保留原码位的文本: {self.font_to_passthrough_char_mapping}")

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

    def get_unicode_cmap_tables(self, font):
        if "cmap" not in font:
            return []
        return [table for table in font["cmap"].tables if table.isUnicode()]

    def rewrite_unicode_cmaps(self, font, replace_codepoint_to_glyph, source_text):
        unicode_cmap_tables = self.get_unicode_cmap_tables(font)
        if not unicode_cmap_tables:
            raise ValueError("字体缺少 Unicode cmap 表")

        source_codepoints = {ord(char) for char in source_text}
        for table in unicode_cmap_tables:
            for codepoint in source_codepoints:
                table.cmap.pop(codepoint, None)
            table.cmap.update(replace_codepoint_to_glyph)

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
            try:
                original_bytes = self.epub.read(font_path)
                original_font = TTFont(BytesIO(original_bytes))
                original_cmap: dict = original_font.getBestCmap() or {}
                miss_char, plain_text = self.ensure_cmap_has_all_text(
                    original_cmap, plain_text
                )
                if len(miss_char) > 0:
                    logger.write(f"字体文件{font_path}缺少字符{miss_char}")
                preserved_text = self.font_to_passthrough_char_mapping.get(font_path, "")
                missing_preserved_chars, preserved_text = self.ensure_cmap_has_all_text(
                    original_cmap, preserved_text
                )
                if len(missing_preserved_chars) > 0:
                    logger.write(f"字体文件{font_path}缺少需保留字符{missing_preserved_chars}")
                if not plain_text:
                    logger.write(f"字体文件{font_path}没有可混淆字符，保留原字体")
                    self.target_epub.writestr(font_path, original_bytes, zipfile.ZIP_DEFLATED)
                    self.font_to_char_mapping[font_path] = {}
                    continue

                obfuscation_codepoint_mapping = self.build_obfuscation_codepoint_mapping(
                    plain_text,
                    preserved_text,
                )
                replace_codepoint_to_glyph = {}
                replace_table = {}
                for plain in plain_text:
                    codepoint = obfuscation_codepoint_mapping[ord(plain)]
                    replace_codepoint_to_glyph[codepoint] = original_cmap[ord(plain)]
                    replace_table[plain] = hex(codepoint).replace("0x", "&#x")

                self.rewrite_unicode_cmaps(
                    original_font,
                    replace_codepoint_to_glyph,
                    plain_text,
                )
                self.set_timestamps(original_font)
                font_stream = BytesIO()
                original_font.save(font_stream)

                self.target_epub.writestr(
                    font_path, font_stream.getvalue(), zipfile.ZIP_DEFLATED
                )
                self.font_to_char_mapping[font_path] = replace_table
                logger.write(f"字体文件{font_path}的加密映射: \n{replace_table}")
            except Exception as e:
                logger.write(f"字体文件{font_path}混淆失败，保留原字体，错误信息: {e}")
                self.target_epub.writestr(font_path, self.epub.read(font_path), zipfile.ZIP_DEFLATED)
                self.font_to_char_mapping[font_path] = {}

    def close_file(self):
        if self.epub:
            self.epub.close()
        if self.target_epub:
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
            protected_content, placeholder_map = self.protect_escaped_angle_entities(content)
            soup = BeautifulSoup(protected_content, "html.parser")
            css_font_rule_index = self.build_css_font_rule_index(soup)

            for tag in soup.find_all(True):
                font_file = self.get_effective_font_file(tag, css_font_rule_index)
                if not font_file or not self.is_target_font_file(font_file):
                    continue
                replace_table = self.font_to_char_mapping.get(font_file, {})
                if not replace_table:
                    continue
                char_replace_table = {
                    source: self.decode_hex_entity(target)
                    for source, target in replace_table.items()
                }
                trans_table = str.maketrans(char_replace_table)
                for text_node in list(self.iter_direct_text_nodes(tag)):
                    text_node.replace_with(text_node.translate(trans_table))
            # 使用 minimal formatter：
            # 1) 会对文本中的 < / & 等进行最小必要转义，避免 &lt;script&gt; 变回真实标签导致 XHTML 结构损坏；
            # 2) 不会像 html formatter 那样把 … / — 等字符广泛替换为命名实体。
            formatted_html = soup.decode(formatter="minimal")
            restored_html = self.restore_escaped_angle_entities(formatted_html, placeholder_map)
            self.target_epub.writestr(
                one_html, restored_html.encode("utf-8"), zipfile.ZIP_DEFLATED
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
):
    logger.write(f"\n正在尝试加密EPUB字体: {epub_path}")
    fe = FontEncrypt(
        epub_path,
        output_path,
        target_font_families=target_font_families,
    )
    if len(fe.fonts) == 0:
        logger.write("没有找到字体文件，退出")
        fe.close_file()
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
