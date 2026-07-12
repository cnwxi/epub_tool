import zipfile
import os
import posixpath
import codecs
import cssselect2
from bs4 import BeautifulSoup, Comment, NavigableString
from tinycss2 import (
    parse_component_value_list,
    parse_stylesheet,
    serialize,
    parse_declaration_list,
    parse_rule_list,
)
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
    from python_backend.services.log import logwriter
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
FONT_RULE_BLOCKER = object()
FONT_RULE_INHERIT = object()
FONT_RULE_REVERT_LAYER = object()
CSS_CUSTOM_PROPERTY_MISSING = object()
CSS_WIDE_KEYWORDS = frozenset(
    {"inherit", "initial", "unset", "revert", "revert-layer"}
)
EPUB_FONT_FILE_EXTENSIONS = (".ttf", ".otf", ".woff", ".woff2")
GENERIC_FONT_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "emoji",
        "math",
        "fangsong",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
    }
)
FONT_FAMILY_NON_BLOCKING_KEYWORDS = frozenset({"inherit", "unset", "normal"})
CSS_SUPPORTS_KNOWN_PROPERTIES = frozenset(
    {
        "background",
        "background-color",
        "border",
        "color",
        "display",
        "font",
        "font-family",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "height",
        "line-height",
        "margin",
        "opacity",
        "padding",
        "text-align",
        "text-decoration",
        "visibility",
        "width",
    }
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
        names = epub.namelist()
        css_files = [item for item in names if item.lower().endswith(".css")]
        font_file_names = {
            os.path.basename(item).lower()
            for item in names
            if item.lower().endswith(EPUB_FONT_FILE_EXTENSIONS)
        }
        if not css_files or not font_file_names:
            return {"font_families": []}

        font_families = set()

        for css in css_files:
            try:
                content_bytes = epub.read(css)
                if b"@font-face" not in content_bytes.lower():
                    continue
                content = content_bytes.decode("utf-8")
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
            elif file.lower().endswith(EPUB_FONT_FILE_EXTENSIONS):
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

    def is_css_ignored_token(self, token):
        return token.type in ("whitespace", "comment")

    def trim_css_tokens(self, tokens):
        result = list(tokens)
        while result and self.is_css_ignored_token(result[0]):
            result.pop(0)
        while result and self.is_css_ignored_token(result[-1]):
            result.pop()
        return result

    def split_css_tokens_on_comma(self, tokens):
        groups = []
        current = []
        for token in tokens:
            if token.type == "literal" and token.value == ",":
                groups.append(current)
                current = []
                continue
            current.append(token)
        groups.append(current)
        return groups

    def font_family_candidate_from_tokens(self, tokens):
        tokens = self.trim_css_tokens(tokens)
        if not tokens:
            return ""
        if len(tokens) == 1 and tokens[0].type == "string":
            return tokens[0].value.strip()
        meaningful_tokens = [
            token for token in tokens if not self.is_css_ignored_token(token)
        ]
        if self.contains_css_var_function(meaningful_tokens):
            return serialize(tokens).strip().strip("'\"")
        if not all(token.type == "ident" for token in meaningful_tokens):
            return ""
        return serialize(tokens).strip().strip("'\"")

    def extract_font_family_candidates_from_tokens(self, tokens):
        candidates = []
        for group in self.split_css_tokens_on_comma(tokens):
            candidate = self.font_family_candidate_from_tokens(group)
            if not candidate:
                return []
            candidates.append(candidate)
        return candidates

    def is_font_shorthand_size_token(self, token):
        if token.type in ("dimension", "percentage"):
            return True
        if token.type == "ident":
            return token.value.lower() in {
                "xx-small",
                "x-small",
                "small",
                "medium",
                "large",
                "x-large",
                "xx-large",
                "xxx-large",
                "larger",
                "smaller",
            }
        if token.type == "function":
            return token.lower_name in ("calc", "clamp", "max", "min", "var")
        return False

    def extract_font_shorthand_family_tokens(self, tokens):
        significant_tokens = [
            token for token in tokens if not self.is_css_ignored_token(token)
        ]
        size_index = None
        for index, token in enumerate(significant_tokens):
            if self.is_font_shorthand_size_token(token):
                size_index = index
                break
        if size_index is None:
            return []

        family_tokens = significant_tokens[size_index + 1 :]
        if family_tokens and family_tokens[0].type == "literal" and family_tokens[0].value == "/":
            family_tokens = family_tokens[2:] if len(family_tokens) > 1 else []
        return family_tokens

    def extract_font_candidates_from_value(self, property_name, value_tokens, include_generic=False):
        candidates = []
        if property_name == "font-family":
            candidates.extend(
                self.extract_font_family_candidates_from_tokens(value_tokens)
            )
        elif property_name == "font":
            family_tokens = self.extract_font_shorthand_family_tokens(
                value_tokens
            )
            if family_tokens:
                candidates.extend(
                    self.extract_font_family_candidates_from_tokens(family_tokens)
                )
            elif self.contains_css_var_function(
                value_tokens
            ) or self.get_css_global_keyword(value_tokens):
                raw = serialize(value_tokens).strip().strip("'\"")
                if raw:
                    candidates.append(raw)

        dedup = []
        seen = set()
        for item in candidates:
            normalized = self.normalize_font_name(item)
            if not normalized or normalized in seen:
                continue
            if normalized in FONT_FAMILY_NON_BLOCKING_KEYWORDS:
                continue
            if not include_generic and normalized in GENERIC_FONT_FAMILIES:
                continue
            seen.add(normalized)
            dedup.append(item)
        return dedup

    def extract_font_candidates_from_declaration(self, declaration, include_generic=False):
        if declaration.type != "declaration":
            return []
        return self.extract_font_candidates_from_value(
            declaration.lower_name,
            declaration.value,
            include_generic=include_generic,
        )

    def get_css_global_keyword(self, value_tokens):
        tokens = self.trim_css_tokens(value_tokens)
        if len(tokens) != 1 or tokens[0].type != "ident":
            return ""
        keyword = tokens[0].value.lower()
        return keyword if keyword in CSS_WIDE_KEYWORDS else ""

    def get_css_font_global_keyword(self, property_name, value_tokens):
        if property_name not in ("font-family", "font", "all"):
            return ""
        return self.get_css_global_keyword(value_tokens)

    def get_css_custom_property_global_keyword(self, value_tokens):
        return self.get_css_global_keyword(value_tokens)

    def contains_css_var_function(self, tokens):
        for token in tokens:
            if token.type != "function":
                continue
            if token.lower_name == "var":
                return True
            if self.contains_css_var_function(getattr(token, "arguments", [])):
                return True
        return False

    def split_css_var_arguments(self, tokens):
        comma_index = None
        for index, token in enumerate(tokens):
            if token.type == "literal" and token.value == ",":
                comma_index = index
                break
        if comma_index is None:
            name_tokens = self.trim_css_tokens(tokens)
            fallback_tokens = []
        else:
            name_tokens = self.trim_css_tokens(tokens[:comma_index])
            fallback_tokens = self.trim_css_tokens(tokens[comma_index + 1 :])
        if not name_tokens:
            return "", []
        if len(name_tokens) != 1 or name_tokens[0].type != "ident":
            return "", []
        return name_tokens[0].value, fallback_tokens

    def resolve_css_var_tokens(self, tokens, custom_properties, seen=None):
        seen = seen or set()
        resolved = []
        for token in tokens:
            if token.type == "function" and token.lower_name == "var":
                property_name, fallback_tokens = self.split_css_var_arguments(
                    token.arguments
                )
                if not property_name:
                    return None
                if property_name in custom_properties and property_name not in seen:
                    nested = self.resolve_css_var_tokens(
                        custom_properties[property_name],
                        custom_properties,
                        seen | {property_name},
                    )
                elif fallback_tokens:
                    nested = self.resolve_css_var_tokens(
                        fallback_tokens,
                        custom_properties,
                        seen,
                    )
                else:
                    return None
                if nested is None:
                    return None
                resolved.extend(nested)
            elif token.type == "function" and self.contains_css_var_function(
                getattr(token, "arguments", [])
            ):
                nested = self.resolve_css_var_tokens(
                    token.arguments,
                    custom_properties,
                    seen,
                )
                if nested is None:
                    return None
                resolved.append(token)
            else:
                resolved.append(token)
        return resolved

    def resolve_css_font_value(self, property_name, value_tokens, custom_properties=None):
        custom_properties = custom_properties or {}
        resolved_tokens = self.resolve_css_var_tokens(value_tokens, custom_properties)
        if resolved_tokens is None:
            if property_name in ("font-family", "font"):
                return FONT_RULE_INHERIT, None, ["inherit"]
            return None, None, []
        global_keyword = self.get_css_font_global_keyword(
            property_name,
            resolved_tokens,
        )
        if global_keyword in ("inherit", "unset"):
            return FONT_RULE_INHERIT, None, [global_keyword]
        if global_keyword == "revert-layer":
            return FONT_RULE_REVERT_LAYER, None, [global_keyword]
        if property_name == "all" and global_keyword in ("initial", "revert"):
            return None, None, [global_keyword]
        candidates = self.extract_font_candidates_from_value(
            property_name,
            resolved_tokens,
            include_generic=True,
        )
        if not candidates:
            if self.contains_css_var_function(value_tokens) and property_name in (
                "font-family",
                "font",
            ):
                return FONT_RULE_INHERIT, None, ["inherit"]
            return None, None, []
        font_file, matched_family = self.resolve_font_candidate(candidates)
        return font_file, matched_family, candidates

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
        if not font_file or font_file is FONT_RULE_BLOCKER:
            return False
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

    def add_selector_specificity(self, left, right):
        return tuple(left[index] + right[index] for index in range(3))

    def max_selector_specificity(self, items):
        return max(items, default=(0, 0, 0))

    def iter_css_function_blocks(self, tokens):
        for token in tokens:
            if token.type == "function":
                yield token
                yield from self.iter_css_function_blocks(token.arguments)
            elif token.type == "[] block":
                yield from self.iter_css_function_blocks(token.content)

    def calculate_nth_child_of_specificity(self, selector, depth=0):
        if depth > 8:
            return 0, 0, 0
        try:
            tokens = parse_component_value_list(selector or "")
        except Exception:
            return 0, 0, 0
        result = (0, 0, 0)
        for function in self.iter_css_function_blocks(tokens):
            if function.lower_name not in ("nth-child", "nth-last-child"):
                continue
            of_index = None
            for index, token in enumerate(function.arguments):
                if token.type == "ident" and token.value.lower() == "of":
                    of_index = index
                    break
            if of_index is None:
                continue
            selector_text = serialize(function.arguments[of_index + 1 :]).strip()
            selector_specificity = self.max_selector_specificity(
                self.calculate_selector_specificity(item, depth + 1)
                for item in self.split_css_selector_list(selector_text)
            )
            result = self.add_selector_specificity(result, selector_specificity)
        return result

    def calculate_selector_specificity(self, selector, depth=0):
        try:
            selectors = cssselect2.compile_selector_list(selector or "")
        except cssselect2.SelectorError:
            base_specificity = (0, 0, 0)
        else:
            base_specificity = self.max_selector_specificity(
                compiled.specificity for compiled in selectors
            )
        nth_of_specificity = self.calculate_nth_child_of_specificity(selector, depth)
        return self.add_selector_specificity(base_specificity, nth_of_specificity)

    def record_css_selector_font_rule(
        self,
        selector,
        font_file,
        mapping,
        order,
        matched_family=None,
        is_blocker=False,
        is_inherit=False,
        is_revert_layer=False,
        important=False,
        source_path=None,
        html_path=None,
        match_selector=None,
        scope_root_selector=None,
        scope_limit_selectors=None,
        layer_order=None,
        declaration_name=None,
        value_tokens=None,
        rule_list=None,
    ):
        mapping_selector = match_selector or selector
        if not is_blocker and self.is_target_font_file(font_file, matched_family):
            mapping[mapping_selector] = font_file
        target_rule_list = (
            rule_list if rule_list is not None else self.css_selector_font_rules
        )
        target_rule_list.append(
            {
                "selector": selector,
                "match_selector": mapping_selector,
                "font_file": font_file,
                "family": matched_family,
                "resolved": bool(font_file),
                "is_blocker": is_blocker,
                "is_inherit": is_inherit,
                "is_revert_layer": is_revert_layer,
                "specificity": self.calculate_selector_specificity(selector),
                "order": order,
                "important": bool(important),
                "layer_order": layer_order,
                "source_path": source_path,
                "html_path": html_path,
                "scope_root_selector": scope_root_selector,
                "scope_limit_selectors": list(scope_limit_selectors or []),
                "declaration_name": declaration_name,
                "value_tokens": list(value_tokens or []),
            }
        )

    def record_css_custom_property_rule(
        self,
        selector,
        property_name,
        value_tokens,
        order,
        important=False,
        source_path=None,
        html_path=None,
        match_selector=None,
        scope_root_selector=None,
        scope_limit_selectors=None,
        layer_order=None,
    ):
        self.css_custom_property_rules.append(
            {
                "selector": selector,
                "match_selector": match_selector or selector,
                "property_name": property_name,
                "value_tokens": list(value_tokens),
                "specificity": self.calculate_selector_specificity(selector),
                "order": order,
                "important": bool(important),
                "layer_order": layer_order,
                "source_path": source_path,
                "html_path": html_path,
                "scope_root_selector": scope_root_selector,
                "scope_limit_selectors": list(scope_limit_selectors or []),
            }
        )

    def resolve_css_font_declaration(self, declarations):
        selected = None
        for declaration_order, declaration in enumerate(declarations, 1):
            if declaration.type != "declaration":
                continue
            if declaration.lower_name not in ("font-family", "font", "all"):
                continue
            global_keyword = self.get_css_font_global_keyword(
                declaration.lower_name,
                declaration.value,
            )
            candidates = self.extract_font_candidates_from_declaration(
                declaration,
                include_generic=True,
            )
            if global_keyword in ("inherit", "unset", "revert-layer"):
                candidates = [global_keyword]
            elif declaration.lower_name == "all" and global_keyword in (
                "initial",
                "revert",
            ):
                candidates = [global_keyword]
            if not candidates:
                continue
            precedence = (1 if declaration.important else 0, declaration_order)
            if selected is None or precedence >= selected["precedence"]:
                selected = {
                    "candidates": candidates,
                    "important": bool(declaration.important),
                    "precedence": precedence,
                    "declaration_name": declaration.lower_name,
                    "value_tokens": list(declaration.value),
                    "is_inherit": global_keyword in ("inherit", "unset"),
                    "is_revert_layer": global_keyword == "revert-layer",
                }
        if selected is None:
            return None, None, False, [], None, []
        if selected.get("is_inherit"):
            font_file, matched_family = FONT_RULE_INHERIT, None
        elif selected.get("is_revert_layer"):
            font_file, matched_family = FONT_RULE_REVERT_LAYER, None
        else:
            font_file, matched_family = self.resolve_font_candidate(
                selected["candidates"]
            )
        return (
            font_file,
            matched_family,
            selected["important"],
            selected["candidates"],
            selected["declaration_name"],
            selected["value_tokens"],
        )

    def build_font_rule_precedence(
        self,
        important,
        specificity=None,
        order=0,
        is_inline=False,
        layer_order=None,
        scope_proximity=None,
    ):
        if is_inline:
            cascade_specificity = (1, 0, 0, 0)
        else:
            normalized_specificity = tuple(specificity or (0, 0, 0))
            if len(normalized_specificity) == 3:
                cascade_specificity = (0, *normalized_specificity)
            else:
                cascade_specificity = normalized_specificity
        max_layer_order = 1000000
        if is_inline and important:
            layer_score = max_layer_order * 2
        elif important:
            layer_score = -layer_order if layer_order is not None else -max_layer_order
        else:
            layer_score = max_layer_order if layer_order is None else layer_order
        max_scope_proximity = 1000000
        scope_score = (
            -scope_proximity
            if scope_proximity is not None
            else -max_scope_proximity
        )
        return (1 if important else 0, layer_score, cascade_specificity, scope_score, order)

    def normalize_css_condition_text(self, value):
        if isinstance(value, list):
            if all(hasattr(item, "type") for item in value):
                raw = serialize(value)
            else:
                raw = " ".join(str(item) for item in value)
        else:
            raw = str(value or "")
        return re.sub(r"\s+", " ", raw.strip())

    def media_query_applies_to_epub(self, query):
        query = self.normalize_css_condition_text(query).lower()
        if not query:
            return True
        query = re.sub(r"/\*.*?\*/", " ", query).strip()
        if query.startswith("only "):
            query = query[5:].strip()
        negated = False
        if query.startswith("not "):
            query = query[4:].strip()
            negated = True
            if query.startswith("("):
                return True

        match = re.match(r"([a-z][\w-]*)\b", query)
        media_type = match.group(1) if match else "all"
        applies = media_type in ("all", "screen")
        return not applies if negated else applies

    def media_query_list_applies_to_epub(self, media_text):
        media_text = self.normalize_css_condition_text(media_text)
        if not media_text:
            return True
        queries = self.split_css_selector_list(media_text)
        if not queries:
            return True
        return any(self.media_query_applies_to_epub(query) for query in queries)

    def strip_leading_css_function_clause(self, text, name):
        stripped = text.lstrip()
        if not stripped.lower().startswith(name):
            return text
        index = len(name)
        if len(stripped) > index and stripped[index] not in (" ", "\t", "\r", "\n", "("):
            return text
        remainder = stripped[index:].lstrip()
        if not remainder.startswith("("):
            return remainder

        depth = 0
        quote = None
        escaped = False
        for position, char in enumerate(remainder):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return remainder[position + 1 :].lstrip()
        return ""

    def strip_css_import_supports_prefix(self, text):
        stripped = (text or "").lstrip()
        name = "supports"
        if not stripped.lower().startswith(name):
            return text, True, False
        index = len(name)
        if len(stripped) > index and stripped[index] not in (" ", "\t", "\r", "\n", "("):
            return text, True, False
        remainder = stripped[index:].lstrip()
        if not remainder.startswith("("):
            return remainder, True, True
        condition, end_index = self.extract_first_parenthesized_range(remainder)
        if condition is None:
            return "", False, True
        return (
            remainder[end_index + 1 :].lstrip(),
            self.css_supports_condition_applies(condition),
            True,
        )

    def strip_css_import_media_prefixes(self, media_text):
        previous = None
        current = (media_text or "").strip()
        while current and current != previous:
            previous = current
            current = self.strip_leading_css_function_clause(current, "layer").strip()
            current, supports_applies, _ = self.strip_css_import_supports_prefix(current)
            if not supports_applies:
                return None
            current = current.strip()
        return current

    def css_import_media_applies_to_epub(self, media_text):
        media_text = self.strip_css_import_media_prefixes(media_text)
        if media_text is None:
            return False
        return self.media_query_list_applies_to_epub(media_text)

    def extract_first_parenthesized_range(self, text, start_index=0):
        start = text.find("(", start_index)
        if start < 0:
            return None, -1
        depth = 0
        quote = None
        escaped = False
        for position, char in enumerate(text[start:], start):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : position].strip(), position
        return None, -1

    def extract_first_parenthesized_text(self, text):
        value, _ = self.extract_first_parenthesized_range(text)
        return value

    def extract_scope_selectors(self, prelude):
        text = self.normalize_css_condition_text(prelude)
        scope_text, scope_end = self.extract_first_parenthesized_range(text)
        if not scope_text:
            return [], []
        scope_roots = [
            selector
            for selector in self.split_css_selector_list(scope_text)
            if selector
        ]
        scope_limits = []
        remainder = text[scope_end + 1 :].strip() if scope_end >= 0 else ""
        if re.match(r"(?i)^to\b", remainder):
            limit_text = self.extract_first_parenthesized_text(
                re.sub(r"(?i)^to\b", "", remainder, count=1).strip()
            )
            if limit_text:
                scope_limits = [
                    selector
                    for selector in self.split_css_selector_list(limit_text)
                    if selector
                ]
        return scope_roots, scope_limits

    def build_scoped_match_selector(self, selector, scope_prefix):
        scope_prefix = (scope_prefix or "").strip()
        if not scope_prefix:
            return selector
        stripped_selector = selector.lstrip()
        leading_space = selector[: len(selector) - len(stripped_selector)]
        if stripped_selector.startswith(":scope"):
            return f"{scope_prefix}{stripped_selector[len(':scope'):]}"
        return f"{scope_prefix} {leading_space}{stripped_selector}".strip()

    def build_scope_contexts(self, current_contexts, scope_roots, scope_limits=None):
        contexts = current_contexts or [{"prefix": "", "limit_selectors": []}]
        return [
            {
                "prefix": f"{context.get('prefix', '')} {scope_root}".strip(),
                "limit_selectors": list(context.get("limit_selectors", []))
                + [
                    self.build_scoped_match_selector(
                        scope_limit,
                        f"{context.get('prefix', '')} {scope_root}".strip(),
                    )
                    for scope_limit in (scope_limits or [])
                ],
            }
            for context in contexts
            for scope_root in scope_roots
        ]

    def strip_enclosing_css_parentheses(self, text):
        text = (text or "").strip()
        if not text.startswith("(") or not text.endswith(")"):
            return text
        depth = 0
        quote = None
        escaped = False
        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    return text
        return text[1:-1].strip() if depth == 0 else text

    def split_css_condition_on_operator(self, text, operator):
        parts = []
        start = 0
        index = 0
        depth = 0
        quote = None
        escaped = False
        lower_text = text.lower()
        while index < len(text):
            char = text[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\":
                escaped = True
                index += 1
                continue
            if quote:
                if char == quote:
                    quote = None
                index += 1
                continue
            if char in ("'", '"'):
                quote = char
                index += 1
                continue
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")":
                depth = max(0, depth - 1)
                index += 1
                continue
            end = index + len(operator)
            if (
                depth == 0
                and lower_text.startswith(operator, index)
                and (index == 0 or not re.match(r"[\w-]", text[index - 1]))
                and (end == len(text) or not re.match(r"[\w-]", text[end]))
            ):
                parts.append(text[start:index].strip())
                start = end
                index = end
                continue
            index += 1
        if not parts:
            return []
        parts.append(text[start:].strip())
        return [part for part in parts if part]

    def css_supports_declaration_applies(self, text):
        declarations = [
            declaration
            for declaration in parse_declaration_list(text)
            if declaration.type == "declaration"
        ]
        if len(declarations) != 1:
            return True
        property_name = declarations[0].lower_name
        return property_name.startswith("--") or property_name in CSS_SUPPORTS_KNOWN_PROPERTIES

    def css_supports_selector_applies(self, text):
        selector_text = self.extract_first_parenthesized_text(text)
        if selector_text is None:
            return True
        try:
            cssselect2.compile_selector_list(selector_text)
        except cssselect2.SelectorError:
            return False
        return True

    def css_supports_condition_applies(self, condition):
        text = self.normalize_css_condition_text(condition)
        if not text:
            return True
        text = self.strip_enclosing_css_parentheses(text)

        parts = self.split_css_condition_on_operator(text, "or")
        if parts:
            return any(self.css_supports_condition_applies(part) for part in parts)
        parts = self.split_css_condition_on_operator(text, "and")
        if parts:
            return all(self.css_supports_condition_applies(part) for part in parts)
        if re.match(r"(?i)^not\b", text):
            return not self.css_supports_condition_applies(
                re.sub(r"(?i)^not\b", "", text, count=1).strip()
            )

        inner_text = self.strip_enclosing_css_parentheses(text)
        if inner_text != text:
            return self.css_supports_condition_applies(inner_text)
        if re.match(r"(?is)^selector\s*\(", text):
            return self.css_supports_selector_applies(text)
        if ":" in text:
            return self.css_supports_declaration_applies(text)
        return True

    def ensure_css_layer_order(self, layer_name):
        if not layer_name:
            return None
        if not hasattr(self, "_css_layer_order"):
            self._css_layer_order = {}
        if layer_name not in self._css_layer_order:
            self._css_layer_order[layer_name] = len(self._css_layer_order) + 1
        return self._css_layer_order[layer_name]

    def next_anonymous_css_layer_name(self):
        self._css_anonymous_layer_count = getattr(
            self,
            "_css_anonymous_layer_count",
            0,
        ) + 1
        return f"__anonymous_layer_{self._css_anonymous_layer_count}"

    def extract_css_layer_names(self, prelude):
        text = self.normalize_css_condition_text(prelude)
        return [
            name
            for name in self.split_css_selector_list(text)
            if name
        ]

    def build_css_layer_name(self, parent_layer, layer_name):
        if not parent_layer:
            return layer_name
        if not layer_name:
            return parent_layer
        return f"{parent_layer}.{layer_name}"

    def iter_css_qualified_rules(
        self,
        rules,
        scope_contexts=None,
        layer_name=None,
    ):
        scope_contexts = scope_contexts or [{"prefix": "", "limit_selectors": []}]
        for rule in rules:
            if rule.type == "qualified-rule":
                yield rule, scope_contexts, self.ensure_css_layer_order(layer_name)
            elif (
                rule.type == "at-rule"
                and rule.content
                and rule.lower_at_keyword
                in ("media", "supports", "layer", "container", "scope")
            ):
                if (
                    rule.lower_at_keyword == "media"
                    and not self.media_query_list_applies_to_epub(rule.prelude)
                ):
                    continue
                if (
                    rule.lower_at_keyword == "supports"
                    and not self.css_supports_condition_applies(rule.prelude)
                ):
                    continue
                if rule.lower_at_keyword == "container":
                    continue
                nested_layer_name = layer_name
                if rule.lower_at_keyword == "layer":
                    layer_names = self.extract_css_layer_names(rule.prelude)
                    if layer_names:
                        nested_layer_name = self.build_css_layer_name(
                            layer_name,
                            layer_names[0],
                        )
                    else:
                        nested_layer_name = self.build_css_layer_name(
                            layer_name,
                            self.next_anonymous_css_layer_name(),
                        )
                    self.ensure_css_layer_order(nested_layer_name)
                nested_scope_contexts = scope_contexts
                if rule.lower_at_keyword == "scope":
                    scope_roots, scope_limits = self.extract_scope_selectors(rule.prelude)
                    if not scope_roots:
                        continue
                    nested_scope_contexts = self.build_scope_contexts(
                        scope_contexts,
                        scope_roots,
                        scope_limits,
                    )
                yield from self.iter_css_qualified_rules(
                    parse_rule_list(rule.content),
                    nested_scope_contexts,
                    nested_layer_name,
                )
            elif (
                rule.type == "at-rule"
                and not rule.content
                and rule.lower_at_keyword == "layer"
            ):
                for layer_name_item in self.extract_css_layer_names(rule.prelude):
                    self.ensure_css_layer_order(
                        self.build_css_layer_name(layer_name, layer_name_item)
                    )

    def iter_css_font_face_rules(self, rules):
        for rule in rules:
            if rule.type == "at-rule" and rule.lower_at_keyword == "font-face":
                yield rule
            elif (
                rule.type == "at-rule"
                and rule.content
                and rule.lower_at_keyword
                in ("media", "supports", "layer", "container", "scope")
            ):
                if (
                    rule.lower_at_keyword == "media"
                    and not self.media_query_list_applies_to_epub(rule.prelude)
                ):
                    continue
                if (
                    rule.lower_at_keyword == "supports"
                    and not self.css_supports_condition_applies(rule.prelude)
                ):
                    continue
                if rule.lower_at_keyword == "container":
                    continue
                yield from self.iter_css_font_face_rules(parse_rule_list(rule.content))

    def iter_css_import_paths(self, css_text, base_path):
        for rule in parse_stylesheet(css_text):
            if rule.type in ("whitespace", "comment"):
                continue
            if rule.type != "at-rule":
                break
            if rule.lower_at_keyword in ("charset", "layer") and not rule.content:
                continue
            if rule.lower_at_keyword != "import":
                break
            if rule.content:
                break
            prelude = serialize(rule.prelude).strip()
            match = re.search(
                r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)|['\"]([^'\"]+)['\"]",
                prelude,
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            href = match.group(1) or match.group(2)
            media_text = prelude[match.end() :].strip()
            if not self.css_import_media_applies_to_epub(media_text):
                continue
            import_path = self.resolve_book_path(base_path, href)
            if import_path:
                yield import_path

    def split_css_selector_list(self, selector):
        selectors = []
        current = []
        quote = None
        escaped = False
        depth = 0
        for char in selector or "":
            if escaped:
                current.append(char)
                escaped = False
                continue
            if char == "\\":
                current.append(char)
                escaped = True
                continue
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                current.append(char)
                quote = char
                continue
            if char in "([":
                depth += 1
            elif char in ")]" and depth > 0:
                depth -= 1
            if char == "," and depth == 0:
                item = "".join(current).strip()
                if item:
                    selectors.append(item)
                current = []
                continue
            current.append(char)
        item = "".join(current).strip()
        if item:
            selectors.append(item)
        return selectors

    def iter_css_text_with_imports(self, css_path, css_text, seen=None):
        seen = seen or set()
        if css_path in seen:
            return
        seen.add(css_path)
        for import_path in self.iter_css_import_paths(css_text, css_path):
            if import_path not in self.epub.namelist():
                continue
            try:
                with self.epub.open(import_path) as f:
                    import_text = f.read().decode("utf-8")
            except Exception:
                continue
            yield from self.iter_css_text_with_imports(import_path, import_text, seen)
        yield css_path, css_text

    def iter_document_css_texts(self, soup, html_path):
        for tag in soup.find_all(["link", "style"]):
            if not self.media_query_list_applies_to_epub(tag.get("media", "")):
                continue
            if tag.name == "style":
                css_text = tag.get_text() or ""
                if css_text.strip():
                    yield html_path, css_text
                continue
            href = tag.get("href")
            if not href:
                continue
            rel = tag.get("rel", [])
            rel_values = (
                [item.lower() for item in rel]
                if isinstance(rel, list)
                else str(rel).lower().split()
            )
            if "alternate" in rel_values:
                continue
            if "stylesheet" not in rel_values and not href.lower().endswith(".css"):
                continue
            css_path = self.resolve_book_path(html_path, href)
            if css_path not in self.epub.namelist():
                continue
            try:
                with self.epub.open(css_path) as f:
                    css_text = f.read().decode("utf-8")
            except Exception:
                continue
            yield from self.iter_css_text_with_imports(css_path, css_text)

    def parse_css_selector_mapping(
        self,
        css_text,
        source_path,
        mapping,
        html_path=None,
        rule_list=None,
        order_counter=None,
    ):
        rules = parse_stylesheet(css_text)
        for rule, scope_contexts, layer_order in self.iter_css_qualified_rules(rules):
            selector = serialize(rule.prelude).strip()
            if not selector:
                continue
            declarations = parse_declaration_list(rule.content)
            if order_counter is None:
                self._css_selector_rule_order += 1
                rule_order = self._css_selector_rule_order
            else:
                order_counter["value"] += 1
                rule_order = order_counter["value"]
            for declaration_order, declaration in enumerate(declarations, 1):
                if (
                    declaration.type != "declaration"
                    or not declaration.lower_name.startswith("--")
                ):
                    continue
                for one_selector in self.split_css_selector_list(selector):
                    one_selector = one_selector.strip()
                    if not one_selector:
                        continue
                    for scope_context in scope_contexts:
                        self.record_css_custom_property_rule(
                            one_selector,
                            declaration.lower_name,
                            declaration.value,
                            (rule_order, declaration_order),
                            important=declaration.important,
                            source_path=source_path,
                            html_path=html_path,
                            match_selector=self.build_scoped_match_selector(
                                one_selector,
                                scope_context.get("prefix", ""),
                            ),
                            scope_root_selector=scope_context.get("prefix", "") or None,
                            scope_limit_selectors=scope_context.get(
                                "limit_selectors",
                                [],
                            ),
                            layer_order=layer_order,
                        )
            (
                font_file,
                matched_family,
                important,
                candidates,
                declaration_name,
                value_tokens,
            ) = (
                self.resolve_css_font_declaration(declarations)
            )
            if not font_file and not candidates:
                continue
            for one_selector in self.split_css_selector_list(selector):
                one_selector = one_selector.strip()
                if one_selector:
                    for scope_context in scope_contexts:
                        self.record_css_selector_font_rule(
                            one_selector,
                            font_file,
                            mapping,
                            rule_order,
                            matched_family=matched_family,
                            is_blocker=font_file is None,
                            is_inherit=font_file is FONT_RULE_INHERIT,
                            is_revert_layer=font_file is FONT_RULE_REVERT_LAYER,
                            important=important,
                            source_path=source_path,
                            html_path=html_path,
                            match_selector=self.build_scoped_match_selector(
                                one_selector,
                                scope_context.get("prefix", ""),
                            ),
                            scope_root_selector=scope_context.get("prefix", "") or None,
                            scope_limit_selectors=scope_context.get(
                                "limit_selectors",
                                [],
                            ),
                            layer_order=layer_order,
                            declaration_name=declaration_name,
                            value_tokens=value_tokens,
                            rule_list=rule_list,
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
                for rule in self.iter_css_font_face_rules(rules):
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
        self.css_custom_property_rules = []
        self._css_selector_rule_order = 0
        for one_html in self.htmls:
            with self.epub.open(one_html) as f:
                html_content = f.read().decode("utf-8")
            soup = BeautifulSoup(html_content, "html.parser")
            for source_path, css_text in self.iter_document_css_texts(soup, one_html):
                self.parse_css_selector_mapping(
                    css_text,
                    source_path,
                    mapping,
                    html_path=one_html,
                )

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

    def find_tag_end(self, html_text, start):
        quote = None
        for index in range(start + 1, len(html_text)):
            char = html_text[index]
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                continue
            if char == ">":
                return index
        return -1

    def is_markup_start_tag(self, html_text, start):
        if start + 1 >= len(html_text):
            return False
        next_char = html_text[start + 1]
        return bool(re.match(r"[A-Za-z_:]", next_char))

    def inject_cssselect2_markers(self, html_text):
        marker_attr = f"data-epub-tool-node-{uuid.uuid4().hex}"
        parts = []
        last_index = 0
        search_index = 0
        marker_index = 0
        while True:
            start = html_text.find("<", search_index)
            if start < 0:
                break
            if html_text.startswith("<!--", start):
                end = html_text.find("-->", start + 4)
                search_index = len(html_text) if end < 0 else end + 3
                continue
            if html_text.startswith("<![CDATA[", start):
                end = html_text.find("]]>", start + 9)
                search_index = len(html_text) if end < 0 else end + 3
                continue
            if html_text.startswith("<?", start):
                end = html_text.find("?>", start + 2)
                search_index = len(html_text) if end < 0 else end + 2
                continue
            end = self.find_tag_end(html_text, start)
            if end < 0:
                break
            if not self.is_markup_start_tag(html_text, start):
                search_index = end + 1
                continue

            marker_index += 1
            body = html_text[start + 1 : end]
            stripped_body = body.rstrip()
            insert_index = end
            if stripped_body.endswith("/"):
                insert_index = start + 1 + len(stripped_body) - 1
            parts.append(html_text[last_index:insert_index])
            parts.append(f' {marker_attr}="{marker_index}"')
            last_index = insert_index
            search_index = end + 1
            tag_match = re.match(r"\s*([A-Za-z_:][\w:.-]*)", body)
            if tag_match and tag_match.group(1).split(":")[-1].lower() in (
                "script",
                "style",
            ):
                close_match = re.search(
                    rf"</\s*{re.escape(tag_match.group(1))}\s*>",
                    html_text[end + 1 :],
                    flags=re.IGNORECASE,
                )
                if close_match:
                    search_index = end + 1 + close_match.end()
        parts.append(html_text[last_index:])
        return "".join(parts), marker_attr

    def remove_cssselect2_markers(self, soup, marker_attr):
        if not marker_attr:
            return
        for tag in soup.find_all(True):
            tag.attrs.pop(marker_attr, None)

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

    def build_inline_font_rule_record(self, tag):
        if not tag or not tag.has_attr("style"):
            return None
        declarations = parse_declaration_list(tag.get("style", ""))
        (
            font_file,
            _,
            important,
            candidates,
            declaration_name,
            value_tokens,
        ) = self.resolve_css_font_declaration(declarations)
        if value_tokens:
            custom_properties = self.build_css_custom_property_map(tag)
            font_file, _, resolved_candidates = self.resolve_css_font_value(
                declaration_name,
                value_tokens,
                custom_properties,
            )
            if not resolved_candidates:
                return None
            candidates = resolved_candidates
        if not font_file and not candidates:
            return None
        return {
            "font_file": font_file,
            "is_blocker": font_file is None,
            "is_inherit": font_file is FONT_RULE_INHERIT,
            "is_revert_layer": font_file is FONT_RULE_REVERT_LAYER,
            "precedence": self.build_font_rule_precedence(
                important,
                order=0,
                is_inline=True,
            ),
        }

    def build_cssselect2_match_context(self, soup, html_content=None, marker_attr=None):
        if not html_content or not marker_attr:
            return None
        soup_nodes_by_marker = {
            tag.get(marker_attr): tag
            for tag in soup.find_all(True)
            if tag.has_attr(marker_attr)
        }
        try:
            root = ElementTree.fromstring(html_content.encode("utf-8"))
            wrapped_root = cssselect2.ElementWrapper.from_html_root(root)
            wrapped_elements = []
            for wrapped in wrapped_root.iter_subtree():
                marker = wrapped.etree_element.attrib.pop(marker_attr, None)
                if marker in soup_nodes_by_marker:
                    wrapped_elements.append((wrapped, soup_nodes_by_marker[marker]))
            return wrapped_elements
        except Exception:
            return None

    def build_scope_exclusion_indexes(self, soup, rules):
        for rule in rules:
            root_selector = rule.get("scope_root_selector")
            if root_selector:
                try:
                    root_elements = soup.select(root_selector)
                except Exception:
                    root_elements = []
                rule["scope_root_element_ids"] = {
                    id(root_element)
                    for root_element in root_elements
                }
            limit_selectors = rule.get("scope_limit_selectors") or []
            if not limit_selectors:
                continue
            excluded_element_ids = set()
            for limit_selector in limit_selectors:
                try:
                    limit_elements = soup.select(limit_selector)
                except Exception:
                    continue
                for limit_element in limit_elements:
                    excluded_element_ids.add(id(limit_element))
                    for child in limit_element.find_all(True):
                        excluded_element_ids.add(id(child))
            rule["scope_excluded_element_ids"] = excluded_element_ids

    def calculate_scope_proximity(self, element, rule):
        root_ids = rule.get("scope_root_element_ids")
        if not root_ids:
            return None
        proximity = 0
        current = element
        while current is not None and getattr(current, "name", None):
            if id(current) in root_ids:
                return proximity
            current = current.parent
            proximity += 1
        return None

    def apply_css_custom_property_rule_to_element(self, index, element, rule):
        if id(element) in rule.get("scope_excluded_element_ids", set()):
            return
        precedence = self.build_font_rule_precedence(
            rule.get("important", False),
            rule["specificity"],
            rule["order"],
            layer_order=rule.get("layer_order"),
            scope_proximity=self.calculate_scope_proximity(element, rule),
        )
        property_index = index.setdefault(id(element), {})
        entry = property_index.setdefault(rule["property_name"], {"records": []})
        entry.setdefault("records", []).append(
            {
                "value_tokens": rule["value_tokens"],
                "precedence": precedence,
            }
        )

    def build_css_custom_property_index(
        self,
        soup,
        html_path=None,
        html_content=None,
        marker_attr=None,
    ):
        index = {}
        rules = getattr(self, "css_custom_property_rules", [])
        if html_path is not None:
            rules = [
                rule
                for rule in rules
                if rule.get("html_path") in (None, html_path)
            ]
        if not rules:
            return index
        self.build_scope_exclusion_indexes(soup, rules)
        matcher = cssselect2.Matcher()
        fallback_rules = []
        for rule in rules:
            match_selector = rule.get("match_selector", rule["selector"])
            try:
                selectors = cssselect2.compile_selector_list(match_selector)
            except cssselect2.SelectorError:
                fallback_rules.append(rule)
                continue
            added = False
            for selector in selectors:
                if selector.pseudo_element is not None:
                    continue
                matcher.add_selector(selector, rule)
                added = True
            if not added:
                fallback_rules.append(rule)

        match_context = self.build_cssselect2_match_context(
            soup,
            html_content,
            marker_attr,
        )
        if match_context is None:
            fallback_rules = rules
        else:
            for wrapped, element in match_context:
                for _, _, _, rule in matcher.match(wrapped):
                    self.apply_css_custom_property_rule_to_element(index, element, rule)

        for rule in fallback_rules:
            try:
                elements = soup.select(rule.get("match_selector", rule["selector"]))
            except Exception:
                continue
            for element in elements:
                self.apply_css_custom_property_rule_to_element(index, element, rule)
        return index

    def build_inline_custom_property_records(self, tag):
        if not tag or not tag.has_attr("style"):
            return []
        records = []
        declarations = parse_declaration_list(tag.get("style", ""))
        for declaration_order, declaration in enumerate(declarations, 1):
            if (
                declaration.type != "declaration"
                or not declaration.lower_name.startswith("--")
            ):
                continue
            records.append(
                {
                    "property_name": declaration.lower_name,
                    "value_tokens": list(declaration.value),
                    "precedence": self.build_font_rule_precedence(
                        declaration.important,
                        order=declaration_order,
                        is_inline=True,
                    ),
                }
            )
        return records

    def select_css_custom_property_tokens(self, records, inherited_tokens=None):
        candidates = list(records or [])
        while candidates:
            candidates.sort(key=lambda item: item["precedence"], reverse=True)
            selected = candidates[0]
            keyword = self.get_css_custom_property_global_keyword(
                selected["value_tokens"]
            )
            if keyword in ("inherit", "unset"):
                return (
                    inherited_tokens
                    if inherited_tokens is not None
                    else CSS_CUSTOM_PROPERTY_MISSING
                )
            if keyword in ("initial", "revert"):
                return CSS_CUSTOM_PROPERTY_MISSING
            if keyword == "revert-layer":
                selected_precedence = selected["precedence"]
                selected_importance = selected_precedence[0]
                selected_layer_score = selected_precedence[1]
                candidates = [
                    candidate
                    for candidate in candidates
                    if not (
                        candidate["precedence"][0] == selected_importance
                        and candidate["precedence"][1] >= selected_layer_score
                    )
                ]
                continue
            return selected["value_tokens"]
        return (
            inherited_tokens
            if inherited_tokens is not None
            else CSS_CUSTOM_PROPERTY_MISSING
        )

    def build_css_custom_property_map(self, element):
        cache = getattr(self, "_css_custom_property_cache", {})
        element_id = id(element)
        if element_id in cache:
            return cache[element_id]
        chain = []
        current = element
        while current is not None and getattr(current, "name", None):
            chain.append(current)
            current = current.parent
        values = {}
        css_index = getattr(self, "_css_custom_property_index", {})
        for tag in reversed(chain):
            specified = {
                property_name: list(entry.get("records") or [])
                for property_name, entry in css_index.get(id(tag), {}).items()
            }
            for record in self.build_inline_custom_property_records(tag):
                specified.setdefault(record["property_name"], []).append(record)
            for property_name, records in specified.items():
                selected_tokens = self.select_css_custom_property_tokens(
                    records,
                    values.get(property_name),
                )
                if selected_tokens is CSS_CUSTOM_PROPERTY_MISSING:
                    values.pop(property_name, None)
                else:
                    values[property_name] = selected_tokens
        cache[element_id] = values
        self._css_custom_property_cache = cache
        return values

    def apply_css_font_rule_to_element(self, index, element, rule):
        if id(element) in rule.get("scope_excluded_element_ids", set()):
            return
        font_file = rule["font_file"]
        is_blocker = rule.get("is_blocker", False)
        is_inherit = rule.get("is_inherit", False)
        is_revert_layer = rule.get("is_revert_layer", False)
        if rule.get("value_tokens"):
            custom_properties = self.build_css_custom_property_map(element)
            font_file, _, candidates = self.resolve_css_font_value(
                rule.get("declaration_name"),
                rule.get("value_tokens"),
                custom_properties,
            )
            if not candidates:
                return
            is_blocker = font_file is None
            is_inherit = font_file is FONT_RULE_INHERIT
            is_revert_layer = font_file is FONT_RULE_REVERT_LAYER
        precedence = self.build_font_rule_precedence(
            rule.get("important", False),
            rule["specificity"],
            rule["order"],
            layer_order=rule.get("layer_order"),
            scope_proximity=self.calculate_scope_proximity(element, rule),
        )
        entry = index.setdefault(id(element), {"records": []})
        entry.setdefault("records", []).append(
            {
                "font_file": font_file,
                "is_blocker": is_blocker,
                "is_inherit": is_inherit,
                "is_revert_layer": is_revert_layer,
                "precedence": precedence,
            }
        )

    def build_css_font_rule_index(
        self,
        soup,
        html_path=None,
        html_content=None,
        marker_attr=None,
    ):
        index = {}
        rules = getattr(self, "css_selector_font_rules", [])
        if html_path is not None:
            scoped_rules = [
                rule
                for rule in rules
                if rule.get("html_path") in (None, html_path)
            ]
            if scoped_rules:
                rules = scoped_rules
        if not rules:
            rules = [
                {
                    "selector": selector,
                    "font_file": font_file,
                    "specificity": self.calculate_selector_specificity(selector),
                    "order": order,
                    "important": False,
                }
                for order, (selector, font_file) in enumerate(
                    getattr(self, "css_selector_to_font_mapping", {}).items(),
                    1,
                )
            ]
        self._css_custom_property_cache = {}
        self._css_custom_property_index = self.build_css_custom_property_index(
            soup,
            html_path,
            html_content,
            marker_attr,
        )
        self.build_scope_exclusion_indexes(soup, rules)
        matcher = cssselect2.Matcher()
        fallback_rules = []
        for rule in rules:
            match_selector = rule.get("match_selector", rule["selector"])
            try:
                selectors = cssselect2.compile_selector_list(match_selector)
            except cssselect2.SelectorError:
                fallback_rules.append(rule)
                continue
            added = False
            for selector in selectors:
                if selector.pseudo_element is not None:
                    continue
                matcher.add_selector(selector, rule)
                added = True
            if not added:
                fallback_rules.append(rule)

        match_context = self.build_cssselect2_match_context(
            soup,
            html_content,
            marker_attr,
        )
        if match_context is None:
            fallback_rules = rules
        else:
            for wrapped, element in match_context:
                for _, _, _, rule in matcher.match(wrapped):
                    self.apply_css_font_rule_to_element(index, element, rule)

        for rule in fallback_rules:
            try:
                elements = soup.select(rule.get("match_selector", rule["selector"]))
            except Exception:
                continue
            for element in elements:
                self.apply_css_font_rule_to_element(index, element, rule)
        return index

    def select_css_font_rule_record(self, records):
        candidates = list(records or [])
        while candidates:
            candidates.sort(key=lambda item: item["precedence"], reverse=True)
            selected = candidates[0]
            if not selected.get("is_revert_layer"):
                return selected
            selected_precedence = selected["precedence"]
            selected_importance = selected_precedence[0]
            selected_layer_score = selected_precedence[1]
            candidates = [
                candidate
                for candidate in candidates
                if not (
                    candidate["precedence"][0] == selected_importance
                    and candidate["precedence"][1] >= selected_layer_score
                )
            ]
        return None

    def get_css_font_rule_records(self, rule_record):
        if not rule_record:
            return []
        if "records" in rule_record:
            return list(rule_record.get("records") or [])
        return [rule_record]

    def get_effective_font_file(self, tag, css_font_rule_index):
        current = tag
        while current is not None and getattr(current, "name", None):
            rule_record = css_font_rule_index.get(id(current))
            inline_record = self.build_inline_font_rule_record(current)
            records = self.get_css_font_rule_records(rule_record)
            if inline_record:
                records.append(inline_record)
            rule_record = self.select_css_font_rule_record(records)
            if rule_record:
                if rule_record.get("is_blocker"):
                    return FONT_RULE_BLOCKER
                if rule_record.get("is_inherit"):
                    current = current.parent
                    continue
                return rule_record["font_file"]
            current = current.parent
        return None

    def find_char_mapping(self):
        mapping = {}
        for one_html in self.htmls:
            with self.epub.open(one_html) as f:
                content = f.read().decode("utf-8")
                marked_content, marker_attr = self.inject_cssselect2_markers(content)
                soup = BeautifulSoup(marked_content, "html.parser")
                css_font_rule_index = self.build_css_font_rule_index(
                    soup,
                    one_html,
                    marked_content,
                    marker_attr,
                )
                self.remove_cssselect2_markers(soup, marker_attr)
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
            marked_content, marker_attr = self.inject_cssselect2_markers(protected_content)
            soup = BeautifulSoup(marked_content, "html.parser")
            css_font_rule_index = self.build_css_font_rule_index(
                soup,
                one_html,
                marked_content,
                marker_attr,
            )
            self.remove_cssselect2_markers(soup, marker_attr)

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
