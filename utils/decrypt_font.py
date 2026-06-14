import codecs
import os
import posixpath
import re
import sys
import traceback
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Comment, NavigableString
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
from tinycss2 import parse_declaration_list, parse_stylesheet, serialize

try:
    from utils.log import logwriter
except Exception:
    from log import logwriter

logger = logwriter()

DEFAULT_OCR_MODEL_NAME = "PP-OCRv6_small_rec"
OCR_MODEL_NAME = os.environ.get("EPUB_TOOL_OCR_MODEL_NAME", DEFAULT_OCR_MODEL_NAME).strip()
if not OCR_MODEL_NAME:
    OCR_MODEL_NAME = DEFAULT_OCR_MODEL_NAME
ONNX_OCR_MODEL_NAME = f"{OCR_MODEL_NAME}_onnx"
ONNX_MODEL_FILE_NAME = "inference.onnx"
ONNX_LOG_SEVERITY_ERROR = 3


@dataclass(slots=True)
class OcrTextResult:
    text: str
    confidence: float | None = None


def format_ocr_progress(processed_count, total_count):
    if total_count <= 0:
        return ""
    percent = processed_count / total_count * 100
    return f"，进度 {processed_count}/{total_count} ({percent:.1f}%)"


def create_onnx_session_options(ort):
    session_options = ort.SessionOptions()
    session_options.log_severity_level = ONNX_LOG_SEVERITY_ERROR
    return session_options


