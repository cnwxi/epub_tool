# CLI Usage

统一入口：`python -m python_backend.cli`

## 安装依赖

```bash
conda create -n epub_tool python=3.12 -y
conda run -n epub_tool python -m pip install -r requirements.txt
```

## 查看帮助

```bash
conda run -n epub_tool python -m python_backend.cli --help
conda run -n epub_tool python -m python_backend.cli run --help
```

## 直接执行任务

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type reformat \
  --input-file /path/book.epub \
  --output-dir /path/output
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type encrypt \
  --input-file /path/book.epub
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --task-type font_decrypt \
  --input-file /path/book.epub \
  --options-json '{
    "target_font_families": ["ObfuscatedFont"],
    "min_ocr_confidence": 0.8
  }'
```

## 使用完整请求 JSON

```bash
conda run -n epub_tool python -m python_backend.cli run --request-json '{
  "task_id": "demo-task",
  "task_type": "font_encrypt",
  "input_files": ["/path/book.epub"],
  "output_dir": "/path/output",
  "options": {
    "target_font_families_by_file": {
      "/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}'
```

`font_decrypt` 使用同一套 `target_font_families_by_file` 选项，并额外支持 `min_ocr_confidence` 等 OCR 参数。OCR 模型默认固定为构建时内置的 `PP-OCRv6_small_rec_onnx`，默认路径为 `src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`；命令行单独调试时也可通过 `EPUB_TOOL_OCR_ONNX_MODEL_DIR` 指定模型目录，或通过 `EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec` 选择已准备好的高准确率模型目录。

## 列出字体 family

```bash
conda run -n epub_tool python -m python_backend.cli list-fonts /path/book.epub
```

说明：

- `run` 会输出 JSON Lines 事件流。
- 成功时退出码为 `0`，存在失败项时为 `1`。
- 日志文件固定写入仓库根目录 `log.txt`。
