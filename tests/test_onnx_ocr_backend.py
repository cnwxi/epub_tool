import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from scripts import build_python_sidecar
from scripts import ocr_model_config
from python_backend.services.decrypt_font import (
    DEFAULT_OCR_MODEL_NAME,
    OCR_CHAR_POLICY_COMPATIBLE,
    OCR_CHAR_POLICY_STRICT,
    OCR_LOW_CONF,
    FontDecrypt,
    FontGlyphRenderer,
    OcrTextResult,
    OnnxGlyphOcrBackend,
    create_onnx_session_options,
    format_ocr_progress,
    iter_onnx_ocr_model_dir_candidates,
    load_text_recognition_config,
)


class OnnxOcrBackendTest(unittest.TestCase):
    def test_onnx_model_candidates_include_repo_resource_when_cwd_is_elsewhere(self):
        expected = (
            Path(__file__).resolve().parent.parent
            / "src-tauri"
            / "bundle-resources"
            / "ocr-models"
            / f"{DEFAULT_OCR_MODEL_NAME}_onnx"
        )

        with patch("python_backend.services.decrypt_font.os.getcwd", return_value="/tmp"):
            candidates = list(iter_onnx_ocr_model_dir_candidates())

        self.assertEqual(candidates[-1], str(expected))

    def test_format_ocr_progress_includes_count_and_percent(self):
        self.assertEqual(format_ocr_progress(3, 12), "，进度 3/12 (25.0%)")
        self.assertEqual(format_ocr_progress(0, 0), "")

    def test_onnx_session_options_suppress_runtime_warnings(self):
        import onnxruntime as ort

        session_options = create_onnx_session_options(ort)

        self.assertEqual(session_options.log_severity_level, 3)

    def test_load_text_recognition_config_reads_shape_mode_and_characters(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as file:
            file.write(
                """PreProcess:
  transform_ops:
  - DecodeImage:
      img_mode: BGR
  - RecResizeImg:
      image_shape:
      - 3
      - 48
      - 320
  - KeepKeys:
      keep_keys:
      - image
PostProcess:
  character_dict:
  - 你
  - 好
"""
            )
            config_path = file.name
        try:
            config = load_text_recognition_config(config_path)
        finally:
            os.remove(config_path)

        self.assertEqual(config["image_shape"], [3, 48, 320])
        self.assertEqual(config["img_mode"], "BGR")
        self.assertEqual(config["character_dict"], ["你", "好"])

    def test_decode_prediction_removes_ctc_blank_and_duplicates(self):
        backend = OnnxGlyphOcrBackend.__new__(OnnxGlyphOcrBackend)
        backend.np = np
        backend.characters = ["blank", "你", "好", " "]

        prediction = np.zeros((1, 5, 4), dtype=np.float32)
        prediction[0, 0, 1] = 0.9
        prediction[0, 1, 1] = 0.8
        prediction[0, 2, 0] = 0.7
        prediction[0, 3, 2] = 0.95
        prediction[0, 4, 0] = 0.6

        result = backend.decode_prediction(prediction)

        self.assertEqual(result.text, "你好")
        self.assertAlmostEqual(result.confidence, 0.925)

    def test_preprocess_image_matches_ocr_input_shape(self):
        backend = OnnxGlyphOcrBackend.__new__(OnnxGlyphOcrBackend)
        backend.np = np
        backend.image_shape = [3, 48, 320]
        backend.image_mode = "RGB"
        backend.max_img_width = 3200

        image = Image.new("RGB", (40, 80), (255, 255, 255))
        tensor = backend.preprocess_image(image)

        self.assertEqual(tensor.shape, (1, 3, 48, 320))
        self.assertEqual(tensor.dtype, np.float32)


class FontGlyphRendererTest(unittest.TestCase):
    def test_small_glyph_bbox_triggers_adaptive_rendering(self):
        renderer = FontGlyphRenderer.__new__(FontGlyphRenderer)
        renderer.small_glyph_threshold = 0.42

        self.assertTrue(renderer.is_small_glyph_bbox((0, 0, 20, 20), 128))
        self.assertTrue(renderer.is_small_glyph_bbox((0, 0, 120, 12), 128))
        self.assertFalse(renderer.is_small_glyph_bbox((0, 0, 90, 100), 128))

    def test_period_like_image_rejects_zero_sized_glyphs(self):
        period_image = Image.new("RGB", (160, 120), (255, 255, 255))
        period_draw = ImageDraw.Draw(period_image)
        period_draw.ellipse((72, 48, 92, 68), outline=0, width=3)

        zero_image = Image.new("RGB", (160, 120), (255, 255, 255))
        zero_draw = ImageDraw.Draw(zero_image)
        zero_draw.ellipse((42, 16, 118, 104), outline=0, width=6)

        self.assertTrue(FontGlyphRenderer.is_period_like_image(period_image))
        self.assertFalse(FontGlyphRenderer.is_period_like_image(zero_image))


class FontDecryptOcrTextCleanupTest(unittest.TestCase):
    def create_font_decrypt(self, policy=OCR_CHAR_POLICY_STRICT):
        font_decrypt = FontDecrypt.__new__(FontDecrypt)
        font_decrypt.ocr_options = {"ocr_char_policy": policy}
        return font_decrypt

    def test_clean_text_keeps_encrypt_font_passthrough_text_out_of_ocr(self):
        font_decrypt = self.create_font_decrypt()
        font_decrypt.font_to_char_mapping = {
            "font.ttf": "你０Ａ❶0A。？，！、；：《》（）\ue000<& \u0000",
        }

        font_decrypt.clean_text()

        self.assertEqual(font_decrypt.font_to_char_mapping["font.ttf"], "你０Ａ0A\ue000")

    def test_strict_ocr_policy_matches_encrypt_font_obfuscation_scope(self):
        font_decrypt = self.create_font_decrypt(OCR_CHAR_POLICY_STRICT)

        self.assertFalse(font_decrypt.should_ocr_char("❶"))
        self.assertTrue(font_decrypt.should_ocr_char("0"))
        self.assertTrue(font_decrypt.should_ocr_char("A"))
        self.assertTrue(font_decrypt.should_ocr_char("z"))
        self.assertTrue(font_decrypt.should_ocr_char("０"))
        self.assertTrue(font_decrypt.should_ocr_char("Ａ"))
        self.assertTrue(font_decrypt.should_ocr_char("ｚ"))
        self.assertFalse(font_decrypt.should_ocr_char("。"))
        self.assertFalse(font_decrypt.should_ocr_char("\u0000"))
        self.assertTrue(font_decrypt.should_ocr_char("你"))
        self.assertTrue(font_decrypt.should_ocr_char("\ue000"))
        self.assertTrue(font_decrypt.should_ocr_char("\ud73c"))

    def test_compatible_ocr_policy_accepts_external_visible_obfuscation_chars(self):
        font_decrypt = self.create_font_decrypt(OCR_CHAR_POLICY_COMPATIBLE)

        self.assertTrue(font_decrypt.should_ocr_char("你"))
        self.assertTrue(font_decrypt.should_ocr_char("❶"))
        self.assertTrue(font_decrypt.should_ocr_char("０"))
        self.assertTrue(font_decrypt.should_ocr_char("Ａ"))
        self.assertTrue(font_decrypt.should_ocr_char("ｚ"))
        self.assertTrue(font_decrypt.should_ocr_char("\ue000"))
        self.assertFalse(font_decrypt.should_ocr_char(" "))
        self.assertFalse(font_decrypt.should_ocr_char("\u0000"))
        self.assertFalse(font_decrypt.should_ocr_char("。"))
        self.assertTrue(font_decrypt.should_ocr_char("A"))
        self.assertTrue(font_decrypt.should_ocr_char("0"))

    def test_clean_text_uses_compatible_policy_for_external_obfuscation_chars(self):
        font_decrypt = self.create_font_decrypt(OCR_CHAR_POLICY_COMPATIBLE)
        font_decrypt.font_to_char_mapping = {
            "font.ttf": "你０Ａ❶0A。？，！、；：《》（）\ue000<& \u0000",
        }

        font_decrypt.clean_text()

        self.assertEqual(font_decrypt.font_to_char_mapping["font.ttf"], "你０Ａ❶0A\ue000")

    def test_external_ocr_policy_alias_uses_compatible_mode(self):
        font_decrypt = self.create_font_decrypt("external")

        self.assertEqual(font_decrypt.get_ocr_char_policy(), OCR_CHAR_POLICY_COMPATIBLE)
        self.assertTrue(font_decrypt.should_ocr_char("❶"))

    def test_clean_text_skips_chars_missing_from_current_font_cmap(self):
        font_decrypt = self.create_font_decrypt()
        font_decrypt.epub = object()
        font_decrypt.font_to_char_mapping = {
            "font.ttf": "你缺",
        }
        font_decrypt.load_font_cmap = lambda font_path: {ord("你"): "uni4F60"}

        font_decrypt.clean_text()

        self.assertEqual(font_decrypt.font_to_char_mapping["font.ttf"], "你")

    def test_private_use_period_alias_normalizes_to_chinese_full_stop(self):
        font_decrypt = self.create_font_decrypt()

        self.assertEqual(font_decrypt.normalize_ocr_text(".", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text("．", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text("｡", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text(".", hint_char="。"), ".")

    def test_zero_alias_only_normalizes_for_period_like_obfuscated_glyph(self):
        font_decrypt = self.create_font_decrypt()

        self.assertEqual(
            font_decrypt.normalize_ocr_text(
                "0",
                hint_char="\ud73c",
                period_like_glyph=True,
            ),
            "。",
        )
        self.assertEqual(
            font_decrypt.normalize_ocr_text(
                "0",
                hint_char="\ud73c",
                period_like_glyph=False,
            ),
            "0",
        )
        self.assertEqual(
            font_decrypt.normalize_ocr_text(
                "0",
                hint_char="０",
                period_like_glyph=True,
            ),
            "0",
        )

    def test_build_ocr_mapping_uses_status_code_failure_markers(self):
        class FakeEpub:
            def read(self, path):
                return b"font-bytes"

        class FakeRenderer:
            def __init__(self, font_bytes, font_path, options):
                pass

            def render(self, char):
                return Image.new("RGB", (16, 16), (255, 255, 255))

            def is_period_like_image(self, image):
                return False

        cases = [
            (OcrTextResult("", 0.9), None, "OCR_EMPTY", "[U+E000 OCR_EMPTY]"),
            (OcrTextResult("错错", 0.9), None, "OCR_MULTI_CHAR", "[U+E000 OCR_MULTI_CHAR]"),
            (OcrTextResult("错", 0.5), None, "OCR_LOW_CONF", "[U+E000 OCR_LOW_CONF]"),
            (None, RuntimeError("boom"), "OCR_EXCEPTION", "[U+E000 OCR_EXCEPTION]"),
        ]

        for result, error, status_code, expected in cases:
            with self.subTest(status_code=status_code):
                class FakeBackend:
                    def recognize(self, image, hint_char=""):
                        if error:
                            raise error
                        return result

                font_decrypt = FontDecrypt.__new__(FontDecrypt)
                font_decrypt.epub = FakeEpub()
                font_decrypt.ocr_backend = FakeBackend()
                font_decrypt.ocr_options = {"min_ocr_confidence": 0.8}
                font_decrypt.font_to_char_mapping = {"font.ttf": "\ue000"}
                font_decrypt.font_to_replace_mapping = {}
                font_decrypt.font_to_ocr_failure_mapping = {}

                with patch("python_backend.services.decrypt_font.FontGlyphRenderer", FakeRenderer):
                    font_decrypt.build_ocr_mapping()

                self.assertEqual(
                    font_decrypt.font_to_replace_mapping["font.ttf"]["\ue000"],
                    expected,
                )
                failure_record = font_decrypt.font_to_ocr_failure_mapping["font.ttf"][
                    "\ue000"
                ]
                self.assertEqual(failure_record["status_code"], status_code)
                self.assertEqual(failure_record["original_char"], "\ue000")
                self.assertEqual(
                    failure_record["image_alt"],
                    "U+E000 \ue000 " + status_code,
                )

    def test_ocr_failed_placeholder_has_default_status_code(self):
        font_decrypt = self.create_font_decrypt()
        self.assertEqual(
            font_decrypt.build_ocr_failed_placeholder("\ue000"),
            "[U+E000 OCR_FAILED]",
        )

    def test_ocr_failure_image_path_uses_font_hash_codepoint_and_status(self):
        font_decrypt = self.create_font_decrypt()
        font_decrypt.opf_path = "OEBPS/content.opf"

        self.assertEqual(
            font_decrypt.build_ocr_failure_image_path(
                "a13f9c2b",
                "\ue000",
                OCR_LOW_CONF,
            ),
            "OEBPS/Images/ocr-failures/a13f9c2b_U-E000_OCR_LOW_CONF.png",
        )

    def test_target_decrypt_fonts_are_marked_for_output_skip(self):
        font_decrypt = self.create_font_decrypt()
        font_decrypt.css_selector_to_font_mapping = {
            ".body": "fonts/obfuscated.ttf",
        }
        font_decrypt.font_to_char_mapping = {
            "fonts/inline-obfuscated.ttf": "\ue000",
        }

        self.assertEqual(
            font_decrypt.get_decrypt_target_font_files(),
            {"fonts/obfuscated.ttf", "fonts/inline-obfuscated.ttf"},
        )


class BuildPythonSidecarOcrBackendTest(unittest.TestCase):
    def test_default_model_is_v6_small_and_medium_is_optional(self):
        self.assertEqual(ocr_model_config.DEFAULT_OCR_MODEL_NAME, "PP-OCRv6_small_rec")
        self.assertEqual(DEFAULT_OCR_MODEL_NAME, "PP-OCRv6_small_rec")
        self.assertEqual(
            ocr_model_config.HIGH_ACCURACY_OCR_MODEL_NAME,
            "PP-OCRv6_medium_rec",
        )
        self.assertIn(
            ocr_model_config.DEFAULT_OCR_MODEL_NAME,
            ocr_model_config.OCR_MODEL_URLS,
        )
        self.assertIn(
            ocr_model_config.HIGH_ACCURACY_OCR_MODEL_NAME,
            ocr_model_config.OCR_MODEL_URLS,
        )

    def test_required_modules_are_runtime_only(self):
        self.assertIn("bs4", build_python_sidecar.BASE_REQUIRED_MODULES)
        self.assertIn("onnxruntime", build_python_sidecar.ONNX_REQUIRED_MODULES)
        self.assertNotIn("onnxruntime", build_python_sidecar.BASE_REQUIRED_MODULES)
        unused_modules = {"cv2", "pypdfium2", "pyclipper", "shapely", "imagesize"}
        self.assertTrue(unused_modules.isdisjoint(build_python_sidecar.BASE_REQUIRED_MODULES))
        self.assertFalse(any(name.startswith("PADDLE") for name in dir(build_python_sidecar)))

    def test_sidecar_runtime_modules_are_onnx_only(self):
        modules = build_python_sidecar.REQUIRED_MODULES

        self.assertIn("onnxruntime", modules)
        unused_modules = {"cv2", "pypdfium2", "pyclipper", "shapely", "imagesize"}
        self.assertTrue(unused_modules.isdisjoint(modules))
        self.assertNotIn("paddle", modules)
        self.assertNotIn("paddleocr", modules)
        self.assertNotIn("paddlex", modules)

    def test_pyinstaller_args_do_not_collect_paddle_packages(self):
        args = build_python_sidecar.PYINSTALLER_ONNX_ARGS

        self.assertIn("onnxruntime", args)
        self.assertNotIn("--collect-all", args)
        self.assertNotIn("paddle", args)
        self.assertNotIn("paddleocr", args)
        self.assertNotIn("paddlex", args)
        self.assertNotIn("pypdfium2", args)
        self.assertNotIn("pyclipper", args)
        self.assertNotIn("shapely", args)
        self.assertNotIn("imagesize", args)

    def test_sidecar_uses_onedir_layout_to_avoid_onefile_extract_delay(self):
        self.assertEqual(build_python_sidecar.PYINSTALLER_MODE, "--onedir")
        self.assertEqual(
            build_python_sidecar.sidecar_output_path(),
            build_python_sidecar.DIST_DIR
            / build_python_sidecar.SIDECAR_STEM
            / build_python_sidecar.SIDE_CAR_NAME,
        )


if __name__ == "__main__":
    unittest.main()
