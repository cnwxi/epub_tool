import codecs
import hashlib
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
OCR_PASSTHROUGH_PUNCTUATION_CHARS = frozenset(
    "。，、；：？！“”‘’（）《》〈〉【】〔〕…—·"
)
OCR_PERIOD_ALIASES = frozenset({".", "．", "｡"})
OCR_ZERO_PERIOD_ALIAS = "0"
OCR_HANGUL_OBFUSCATION_RANGE = (0xAC00, 0xD7AF)
OCR_OBFUSCATION_EAST_ASIAN_WIDTHS = frozenset({"W", "F"})
DEFAULT_MIN_OCR_CONFIDENCE = 0.8
OCR_CHAR_POLICY_STRICT = "strict"
OCR_CHAR_POLICY_COMPATIBLE = "compatible"
OCR_CHAR_POLICY_ALIASES = {
    "external": OCR_CHAR_POLICY_COMPATIBLE,
}
OCR_CHAR_POLICIES = frozenset({OCR_CHAR_POLICY_STRICT, OCR_CHAR_POLICY_COMPATIBLE})
OCR_FAILED = "OCR_FAILED"
OCR_EMPTY = "OCR_EMPTY"
OCR_MULTI_CHAR = "OCR_MULTI_CHAR"
OCR_LOW_CONF = "OCR_LOW_CONF"
OCR_EXCEPTION = "OCR_EXCEPTION"
OCR_FAILURE_IMAGE_DIR = "Images/ocr-failures"
OCR_FAILURE_STYLE_CLASS = "epub-tool-ocr-failure-style"
OCR_FAILURE_STYLE_CSS = (
    ".ocr-failure{font-size:1em;white-space:nowrap;}"
    ".ocr-failure-glyph{height:1em;width:auto;vertical-align:-0.15em;}"
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
        img_c, img_h, img_w = self.image_shape
        if img_c != 3:
            raise RuntimeError(f"暂不支持非 3 通道 OCR 输入: {self.image_shape}")

        rgb_image = image.convert("RGB")
        w, h = rgb_image.size
        if h <= 0 or w <= 0:
            raise RuntimeError(f"OCR 输入图像尺寸无效: {w}x{h}")

        ratio = w / float(h)
        max_wh_ratio = max(img_w / float(img_h), ratio)
        target_w = min(self.max_img_width, int(img_h * max_wh_ratio))
        resized_w = min(target_w, max(1, int(round(img_h * ratio))))
        resample_filter = getattr(Image, "Resampling", Image).BILINEAR
        resized_image = rgb_image.resize((resized_w, img_h), resample_filter)
        resized = self.np.array(resized_image)
        if self.image_mode.upper() == "BGR":
            resized = resized[:, :, ::-1]
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
        self.small_glyph_font_size = int(
            options.get("glyph_small_font_size")
            or max(self.font_size * 2, self.font_size + 96)
        )
        self.small_glyph_padding = int(
            options.get("glyph_small_padding") or max(8, self.padding // 2)
        )
        self.small_glyph_threshold = float(options.get("glyph_small_threshold") or 0.42)
        self.normalized_font_bytes = self._normalize_font_bytes(font_bytes)
        self.font_cache = {}
        self.font = self._font_for_size(self.font_size)

    def _font_for_size(self, size):
        if size not in self.font_cache:
            self.font_cache[size] = ImageFont.truetype(
                BytesIO(self.normalized_font_bytes),
                size,
            )
        return self.font_cache[size]

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

    def measure_text_bbox(self, char, font, font_size):
        probe = Image.new("L", (font_size * 3, font_size * 3), 255)
        draw = ImageDraw.Draw(probe)
        return draw.textbbox((0, 0), char, font=font)

    def is_small_glyph_bbox(self, bbox, font_size):
        glyph_width = max(1, bbox[2] - bbox[0])
        glyph_height = max(1, bbox[3] - bbox[1])
        threshold = max(1, font_size * self.small_glyph_threshold)
        return glyph_width <= threshold or glyph_height <= threshold

    @staticmethod
    def get_ink_bbox(image):
        gray = image.convert("L")
        ink_mask = gray.point(lambda pixel: 255 if pixel < 250 else 0)
        return ink_mask.getbbox()

    @classmethod
    def is_period_like_image(cls, image):
        ink_bbox = cls.get_ink_bbox(image)
        if not ink_bbox:
            return False

        ink_width = max(1, ink_bbox[2] - ink_bbox[0])
        ink_height = max(1, ink_bbox[3] - ink_bbox[1])
        image_width = max(1, image.width)
        image_height = max(1, image.height)
        aspect_ratio = ink_width / ink_height

        # 仅把墨迹占比很小且接近正方形的小字形视为句号候选，避免数字 0 / 圈号误归一。
        return (
            ink_width <= image_width * 0.38
            and ink_height <= image_height * 0.46
            and 0.55 <= aspect_ratio <= 1.6
        )

    def render_spec(self, char):
        font = self.font
        bbox = self.measure_text_bbox(char, font, self.font_size)
        padding = self.padding
        if (
            self.small_glyph_font_size > self.font_size
            and self.is_small_glyph_bbox(bbox, self.font_size)
        ):
            font = self._font_for_size(self.small_glyph_font_size)
            bbox = self.measure_text_bbox(char, font, self.small_glyph_font_size)
            padding = self.small_glyph_padding
        return font, bbox, padding

    def render(self, char):
        font, bbox, padding = self.render_spec(char)
        width = max(1, bbox[2] - bbox[0]) + padding * 2
        height = max(1, bbox[3] - bbox[1]) + padding * 2
        image = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(image)
        draw.text(
            (padding - bbox[0], padding - bbox[1]),
            char,
            font=font,
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
        self.css_selector_font_rules = []
        self._css_selector_rule_order = 0
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
        self.font_to_ocr_failure_mapping = {}
        self.ocr_failure_image_bytes = {}
        self.font_cmap_cache = {}
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
        self.css_selector_font_rules = []
        self._css_selector_rule_order = 0
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
            try:
                content = self.epub.read(one_html).decode("utf-8")
            except Exception:
                continue
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
        logger.write(f"字体文件到混淆字符映射: {self.font_to_char_mapping}")

    def is_encrypt_obfuscated_char(self, char):
        if is_ascii_latin_alnum(char) or is_fullwidth_latin_alnum(char):
            return True
        category = unicodedata.category(char)
        east_asian_width = unicodedata.east_asian_width(char)
        return (
            category.startswith(("L", "N"))
            and east_asian_width in OCR_OBFUSCATION_EAST_ASIAN_WIDTHS
        )

    def is_ocr_obfuscation_hint_char(self, char):
        if not char:
            return False
        codepoint = ord(char)
        return (
            unicodedata.category(char) == "Co"
            or OCR_HANGUL_OBFUSCATION_RANGE[0]
            <= codepoint
            <= OCR_HANGUL_OBFUSCATION_RANGE[1]
        )

    def get_ocr_char_policy(self):
        raw_policy = str(
            getattr(self, "ocr_options", {}).get(
                "ocr_char_policy",
                OCR_CHAR_POLICY_STRICT,
            )
            or OCR_CHAR_POLICY_STRICT
        ).strip().lower()
        policy = OCR_CHAR_POLICY_ALIASES.get(raw_policy, raw_policy)
        if policy in OCR_CHAR_POLICIES:
            return policy
        logger.write(f"未知 OCR 字符筛选策略 {raw_policy}，回退到 strict")
        return OCR_CHAR_POLICY_STRICT

    def is_ocr_char_common_exclusion(self, char):
        if not char or char.isspace():
            return True
        category = unicodedata.category(char)
        if category.startswith("C") and category != "Co":
            return True
        if char in OCR_PASSTHROUGH_PUNCTUATION_CHARS:
            return True
        return False

    def is_compatible_ocr_candidate_char(self, char):
        if self.is_ocr_char_common_exclusion(char):
            return False
        if ord(char) < 0x80:
            return False
        return True

    def should_ocr_char(self, char):
        if self.is_ocr_char_common_exclusion(char):
            return False
        if self.is_ocr_obfuscation_hint_char(char):
            return True
        if self.is_encrypt_obfuscated_char(char):
            return True
        if self.get_ocr_char_policy() == OCR_CHAR_POLICY_COMPATIBLE:
            return self.is_compatible_ocr_candidate_char(char)
        return False

    def load_font_cmap(self, font_path):
        if not hasattr(self, "font_cmap_cache"):
            self.font_cmap_cache = {}
        if font_path in self.font_cmap_cache:
            return self.font_cmap_cache[font_path]
        try:
            font = TTFont(BytesIO(self.epub.read(font_path)))
            cmap = font.getBestCmap() or {}
        except Exception:
            cmap = None
        self.font_cmap_cache[font_path] = cmap
        return cmap

    def filter_text_by_font_cmap(self, font_path, text):
        if not text or not hasattr(self, "epub"):
            return text
        cmap = self.load_font_cmap(font_path)
        if cmap is None:
            return text
        supported_chars = []
        missing_chars = []
        for char in text:
            if ord(char) in cmap:
                supported_chars.append(char)
            else:
                missing_chars.append(char)
        if missing_chars:
            logger.write(
                f"字体文件{font_path}缺少待 OCR 字符，已跳过: "
                f"{self.remove_duplicates(''.join(missing_chars))}"
            )
        return "".join(supported_chars)

    def clean_text(self):
        for key in list(self.font_to_char_mapping.keys()):
            text = self.font_to_char_mapping[key]
            ocr_text = self.remove_duplicates(
                "".join(char for char in text if self.should_ocr_char(char))
            )
            self.font_to_char_mapping[key] = self.filter_text_by_font_cmap(key, ocr_text)
        logger.write(f"清理后的待OCR字符: {self.font_to_char_mapping}")

    def get_ocr_backend(self):
        if self.ocr_backend is None:
            self.ocr_backend = create_ocr_backend(self.ocr_options)
        return self.ocr_backend

    def normalize_ocr_punctuation(self, text, hint_char, period_like_glyph=False):
        if not self.is_ocr_obfuscation_hint_char(hint_char):
            return text
        if text in OCR_PERIOD_ALIASES:
            return "。"
        if text == OCR_ZERO_PERIOD_ALIAS and period_like_glyph:
            return "。"
        return text

    def normalize_ocr_text(self, text, hint_char=None, period_like_glyph=False):
        chars = [char for char in (text or "").strip() if not char.isspace()]
        normalized = "".join(chars)
        if len(normalized) == 1:
            return self.normalize_ocr_punctuation(
                normalized,
                hint_char,
                period_like_glyph=period_like_glyph,
            )
        return normalized

    def get_min_ocr_confidence(self):
        threshold = self.ocr_options.get("min_ocr_confidence", DEFAULT_MIN_OCR_CONFIDENCE)
        if threshold is None:
            return DEFAULT_MIN_OCR_CONFIDENCE
        return float(threshold)

    def format_ocr_codepoint(self, char):
        return f"U+{ord(char):04X}"

    def format_ocr_codepoint_filename_part(self, char):
        return f"U-{ord(char):04X}"

    def build_ocr_failed_placeholder(self, char, status_code=OCR_FAILED):
        return f"[{self.format_ocr_codepoint(char)} {status_code}]"

    def build_ocr_failure_image_path(self, font_hash, char, status_code):
        filename = (
            f"{font_hash}_{self.format_ocr_codepoint_filename_part(char)}_"
            f"{status_code}.png"
        )
        opf_dir = posixpath.dirname(getattr(self, "opf_path", "") or "")
        return posixpath.normpath(posixpath.join(opf_dir, OCR_FAILURE_IMAGE_DIR, filename))

    def encode_ocr_failure_image(self, image):
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def build_ocr_failure_image_record(self, font_hash, char, status_code, image):
        image_path = self.build_ocr_failure_image_path(font_hash, char, status_code)
        if not hasattr(self, "ocr_failure_image_bytes"):
            self.ocr_failure_image_bytes = {}
        self.ocr_failure_image_bytes.setdefault(
            image_path,
            self.encode_ocr_failure_image(image),
        )
        return {
            "image_path": image_path,
            "image_alt": f"{self.format_ocr_codepoint(char)} {char} {status_code}",
        }

    def record_ocr_failure(
        self,
        failure_table,
        char,
        status_code,
        reason,
        progress_text,
        font_path=None,
        font_hash=None,
        glyph_image=None,
    ):
        placeholder = self.build_ocr_failed_placeholder(char, status_code)
        failure_record = {
            "codepoint": self.format_ocr_codepoint(char),
            "original_char": char,
            "status_code": status_code,
            "font_path": font_path,
            "reason": reason,
            "placeholder": placeholder,
        }
        if font_hash and glyph_image is not None:
            failure_record.update(
                self.build_ocr_failure_image_record(
                    font_hash,
                    char,
                    status_code,
                    glyph_image,
                )
            )
        failure_table[char] = failure_record
        logger.write(
            f"字体字符 U+{ord(char):04X} OCR 未替换，状态: {status_code}，原因: {reason}，"
            f"使用占位符 {placeholder}{progress_text}"
        )
        return placeholder

    def build_ocr_mapping(self):
        backend = self.get_ocr_backend()
        threshold = self.get_min_ocr_confidence()
        total_chars = sum(len(chars) for chars in self.font_to_char_mapping.values())
        processed_count = 0

        for font_path, chars in self.font_to_char_mapping.items():
            if not chars:
                self.font_to_replace_mapping[font_path] = {}
                self.font_to_ocr_failure_mapping[font_path] = {}
                continue

            font_bytes = self.epub.read(font_path)
            font_hash = hashlib.sha1(font_bytes).hexdigest()[:8]
            renderer = FontGlyphRenderer(font_bytes, font_path, self.ocr_options)
            replace_table = {}
            failure_table = {}
            for char in chars:
                processed_count += 1
                progress_text = format_ocr_progress(processed_count, total_chars)
                image = None
                try:
                    image = renderer.render(char)
                    result = backend.recognize(image, hint_char=char)
                    period_like_glyph = renderer.is_period_like_image(image)
                    text = self.normalize_ocr_text(
                        result.text,
                        hint_char=char,
                        period_like_glyph=period_like_glyph,
                    )
                    if not text:
                        replace_table[char] = self.record_ocr_failure(
                            failure_table,
                            char,
                            OCR_EMPTY,
                            f"OCR 为空，字体 {font_path}",
                            progress_text,
                            font_path=font_path,
                            font_hash=font_hash,
                            glyph_image=image,
                        )
                        continue
                    if len(text) != 1:
                        replace_table[char] = self.record_ocr_failure(
                            failure_table,
                            char,
                            OCR_MULTI_CHAR,
                            f"OCR 结果不是单字: {text}，字体 {font_path}",
                            progress_text,
                            font_path=font_path,
                            font_hash=font_hash,
                            glyph_image=image,
                        )
                        continue
                    if result.confidence is not None and result.confidence < threshold:
                        replace_table[char] = self.record_ocr_failure(
                            failure_table,
                            char,
                            OCR_LOW_CONF,
                            (
                                f"OCR 置信度过低: {result.confidence:.4f} "
                                f"< {threshold:.4f}，字体 {font_path}"
                            ),
                            progress_text,
                            font_path=font_path,
                            font_hash=font_hash,
                            glyph_image=image,
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
                    replace_table[char] = self.record_ocr_failure(
                        failure_table,
                        char,
                        OCR_EXCEPTION,
                        f"OCR 异常: {exc}，字体 {font_path}",
                        progress_text,
                        font_path=font_path,
                        font_hash=font_hash,
                        glyph_image=image,
                    )
            self.font_to_replace_mapping[font_path] = replace_table
            self.font_to_ocr_failure_mapping[font_path] = failure_table

        logger.write(f"字体OCR反混淆映射: {self.font_to_replace_mapping}")
        logger.write(f"字体OCR失败字符映射: {self.font_to_ocr_failure_mapping}")

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

    def build_relative_href(self, from_path, to_path):
        from_dir = posixpath.dirname(from_path) or "."
        return posixpath.relpath(to_path, from_dir)

    def build_ocr_failure_span(self, soup, html_path, failure_record):
        span = soup.new_tag(
            "span",
            attrs={
                "class": "ocr-failure",
                "data-codepoint": failure_record.get("codepoint", ""),
                "data-original-char": failure_record.get("original_char", ""),
                "data-status": failure_record.get("status_code", ""),
            },
        )
        if failure_record.get("font_path"):
            span["data-font-path"] = failure_record["font_path"]
        if failure_record.get("reason"):
            span["data-reason"] = failure_record["reason"]

        image_path = failure_record.get("image_path")
        if image_path:
            image = soup.new_tag(
                "img",
                attrs={
                    "class": "ocr-failure-glyph",
                    "src": self.build_relative_href(html_path, image_path),
                    "alt": failure_record.get("image_alt")
                    or failure_record.get("placeholder", ""),
                },
            )
            span.append(image)
        return span

    def replace_text_node(self, soup, html_path, text_node, replace_table, failure_table=None):
        if not replace_table:
            return False

        failure_table = failure_table or {}
        text = str(text_node)
        if not any(char in replace_table for char in text):
            return False

        fragments = []
        text_buffer = []
        inserted_failure_markup = False

        def flush_text_buffer():
            if text_buffer:
                fragments.append(NavigableString("".join(text_buffer)))
                text_buffer.clear()

        for char in text:
            if char not in replace_table:
                text_buffer.append(char)
                continue

            flush_text_buffer()
            if char in failure_table:
                fragments.append(
                    self.build_ocr_failure_span(soup, html_path, failure_table[char])
                )
                inserted_failure_markup = True
            else:
                fragments.append(NavigableString(replace_table[char]))

        flush_text_buffer()
        for fragment in fragments:
            text_node.insert_before(fragment)
        text_node.extract()
        return inserted_failure_markup

    def get_decrypt_target_font_files(self):
        return set(self.css_selector_to_font_mapping.values()) | set(
            self.font_to_char_mapping.keys()
        )

    def extract_xml_attr(self, text, attr_name):
        match = re.search(
            rf"\b{re.escape(attr_name)}\s*=\s*([\"'])(.*?)\1",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return match.group(2) if match else None

    def iter_css_url_book_paths(self, css_path, value_text):
        for raw_url in re.findall(r"url\((.*?)\)", value_text or "", flags=re.IGNORECASE):
            url = raw_url.strip().strip("'\"")
            if not url or "://" in url or url.lower().startswith("data:"):
                continue
            book_path = self.resolve_book_path(css_path, url)
            if book_path:
                yield book_path

    def parse_font_face_reference(self, css_path, font_face_body):
        declarations = parse_declaration_list(font_face_body)
        family_names = set()
        source_paths = set()
        for declaration in declarations:
            if declaration.type != "declaration":
                continue
            if declaration.lower_name == "font-family":
                for candidate in self.extract_font_candidates_from_declaration(declaration):
                    normalized = self.normalize_font_name(candidate)
                    if normalized:
                        family_names.add(normalized)
            elif declaration.lower_name == "src":
                source_paths.update(
                    self.iter_css_url_book_paths(css_path, serialize(declaration.value))
                )
        return family_names, source_paths

    def get_decrypt_target_font_families(self, target_font_files=None):
        target_font_files = target_font_files or self.get_decrypt_target_font_files()
        target_font_families = set(self.target_font_families or set())
        for family_name, font_file in self.font_to_font_family_mapping.items():
            if font_file in target_font_files:
                target_font_families.add(family_name)

        for css_path in self.css:
            try:
                css_text = self.epub.read(css_path).decode("utf-8")
            except Exception:
                continue
            for match in re.finditer(
                r"@font-face\s*\{(?P<body>[^{}]*)\}",
                css_text,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                family_names, source_paths = self.parse_font_face_reference(
                    css_path,
                    match.group("body"),
                )
                if source_paths & target_font_files:
                    target_font_families.update(family_names)
        return target_font_families

    def split_css_family_list(self, value):
        families = []
        current = []
        quote = None
        depth = 0
        index = 0
        while index < len(value):
            char = value[index]
            current.append(char)
            if quote:
                if char == "\\" and index + 1 < len(value):
                    index += 1
                    current.append(value[index])
                elif char == quote:
                    quote = None
            elif char in ("'", '"'):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            elif char == "," and depth == 0:
                current.pop()
                families.append("".join(current).strip())
                current = []
            index += 1
        if current:
            families.append("".join(current).strip())
        return [family for family in families if family]

    def strip_css_important(self, value):
        match = re.search(r"\s*!important\s*$", value, flags=re.IGNORECASE)
        if not match:
            return value.strip(), ""
        return value[: match.start()].strip(), value[match.start() :].strip()

    def clean_css_font_family_declarations(self, css_text, target_font_families):
        if not target_font_families:
            return css_text

        def replace_font_family(match):
            value, important = self.strip_css_important(match.group("value"))
            families = self.split_css_family_list(value)
            kept_families = [
                family
                for family in families
                if self.normalize_font_name(family) not in target_font_families
            ]
            if len(kept_families) == len(families):
                return match.group(0)
            if not kept_families:
                logger.write(
                    f"清理目标反混淆字体 font-family 引用: {match.group(0).strip()}"
                )
                return ""
            suffix = match.group("suffix") or ";"
            important_text = f" {important}" if important else ""
            cleaned = f"{match.group('prefix')}{', '.join(kept_families)}{important_text}{suffix}"
            logger.write(f"清理目标反混淆字体 font-family 引用: {cleaned.strip()}")
            return cleaned

        return re.sub(
            r"(?is)(?P<prefix>\bfont-family\s*:\s*)(?P<value>[^;{}]+)(?P<suffix>;?)",
            replace_font_family,
            css_text,
        )

    def clean_css_font_references(
        self,
        css_text,
        css_path,
        target_font_files,
        target_font_families,
    ):
        if not target_font_files and not target_font_families:
            return css_text

        def replace_font_face(match):
            family_names, source_paths = self.parse_font_face_reference(
                css_path,
                match.group("body"),
            )
            if source_paths & target_font_files or family_names & target_font_families:
                logger.write(
                    f"清理目标反混淆字体 @font-face 引用: {css_path} -> "
                    f"{sorted(family_names) or sorted(source_paths)}"
                )
                return ""
            return match.group(0)

        css_text = re.sub(
            r"\s*@font-face\s*\{(?P<body>[^{}]*)\}",
            replace_font_face,
            css_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return self.clean_css_font_family_declarations(css_text, target_font_families)

    def clean_opf_manifest_font_references(self, opf_text, target_font_files):
        if not target_font_files or not self.opf_path:
            return opf_text

        def replace_manifest_item(match):
            href = self.extract_xml_attr(match.group("attrs"), "href")
            if href and self.resolve_book_path(self.opf_path, href) in target_font_files:
                logger.write(f"清理 OPF manifest 目标反混淆字体项: {href}")
                return ""
            return match.group(0)

        return re.sub(
            r"(?is)(?P<leading>\n?[ \t]*)<item\b(?P<attrs>[^>]*)>\s*(?:</item>)?",
            replace_manifest_item,
            opf_text,
        )

    def clean_html_font_references(
        self,
        soup,
        html_path,
        target_font_files,
        target_font_families,
    ):
        for style_tag in soup.find_all("style"):
            css_text = style_tag.get_text() or ""
            cleaned_css = self.clean_css_font_references(
                css_text,
                html_path,
                target_font_files,
                target_font_families,
            )
            style_tag.string = cleaned_css

        for tag in soup.find_all(style=True):
            cleaned_style = self.clean_css_font_family_declarations(
                tag.get("style", ""),
                target_font_families,
            ).strip()
            if cleaned_style:
                tag["style"] = cleaned_style
            else:
                del tag["style"]

    def ensure_ocr_failure_style(self, soup):
        if soup.find("style", class_=OCR_FAILURE_STYLE_CLASS):
            return

        style_tag = soup.new_tag(
            "style",
            attrs={"type": "text/css", "class": OCR_FAILURE_STYLE_CLASS},
        )
        style_tag.string = OCR_FAILURE_STYLE_CSS
        head = soup.find("head")
        if head:
            head.append(style_tag)
            return
        html_tag = soup.find("html")
        if html_tag:
            html_tag.insert(0, style_tag)
            return
        soup.insert(0, style_tag)

    def escape_xml_attr(self, value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def build_opf_href(self, book_path):
        opf_dir = posixpath.dirname(getattr(self, "opf_path", "") or "") or "."
        return posixpath.relpath(book_path, opf_dir)

    def add_opf_manifest_ocr_failure_images(self, opf_text):
        image_paths = sorted(getattr(self, "ocr_failure_image_bytes", {}).keys())
        if not image_paths:
            return opf_text

        existing_ids = set(
            match.group(2)
            for match in re.finditer(
                r"\bid\s*=\s*([\"'])(.*?)\1",
                opf_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        existing_hrefs = set(
            match.group(2)
            for match in re.finditer(
                r"\bhref\s*=\s*([\"'])(.*?)\1",
                opf_text,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )

        manifest_items = []
        index = 1
        for image_path in image_paths:
            href = self.build_opf_href(image_path)
            if href in existing_hrefs:
                continue
            while True:
                item_id = f"ocr_failure_{index}"
                index += 1
                if item_id not in existing_ids:
                    existing_ids.add(item_id)
                    break
            existing_hrefs.add(href)
            manifest_items.append(
                '    <item id="{item_id}" href="{href}" media-type="image/png"/>'.format(
                    item_id=self.escape_xml_attr(item_id),
                    href=self.escape_xml_attr(href),
                )
            )

        if not manifest_items:
            return opf_text

        match = re.search(r"(?is)</manifest\s*>", opf_text)
        if not match:
            logger.write("未找到 OPF manifest，跳过 OCR 失败字形图片登记")
            return opf_text
        items_text = "\n".join(manifest_items)
        return f"{opf_text[:match.start()]}\n{items_text}\n{opf_text[match.start():]}"

    def write_ocr_failure_images(self):
        for image_path, image_bytes in sorted(
            getattr(self, "ocr_failure_image_bytes", {}).items()
        ):
            self.target_epub.writestr(image_path, image_bytes, zipfile.ZIP_DEFLATED)
            logger.write(f"写入 OCR 失败字形缩略图: {image_path}")

    def write_epub(self):
        self.create_target_epub()
        if "mimetype" in self.epub.namelist():
            self.target_epub.writestr(
                "mimetype",
                self.epub.read("mimetype"),
                zipfile.ZIP_STORED,
            )

        target_font_files = self.get_decrypt_target_font_files()
        target_font_families = self.get_decrypt_target_font_families(target_font_files)

        for one_html in self.htmls:
            content = self.epub.read(one_html).decode("utf-8")
            protected_content, placeholder_map = self.protect_escaped_angle_entities(content)
            soup = BeautifulSoup(protected_content, "html.parser")
            has_ocr_failure_markup = False
            css_font_rule_index = self.build_css_font_rule_index(soup)

            for tag in soup.find_all(True):
                font_file = self.get_effective_font_file(tag, css_font_rule_index)
                if not font_file or not self.is_target_font_file(font_file):
                    continue
                replace_table = self.font_to_replace_mapping.get(font_file, {})
                if not replace_table:
                    continue
                failure_table = self.font_to_ocr_failure_mapping.get(font_file, {})
                for text_node in list(self.iter_direct_text_nodes(tag)):
                    has_ocr_failure_markup = (
                        self.replace_text_node(
                            soup,
                            one_html,
                            text_node,
                            replace_table,
                            failure_table,
                        )
                        or has_ocr_failure_markup
                    )

            self.clean_html_font_references(
                soup,
                one_html,
                target_font_files,
                target_font_families,
            )
            if has_ocr_failure_markup:
                self.ensure_ocr_failure_style(soup)
            formatted_html = soup.decode(formatter="minimal")
            restored_html = self.restore_escaped_angle_entities(formatted_html, placeholder_map)
            self.target_epub.writestr(
                one_html,
                restored_html.encode("utf-8"),
                zipfile.ZIP_DEFLATED,
            )

        for item in self.ori_files:
            if item == "mimetype" or item not in self.epub.namelist():
                continue
            if item in getattr(self, "ocr_failure_image_bytes", {}):
                logger.write(f"跳过原始同名 OCR 失败字形图片: {item}")
                continue
            if item in target_font_files:
                logger.write(f"跳过写入目标反混淆字体文件: {item}")
                continue
            if item == self.opf_path:
                opf_text = self._decode_xml_bytes(self.epub.read(item))
                cleaned_opf = self.clean_opf_manifest_font_references(
                    opf_text,
                    target_font_files,
                )
                cleaned_opf = self.add_opf_manifest_ocr_failure_images(cleaned_opf)
                self.target_epub.writestr(item, cleaned_opf.encode("utf-8"), zipfile.ZIP_DEFLATED)
                continue
            if item in self.css:
                css_text = self.epub.read(item).decode("utf-8")
                cleaned_css = self.clean_css_font_references(
                    css_text,
                    item,
                    target_font_files,
                    target_font_families,
                )
                self.target_epub.writestr(item, cleaned_css.encode("utf-8"), zipfile.ZIP_DEFLATED)
                continue
            self.target_epub.writestr(item, self.epub.read(item), zipfile.ZIP_DEFLATED)
        self.write_ocr_failure_images()
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
