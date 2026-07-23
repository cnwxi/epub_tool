# Task Protocol

Python 后端与 Tauri 壳层之间采用 JSON Lines 协议。

## 请求结构

```json
{
  "task_id": "uuid-or-custom-id",
  "task_type": "reformat_epub",
  "input_files": ["/abs/path/book.epub"],
  "output_dir": "/abs/path/output",
  "options": {}
}
```

`task_type` 当前支持：

- `reformat_epub`
- `decrypt_epub`
- `encrypt_epub`
- `encrypt_font`
- `decrypt_font`
- `webp_to_img`
- `image_compress`
- `image_to_webp`
- `replace_cover`
- `chinese_convert`

## 输出文件命名

任务会在输出目录中以 `{原文件名}_{任务脚本名}.epub` 创建结果文件。对应后缀为：

- `reformat_epub`：`_reformat_epub.epub`
- `decrypt_epub`：`_decrypt_epub.epub`
- `encrypt_epub`：`_encrypt_epub.epub`
- `encrypt_font`：`_encrypt_font.epub`
- `decrypt_font`：`_decrypt_font.epub`
- `webp_to_img`：`_webp_to_img.epub`
- `image_compress`：`_image_compress.epub`
- `image_to_webp`：`_image_to_webp.epub`
- `replace_cover`：`_replace_cover.epub`
- `chinese_convert`：简体转繁体为 `_chinese_convert_tc.epub`，繁体转简体为 `_chinese_convert_sc.epub`

## 运行时事件

每一行都是一个 JSON 对象，核心字段如下：

```json
{
  "event": "task.file.started",
  "task_id": "demo",
  "status": "running",
  "progress": 0,
  "message": "开始处理 book.epub",
  "current_file": "/abs/path/book.epub",
  "current_index": 1,
  "total_files": 3,
  "output_path": "/abs/path/book_reformat_epub.epub",
  "level": "info"
}
```

常见事件：

- `task.started`
- `task.log`
- `task.file.started`
- `task.file.finished`
- `task.finished`
- `task.stderr`

## 最终结果

```json
{
  "ok": true,
  "status": "success",
  "outputs": ["/abs/path/book_reformat_epub.epub"],
  "errors": [],
  "skipped": [],
  "summary": {
    "total": 1,
    "success": 1,
    "failed": 0,
    "skipped": 0
  },
  "log_path": "/repo/log.txt"
}
```

## 字体任务选项

`encrypt_font` 和 `decrypt_font` 都使用同一套按文件选择字体 family 的选项：

```json
{
  "options": {
    "target_font_families_by_file": {
      "/abs/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}
```

`decrypt_font` 使用构建时内置的固定 ONNX OCR 模型，默认最低置信度为 `0.8`，
默认 OCR 字符筛选策略为 `strict`：

```json
{
  "options": {
    "ocr_char_policy": "strict",
    "min_ocr_confidence": 0.8
  }
}
```

`ocr_char_policy` 可选值：

- `strict`：默认值，保持本工具生成 EPUB 的反混淆范围，只对私用区、韩文音节区兼容码位、同宽码位池混淆的半角/全角拉丁字母数字，以及 Unicode 类别为 L/N 且 East Asian Width 为 W/F 的字符做 OCR；空白、控制字符、真实中文标点和符号数字不会进入 OCR。
- `compatible`：兼容外部字体混淆工具。保留 `strict` 的全部识别范围，并对用户选中目标字体命中的文本放宽筛选，额外允许非 ASCII 可见字符进入 OCR；仍排除空白、控制字符、真实中文标点和 ASCII 标点/普通符号。该模式识别面更大，可能把目标字体作用下的真实特殊符号也改写为 OCR 结果。

后端也接受 `external` 作为 `compatible` 的兼容别名。

默认模型为 `PP-OCRv6_small_rec_onnx`，资源目录为
`ocr-models/PP-OCRv6_small_rec_onnx/`。Tauri 启动 Python sidecar 时会通过
`EPUB_TOOL_OCR_ONNX_MODEL_DIR` 注入模型路径。若模型目录缺失，任务会直接失败；默认构建只校验已提交的 ONNX 模型资源，不会在运行时下载或转换 Paddle 源模型。

反混淆输出策略：

- 高置信度单字 OCR 结果会替换 HTML 文本节点中的混淆字符。
- OCR 为空、非单字、置信度低于阈值或异常时，会分别写入带 `ocr-failure` class 的 HTML 可视化占位，正文显示为 `[字形缩略图 OCR_EMPTY]`、`[字形缩略图 OCR_MULTI_CHAR]`、`[字形缩略图 OCR_LOW_CONF]`、`[字形缩略图 OCR_EXCEPTION]`。缩略图 PNG 按 `Images/ocr-failures/{font_hash}_U-E000_OCR_LOW_CONF.png` 规则写入 EPUB，并在 OPF manifest 中登记为 `image/png`；HTML 属性保留 `U+XXXX`、状态码、字体路径和失败原因，便于人工回查和脚本统计。`OCR_FAILED` 仅作为无细分原因时的兜底状态。
- 输出 EPUB 不再写入目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，避免混淆字体继续影响阅读器或后续文本工具的显示结果。

## 图片与文本任务选项

`webp_to_img` 使用以下选项：

```json
{
  "options": {
    "quality": 82,
    "png_quantize": false
  }
}
```

- `quality`：非透明 WebP 转为 JPEG 时使用的质量，取值为 `1` 到 `100`，默认 `82`。
- `png_quantize`：是否将透明 WebP 转出的 PNG 降色至最多 256 色，默认 `false`。开启后可减小体积，但会损失部分颜色细节。

`image_compress` 使用 `jpeg_quality`、`webp_quality` 与可选的 `png_to_jpg`、`png_quantize`。后者会将仍保留为 PNG 的图片降色至最多 256 色；`image_to_webp` 使用 `quality`；`replace_cover` 使用按输入 EPUB 路径映射的 `cover_path_by_file`；`chinese_convert` 使用 `direction`，可选值为 `s2t` 或 `t2s`。
