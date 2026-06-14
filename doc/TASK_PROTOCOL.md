# Task Protocol

Python 后端与 Tauri 壳层之间采用 JSON Lines 协议。

## 请求结构

```json
{
  "task_id": "uuid-or-custom-id",
  "task_type": "reformat",
  "input_files": ["/abs/path/book.epub"],
  "output_dir": "/abs/path/output",
  "options": {}
}
```

`task_type` 当前支持：

- `reformat`
- `decrypt`
- `encrypt`
- `font_encrypt`
- `font_decrypt`
- `transfer_img`

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
  "output_path": "/abs/path/book_reformat.epub",
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
  "outputs": ["/abs/path/book_reformat.epub"],
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

`font_encrypt` 和 `font_decrypt` 都使用同一套按文件选择字体 family 的选项：

```json
{
  "options": {
    "target_font_families_by_file": {
      "/abs/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}
```

`font_decrypt` 使用构建时内置的固定 ONNX OCR 模型，默认最低置信度为 `0.8`，
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

- `strict`：默认值，保持本工具生成 EPUB 的反混淆范围，只对私用区、旧版韩文区兼容码位，以及 Unicode 类别为 L/N 且 East Asian Width 为 W/F 的字符做 OCR；空白、控制字符、真实中文标点、ASCII 普通文本和符号数字不会进入 OCR。
- `compatible`：兼容外部字体混淆工具。对用户选中目标字体命中的文本放宽筛选，允许非 ASCII 可见字符进入 OCR；仍排除空白、控制字符、真实中文标点和 ASCII 普通文本。该模式识别面更大，可能把目标字体作用下的真实特殊符号也改写为 OCR 结果。

后端也接受 `external` 作为 `compatible` 的兼容别名。

默认模型为 `PP-OCRv6_small_rec_onnx`，资源目录为
`ocr-models/PP-OCRv6_small_rec_onnx/`。Tauri 启动 Python sidecar 时会通过
`EPUB_TOOL_OCR_ONNX_MODEL_DIR` 注入模型路径。若模型目录缺失，任务会直接失败；默认构建只校验已提交的 ONNX 模型资源，不会在运行时下载或转换 Paddle 源模型。

反混淆输出策略：

- 高置信度单字 OCR 结果会替换 HTML 文本节点中的混淆字符。
- OCR 为空、非单字、置信度低于阈值或异常时，会分别写入 `[U+XXXX OCR_EMPTY]`、`[U+XXXX OCR_MULTI_CHAR]`、`[U+XXXX OCR_LOW_CONF]`、`[U+XXXX OCR_EXCEPTION]` 占位字符串，便于人工回查和脚本统计。`[U+XXXX OCR_FAILED]` 仅作为无细分原因时的兜底格式。
- 输出 EPUB 不再写入目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，避免混淆字体继续影响阅读器或后续文本工具的显示结果。