class OnnxGlyphOcrBackend:
    def __init__(self, options=None):
        options = options or {}
        try:
            import numpy as np
            import onnxruntime as ort
        except Exception as exc:
            raise RuntimeError("ONNX OCR 运行时不可用，请先安装 onnxruntime。") from exc

        self.np = np
        self.model_dir = resolve_onnx_ocr_model_dir(options)
        self.model_path = resolve_onnx_model_path(self.model_dir)
        self.config = load_text_recognition_config(
            resolve_onnx_ocr_config_path(self.model_dir, options)
        )
        self.session = ort.InferenceSession(
            self.model_path,
            sess_options=create_onnx_session_options(ort),
            providers=options.get("onnx_providers") or ["CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        if not inputs:
            raise RuntimeError(f"ONNX 模型没有输入节点: {self.model_path}")
        self.input_name = inputs[0].name
        self.characters = ["blank", *self.config["character_dict"], " "]
        self.image_shape = self.config["image_shape"]
        self.image_mode = self.config["img_mode"]
        self.max_img_width = int(options.get("onnx_max_image_width") or 3200)

    def recognize(self, image, hint_char=""):
        tensor = self.preprocess_image(image)
        outputs = self.session.run(None, {self.input_name: tensor})
        if not outputs:
            return OcrTextResult("")
        return self.decode_prediction(outputs[0])

    def preprocess_image(self, image):
        import cv2

        img_c, img_h, img_w = self.image_shape
        if img_c != 3:
            raise RuntimeError(f"暂不支持非 3 通道 OCR 输入: {self.image_shape}")

        array = self.np.array(image.convert("RGB"))
        if self.image_mode.upper() == "BGR":
            array = array[:, :, ::-1]

        h, w = array.shape[:2]
        if h <= 0 or w <= 0:
            raise RuntimeError(f"OCR 输入图像尺寸无效: {w}x{h}")

        ratio = w / float(h)
        max_wh_ratio = max(img_w / float(img_h), ratio)
        target_w = min(self.max_img_width, int(img_h * max_wh_ratio))
        resized_w = min(target_w, max(1, int(round(img_h * ratio))))
        resized = cv2.resize(array, (resized_w, img_h))
        resized = resized.astype("float32")
        resized = resized.transpose((2, 0, 1)) / 255.0
        resized -= 0.5
        resized /= 0.5

        padded = self.np.zeros((img_c, img_h, target_w), dtype=self.np.float32)
        padded[:, :, :resized_w] = resized
        return self.np.expand_dims(padded, axis=0)

    def decode_prediction(self, prediction):
        preds = self.np.array(prediction)
        if preds.ndim == 2:
            preds = self.np.expand_dims(preds, axis=0)
        if preds.ndim != 3:
            raise RuntimeError(f"OCR ONNX 输出维度无效: {preds.shape}")

        pred = preds[0]
        token_ids = pred.argmax(axis=-1)
        token_scores = pred.max(axis=-1)
        chars = []
        scores = []
        previous = None
        for token_id, score in zip(token_ids, token_scores, strict=False):
            token_id = int(token_id)
            if token_id == 0 or token_id == previous:
                previous = token_id
                continue
            previous = token_id
            if token_id >= len(self.characters):
                continue
            chars.append(self.characters[token_id])
            scores.append(float(score))

        confidence = sum(scores) / len(scores) if scores else 0.0
        return OcrTextResult("".join(chars), confidence)


class FontGlyphRenderer:
    def __init__(self, font_bytes, font_path, options=None):
        options = options or {}
        self.font_path = font_path
        self.font_size = int(options.get("glyph_font_size") or 128)
        self.padding = int(options.get("glyph_padding") or 32)
        self.font = ImageFont.truetype(BytesIO(self._normalize_font_bytes(font_bytes)), self.font_size)

    def _normalize_font_bytes(self, font_bytes):
        try:
            font = TTFont(BytesIO(font_bytes))
            if font.flavor:
                font.flavor = None
                stream = BytesIO()
                font.save(stream)
                return stream.getvalue()
        except Exception:
            pass
        return font_bytes

    def render(self, char):
        probe = Image.new("L", (self.font_size * 3, self.font_size * 3), 255)
        draw = ImageDraw.Draw(probe)
        bbox = draw.textbbox((0, 0), char, font=self.font)
        width = max(1, bbox[2] - bbox[0]) + self.padding * 2
        height = max(1, bbox[3] - bbox[1]) + self.padding * 2
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        draw.text(
            (self.padding - bbox[0], self.padding - bbox[1]),
            char,
            font=self.font,
            fill=0,
        )
        return image.convert("RGB")


def iter_onnx_ocr_model_dir_candidates(options=None):
    options = options or {}
    explicit = options.get("onnx_model_dir") or os.environ.get("EPUB_TOOL_OCR_ONNX_MODEL_DIR")
    if explicit:
        yield os.path.abspath(explicit)

    if getattr(sys, "frozen", False):
        executable_dir = os.path.dirname(os.path.abspath(sys.executable))
        yield os.path.join(
            os.path.dirname(executable_dir),
            "bundle-resources",
            "ocr-models",
            ONNX_OCR_MODEL_NAME,
        )
        yield os.path.join(
            os.path.dirname(executable_dir),
            "ocr-models",
            ONNX_OCR_MODEL_NAME,
        )

    cwd = os.path.abspath(os.getcwd())
    yield os.path.join(
        cwd,
        "src-tauri",
        "bundle-resources",
        "ocr-models",
        ONNX_OCR_MODEL_NAME,
    )

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    yield os.path.join(
        repo_root,
        "src-tauri",
        "bundle-resources",
        "ocr-models",
        ONNX_OCR_MODEL_NAME,
    )


def resolve_onnx_ocr_model_dir(options=None, required=True):
    for candidate in iter_onnx_ocr_model_dir_candidates(options):
        if os.path.isdir(candidate) and os.path.isfile(resolve_onnx_model_path(candidate)):
            return candidate
    if not required:
        return None
    raise RuntimeError(
        f"未找到内置 ONNX OCR 模型目录 {ONNX_OCR_MODEL_NAME}。"
        "请确认已提交的 ONNX 模型资源存在，或按构建文档的维护流程重新生成模型。"
    )


def resolve_onnx_model_path(model_dir):
    explicit = os.path.join(model_dir, ONNX_MODEL_FILE_NAME)
    if os.path.isfile(explicit):
        return explicit
    for name in os.listdir(model_dir) if os.path.isdir(model_dir) else []:
        if name.lower().endswith(".onnx"):
            return os.path.join(model_dir, name)
    return explicit


def resolve_onnx_ocr_config_path(model_dir, options=None):
    options = options or {}
    explicit = options.get("onnx_config_path")
    if explicit and os.path.isfile(explicit):
        return explicit
    bundled = os.path.join(model_dir, "inference.yml")
    if os.path.isfile(bundled):
        return bundled
    raise RuntimeError(f"未找到 OCR 配置文件: {model_dir}")


def _find_transform_config(config, op_name):
    preprocess = config.get("PreProcess") or {}
    for transform in preprocess.get("transform_ops") or []:
        if not isinstance(transform, dict):
            continue
        op_config = transform.get(op_name)
        if isinstance(op_config, dict):
            return op_config
    return {}


def load_text_recognition_config(config_path):
    try:
        import yaml
    except Exception as exc:
        raise RuntimeError("OCR 配置解析需要 PyYAML，请先安装 base 运行依赖。") from exc

    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    resize_config = _find_transform_config(config, "RecResizeImg")
    image_shape = [int(value) for value in resize_config.get("image_shape") or []]
    if len(image_shape) != 3:
        image_shape = [3, 48, 320]

    postprocess = config.get("PostProcess") or {}
    character_dict = [str(value) for value in postprocess.get("character_dict") or []]
    if not character_dict:
        raise RuntimeError(f"OCR 配置缺少 character_dict: {config_path}")

    decode_config = _find_transform_config(config, "DecodeImage")
    img_mode = str(decode_config.get("img_mode") or "BGR")
    return {
        "image_shape": image_shape,
        "character_dict": character_dict,
        "img_mode": img_mode,
    }


def create_ocr_backend(options=None):
    return OnnxGlyphOcrBackend(options)


class FontDecrypt:
    def __init__(
        self,
        epub_path,
        output_path=None,
        target_font_families=None,
        ocr_backend=None,
        ocr_options=None,
    ):
        if not os.path.exists(epub_path):
            raise Exception("EPUB文件不存在")

        self.epub_path = os.path.normpath(epub_path)
        self.epub = zipfile.ZipFile(epub_path)
        if output_path:
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
            os.path.basename(self.epub_path).replace(".epub", "_font_decrypt.epub"),
        )
        if os.path.exists(self.file_write_path):
            os.remove(self.file_write_path)

        self.htmls = []
        self.css = []
        self.fonts = []
        self.ori_files = []
        self.font_to_font_family_mapping = {}
        self.css_selector_to_font_mapping = {}
        self.font_to_char_mapping = {}
        self.font_to_replace_mapping = {}
        self.target_font_families = (
            {
                item.strip().strip("'\"").lower()
                for item in target_font_families
                if item and item.strip()
            }
            if target_font_families
            else None
        )
        self.ocr_backend = ocr_backend
        self.ocr_options = ocr_options or {}
        self.target_epub = None
        self.opf_path = None
        self._init_opf_path()
        self._validate_opf_safely()

        for file in self.epub.namelist():
            if file.lower().endswith(".html") or file.lower().endswith(".xhtml"):
                self.htmls.append(file)
            else:
                self.ori_files.append(file)
                if file.lower().endswith(".css"):
                    self.css.append(file)
                elif file.lower().endswith((".ttf", ".otf", ".woff")):
                    self.fonts.append(file)

    def _decode_xml_bytes(self, data, default="utf-8"):
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
                return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode(enc))
            except UnicodeDecodeError:
                continue
        try:
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("gb18030"))
        except UnicodeDecodeError:
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data.decode("latin-1"))

    def _read_xml_text(self, zip_path):
        try:
            data = self.epub.read(zip_path)
        except KeyError:
            raise FileNotFoundError(f"zip内缺少XML文件: {zip_path}")
        return self._decode_xml_bytes(data)

    def _sanitize_attr_value(self, value):
        value = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[a-zA-Z][\w.-]*;)", "&amp;", value)
        value = value.replace("<", "&lt;").replace(">", "&gt;")
        return value

    def _sanitize_xml_attr_text(self, xml_text):
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

    def _parse_xml_safe(self, xml_text, label):
        try:
            return ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as err:
            sanitized = self._sanitize_xml_attr_text(xml_text)
            if sanitized == xml_text:
                raise err
            return ElementTree.fromstring(sanitized)

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
            logger.write(f"opf_malformed_fallback_used: decrypt_font ({e})")

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

    def parse_css_selector_mapping(self, css_text, mapping):
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

    def find_local_fonts_mapping(self):
        mapping = self.build_font_name_to_file_mapping()
        for css in self.css:
            try:
                content = self.epub.read(css).decode("utf-8")
                rules = parse_stylesheet(content)
            except Exception:
                continue
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
            try:
                content = self.epub.read(css).decode("utf-8")
            except Exception:
                continue
            self.parse_css_selector_mapping(content, mapping)

        for one_html in self.htmls:
            try:
                html_content = self.epub.read(one_html).decode("utf-8")
            except Exception:
                continue
            soup = BeautifulSoup(html_content, "html.parser")
            for style_tag in soup.find_all("style"):
                css_text = style_tag.get_text() or ""
                if css_text.strip():
                    self.parse_css_selector_mapping(css_text, mapping)

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

    def iter_direct_text_nodes(self, tag):
        for child in tag.children:
            if not isinstance(child, NavigableString):
                continue
            if isinstance(child, Comment):
                continue
            if not child.strip():
                continue
            yield child

    def find_char_mapping(self):
        mapping = {}
        for one_html in self.htmls:
            try:
                content = self.epub.read(one_html).decode("utf-8")
            except Exception:
                continue
            soup = BeautifulSoup(content, "html.parser")
            for css_selector, font_file in self.css_selector_to_font_mapping.items():
                try:
                    elements = soup.select(css_selector)
                except Exception:
                    continue

                text_contents = []
                for element in elements:
                    text_contents.extend(
                        text_node.strip()
                        for text_node in self.iter_direct_text_nodes(element)
                    )
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
                text = "".join(
                    text_node.strip()
                    for text_node in self.iter_direct_text_nodes(tag)
                )
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
        logger.write(f"字体文件到混淆字符映射: {self.font_to_char_mapping}")

    def should_ocr_char(self, char):
        if not char or char.isspace():
            return False
        category = unicodedata.category(char)
        if category.startswith("C") and category != "Co":
            return False
        return category.startswith(("L", "N")) or category in ("Co", "So")

    def clean_text(self):
        for key in list(self.font_to_char_mapping.keys()):
            text = self.font_to_char_mapping[key]
            self.font_to_char_mapping[key] = self.remove_duplicates(
                "".join(char for char in text if self.should_ocr_char(char))
            )
        logger.write(f"清理后的待OCR字符: {self.font_to_char_mapping}")

    def get_ocr_backend(self):
        if self.ocr_backend is None:
            self.ocr_backend = create_ocr_backend(self.ocr_options)
        return self.ocr_backend

    def normalize_ocr_text(self, text):
        chars = [char for char in (text or "").strip() if not char.isspace()]
        return "".join(chars)

    def build_ocr_mapping(self):
        backend = self.get_ocr_backend()
        threshold = self.ocr_options.get("min_ocr_confidence")
        threshold = float(threshold) if threshold is not None else None
        total_chars = sum(len(chars) for chars in self.font_to_char_mapping.values())
        processed_count = 0

        for font_path, chars in self.font_to_char_mapping.items():
            if not chars:
                self.font_to_replace_mapping[font_path] = {}
                continue

            renderer = FontGlyphRenderer(
                self.epub.read(font_path),
                font_path,
                self.ocr_options,
            )
            replace_table = {}
            for char in chars:
                processed_count += 1
                progress_text = format_ocr_progress(processed_count, total_chars)
                try:
                    image = renderer.render(char)
                    result = backend.recognize(image, hint_char=char)
                    text = self.normalize_ocr_text(result.text)
                    if not text:
                        logger.write(
                            f"字体{font_path}字符 U+{ord(char):04X} OCR 为空，跳过{progress_text}"
                        )
                        continue
                    if len(text) != 1:
                        logger.write(
                            f"字体{font_path}字符 U+{ord(char):04X} OCR 结果不是单字: {text}{progress_text}"
                        )
                        continue
                    if (
                        threshold is not None
                        and result.confidence is not None
                        and result.confidence < threshold
                    ):
                        logger.write(
                            f"字体{font_path}字符 U+{ord(char):04X} OCR 置信度过低: {result.confidence}{progress_text}"
                        )
                        continue
                    replace_table[char] = text
                    confidence_text = (
                        f"，置信度 {result.confidence:.4f}"
                        if result.confidence is not None
                        else ""
                    )
                    logger.write(
                        f"字体{font_path}字符 U+{ord(char):04X} -> {text}{confidence_text}{progress_text}"
                    )
                except Exception as exc:
                    logger.write(
                        f"字体{font_path}字符 U+{ord(char):04X} OCR 失败: {exc}{progress_text}"
                    )
            self.font_to_replace_mapping[font_path] = replace_table

        logger.write(f"字体OCR反混淆映射: {self.font_to_replace_mapping}")

    def create_target_epub(self):
        self.target_epub = zipfile.ZipFile(
            self.file_write_path,
            "w",
            zipfile.ZIP_STORED,
        )

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

    def protect_escaped_angle_entities(self, html_text):
        placeholder_map = {}

        def create_unique_placeholder(index):
            while True:
                placeholder = f"__EPUB_TOOL_ESCAPED_ANGLE_{uuid.uuid4().hex}_{index}__"
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

    def replace_text_node(self, text_node, replace_table):
        if not replace_table:
            return
        trans_table = str.maketrans(replace_table)
        text_node.replace_with(text_node.translate(trans_table))

    def write_epub(self):
        self.create_target_epub()
        if "mimetype" in self.epub.namelist():
            self.target_epub.writestr(
                "mimetype",
                self.epub.read("mimetype"),
                zipfile.ZIP_STORED,
            )

        for one_html in self.htmls:
            content = self.epub.read(one_html).decode("utf-8")
            protected_content, placeholder_map = self.protect_escaped_angle_entities(content)
            soup = BeautifulSoup(protected_content, "html.parser")

            for css_selector, font_file in self.css_selector_to_font_mapping.items():
                replace_table = self.font_to_replace_mapping.get(font_file, {})
                if not replace_table:
                    continue
                try:
                    selector_tags = soup.select(css_selector)
                except Exception:
                    continue
                for tag in selector_tags:
                    for text_node in list(self.iter_direct_text_nodes(tag)):
                        self.replace_text_node(text_node, replace_table)

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
                replace_table = self.font_to_replace_mapping.get(font_file, {})
                if not replace_table:
                    continue
                for text_node in list(self.iter_direct_text_nodes(tag)):
                    self.replace_text_node(text_node, replace_table)

            formatted_html = soup.decode(formatter="minimal")
            restored_html = self.restore_escaped_angle_entities(formatted_html, placeholder_map)
            self.target_epub.writestr(
                one_html,
                restored_html.encode("utf-8"),
                zipfile.ZIP_DEFLATED,
            )

        for item in self.ori_files:
            if item != "mimetype" and item in self.epub.namelist():
                self.target_epub.writestr(item, self.epub.read(item), zipfile.ZIP_DEFLATED)
        self.close_file()
        logger.write(f"EPUB文件处理完成，输出文件路径: {self.file_write_path}")


