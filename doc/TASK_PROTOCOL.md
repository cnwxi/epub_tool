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

`font_decrypt` 使用构建时内置的固定 ONNX OCR 模型：

```json
{
  "options": {
    "min_ocr_confidence": 0.8
  }
}
```

默认模型为 `PP-OCRv6_small_rec_onnx`，资源目录为
`ocr-models/PP-OCRv6_small_rec_onnx/`。Tauri 启动 Python sidecar 时会通过
`EPUB_TOOL_OCR_ONNX_MODEL_DIR` 注入模型路径。若模型目录缺失，任务会直接失败；默认构建只校验已提交的 ONNX 模型资源，不会在运行时下载或转换 Paddle 源模型。
