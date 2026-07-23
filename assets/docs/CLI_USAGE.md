# CLI Usage

统一入口：`python -m python_backend.cli`

## 安装依赖

```bash
conda create -n epub_tool python=3.12 -y
conda run -n epub_tool python -m pip install -r requirements/requirements.txt
```

## 查看帮助

```bash
conda run -n epub_tool python -m python_backend.cli --help
conda run -n epub_tool python -m python_backend.cli run --help
```

## 直接执行任务

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type reformat_epub \
  --input-file /path/book.epub \
  --output-dir /path/output
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type encrypt_epub \
  --input-file /path/book.epub
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type decrypt_font \
  --input-file /path/book.epub \
  --options-json '{
    "target_font_families": ["ObfuscatedFont"],
    "ocr_char_policy": "strict",
    "min_ocr_confidence": 0.8
  }'
```

## 使用完整请求 JSON

```bash
conda run -n epub_tool python -m python_backend.cli run --request-json '{
  "task_id": "demo-task",
  "task_type": "encrypt_font",
  "input_files": ["/path/book.epub"],
  "output_dir": "/path/output",
  "options": {
    "target_font_families_by_file": {
      "/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}'
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type webp_to_img \
  --input-file /path/book.epub \
  --options-json '{"quality": 82, "png_quantize": false}'
```

`webp_to_img` 会将透明 WebP 转为 PNG、非透明 WebP 转为 JPEG。`quality` 取值为 `1` 到 `100`，默认 `82`；开启 `png_quantize` 会将透明图片降色至最多 256 色以减小体积，但可能损失颜色细节。

其他任务类型包括 `image_compress`、`image_to_webp`、`replace_cover` 和 `chinese_convert`。完整请求 JSON 中可分别传入 `jpeg_quality` / `webp_quality` / `png_to_jpg` / `png_quantize`、`quality`、`cover_path_by_file` 以及 `direction`（`s2t` 或 `t2s`）等选项。

`decrypt_font` 使用同一套 `target_font_families_by_file` 选项，并额外支持 `ocr_char_policy` 与 `min_ocr_confidence` 等 OCR 参数。`ocr_char_policy` 默认值为 `strict`，适合处理本工具生成的字体混淆 EPUB，会识别同宽码位池混淆后的半角/全角拉丁字母数字；`compatible` 用于兼容外部混淆工具，会保留 `strict` 的全部识别范围，并对用户选中的目标字体命中文本放宽 OCR 字符筛选，额外允许非 ASCII 可见字符进入 OCR，但仍排除空白、控制字符、真实中文标点和 ASCII 标点/普通符号。后端也接受 `external` 作为 `compatible` 的兼容别名。`min_ocr_confidence` 默认最低置信度为 `0.8`。OCR 模型默认固定为构建时内置的 `PP-OCRv6_small_rec_onnx`，默认路径为 `src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`；命令行单独调试时也可通过 `EPUB_TOOL_OCR_ONNX_MODEL_DIR` 指定模型目录，或通过 `EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec` 选择已准备好的高准确率模型目录。

反混淆时，高置信度单字 OCR 结果会回写 HTML 文本；失败分支会写入带 `ocr-failure` class 的可视化 HTML 占位，span 内只保留字形缩略图，避免未人工读校时直接显示错误类别文本。字形 PNG 会按 `Images/ocr-failures/{font_hash}_U-E000_OCR_LOW_CONF.png` 规则写入 EPUB，HTML 的 `data-codepoint`、`data-original-char`、`data-status`、`data-font-path` 和 `data-reason` 属性会保留原码位、原始字符与失败原因，图片 `alt` 会写入“字码 原始字符 错误类别”，便于人工回查和脚本统计。输出 EPUB 会跳过目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，避免混淆字体继续影响显示和后续文本比对。

## 列出字体 family

```bash
conda run -n epub_tool python -m python_backend.cli list-fonts /path/book.epub
```

说明：

- `run` 会输出 JSON Lines 事件流。
- 成功时退出码为 `0`，存在失败项时为 `1`。
- 日志文件固定写入仓库根目录 `log.txt`。