def run_epub_font_decrypt(
    epub_path,
    output_path=None,
    target_font_families=None,
    ocr_options=None,
):
    logger.write(f"\n正在尝试OCR反混淆EPUB字体: {epub_path}")
    fd = FontDecrypt(
        epub_path,
        output_path,
        target_font_families=target_font_families,
        ocr_options=ocr_options,
    )
    if len(fd.fonts) == 0:
        logger.write("没有找到字体文件，退出")
        fd.close_file()
        return "skip"
    logger.write(f"此EPUB文件包含{len(fd.fonts)}个字体文件: {', '.join(fd.fonts)}")
    if fd.target_font_families:
        logger.write("本次目标字体 family 列表:")
        for font_family in sorted(fd.target_font_families):
            logger.write(f" - {font_family}")
    else:
        logger.write("未指定目标字体 family，将按规则处理全部可匹配字体")

    try:
        fd.get_mapping()
        fd.clean_text()
        if not any(fd.font_to_char_mapping.values()):
            logger.write("没有找到需要OCR反混淆的字符，跳过")
            fd.close_file()
            return "skip"
        fd.build_ocr_mapping()
        if not any(mapping for mapping in fd.font_to_replace_mapping.values()):
            logger.write("没有生成可用OCR替换映射，跳过")
            fd.close_file()
            return "skip"
        fd.write_epub()
        logger.write("EPUB字体OCR反混淆成功")
    except Exception as e:
        logger.write(f"EPUB字体OCR反混淆失败，错误信息: {e}")
        traceback.print_exc()
        fd.close_file()
        fd.fail_del_target()
        return e
    return 0
