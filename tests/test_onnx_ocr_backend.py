import os
import tempfile
import unittest

import numpy as np
from PIL import Image

from build_tool import build_python_sidecar
from build_tool import ocr_model_config
from utils.decrypt_font import (
    DEFAULT_OCR_MODEL_NAME,
    FontDecrypt,
    FontGlyphRenderer,
    OnnxGlyphOcrBackend,
    create_onnx_session_options,
    format_ocr_progress,
    load_text_recognition_config,
)


class OnnxOcrBackendTest(unittest.TestCase):
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


class FontDecryptOcrTextCleanupTest(unittest.TestCase):
    def test_clean_text_keeps_encrypt_font_passthrough_punctuation_out_of_ocr(self):
        font_decrypt = FontDecrypt.__new__(FontDecrypt)
        font_decrypt.font_to_char_mapping = {
            "font.ttf": "你。？，！、；：《》（）\ue000<& \u0000",
        }

        font_decrypt.clean_text()

        self.assertEqual(font_decrypt.font_to_char_mapping["font.ttf"], "你\ue000")

    def test_private_use_period_alias_normalizes_to_chinese_full_stop(self):
        font_decrypt = FontDecrypt.__new__(FontDecrypt)

        self.assertEqual(font_decrypt.normalize_ocr_text(".", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text("．", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text("｡", hint_char="\ue000"), "。")
        self.assertEqual(font_decrypt.normalize_ocr_text(".", hint_char="。"), ".")


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
        self.assertFalse(any(name.startswith("PADDLE") for name in dir(build_python_sidecar)))

    def test_sidecar_runtime_modules_are_onnx_only(self):
        modules = build_python_sidecar.REQUIRED_MODULES

        self.assertIn("onnxruntime", modules)
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


if __name__ == "__main__":
    unittest.main()
