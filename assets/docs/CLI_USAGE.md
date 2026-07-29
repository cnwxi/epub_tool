# Python 黄金样本 CLI

> Python CLI 不属于当前桌面应用的运行、构建、打包或发布链路。
> 它仅用于 Rust 迁移的黄金输出对比、问题定位和 OCR 模型维护。
> Python CLI 使用与桌面端相同的版本化 Protobuf JSON 信封；不接受 snake_case 别名作为公开契约。

统一入口：`python -m python_backend.cli`。任务模块仅供统一后端加载，不支持直接执行脚本；这样包内依赖的导入异常会原样暴露，便于诊断。

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
  --requestId demo-request \
  --taskId demo-task \
  --taskType TASK_TYPE_REFORMAT_EPUB \
  --inputFile /path/book.epub \
  --outputDir /path/output
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --requestId demo-request \
  --taskId demo-task \
  --taskType TASK_TYPE_ENCRYPT_EPUB \
  --inputFile /path/book.epub
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --requestId demo-request \
  --taskId demo-task \
  --taskType TASK_TYPE_DECRYPT_FONT \
  --inputFile /path/book.epub \
  --optionsJson '{
    "font": {
      "targetFontFamilies": ["ObfuscatedFont"],
      "ocrCharPolicy": "strict",
      "minOcrConfidence": 0.8
    }
  }'
```

## 使用完整 Protobuf 请求 JSON

```bash
conda run -n epub_tool python -m python_backend.cli run --requestJson '{
  "protocolVersion": "PROTOCOL_VERSION_V1",
  "requestId": "demo-request",
  "runTask": {
    "taskId": "demo-task",
    "taskType": "TASK_TYPE_ENCRYPT_FONT",
    "inputFiles": ["/path/book.epub"],
    "outputDir": "/path/output",
    "options": {
      "font": {
        "targetFontFamiliesByFile": {
          "/path/book.epub": { "values": ["KaiTi", "Source Han Serif SC"] }
        }
      }
    }
  }
}'
```

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --requestId demo-request \
  --taskId demo-task \
  --taskType TASK_TYPE_WEBP_TO_IMG \
  --inputFile /path/book.epub \
  --optionsJson '{"imageConversion": {"quality": 82, "pngQuantize": false}}'
```

`webp_to_img` 会将透明 WebP 转为 PNG、非透明 WebP 转为 JPEG。`quality` 取值为 `1` 到 `100`，默认 `82`；开启 `png_quantize` 会将透明图片降色至最多 256 色以减小体积，但可能损失颜色细节。

其他任务类型包括 `image_compress`、`image_to_webp`、`replace_cover` 和 `chinese_convert`。完整请求 JSON 的 options oneof 分别为 `imageCompress`、`imageConversion`、`replaceCover` 与 `chineseConvert`；字段映射详见 [任务协议](./TASK_PROTOCOL.md)。

`decrypt_font` 使用同一套 `target_font_families_by_file` 选项，并额外支持 `ocr_char_policy` 与 `min_ocr_confidence` 等 OCR 参数。`ocr_char_policy` 默认值为 `strict`，适合处理本工具生成的字体混淆 EPUB，会识别同宽码位池混淆后的半角/全角拉丁字母数字；`compatible` 用于兼容外部混淆工具，会保留 `strict` 的全部识别范围，并对用户选中的目标字体命中文本放宽 OCR 字符筛选，额外允许非 ASCII 可见字符进入 OCR，但仍排除空白、控制字符、真实中文标点和 ASCII 标点/普通符号。后端也接受 `external` 作为 `compatible` 的兼容别名。`min_ocr_confidence` 默认最低置信度为 `0.8`。OCR 模型默认固定为构建时内置的 `PP-OCRv6_small_rec_onnx`，默认路径为 `src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`；命令行单独调试时也可通过 `EPUB_TOOL_OCR_ONNX_MODEL_DIR` 指定模型目录，或通过 `EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec` 选择已准备好的高准确率模型目录。

反混淆时，高置信度单字 OCR 结果会回写 HTML 文本；失败分支会写入带 `ocr-failure` class 的可视化 HTML 占位，span 内只保留字形缩略图，避免未人工读校时直接显示错误类别文本。字形 PNG 会按 `Images/ocr-failures/{font_hash}_U-E000_OCR_LOW_CONF.png` 规则写入 EPUB，HTML 的 `data-codepoint`、`data-original-char`、`data-status`、`data-font-path` 和 `data-reason` 属性会保留原码位、原始字符与失败原因，图片 `alt` 会写入“字码 原始字符 错误类别”，便于人工回查和脚本统计。输出 EPUB 会跳过目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，避免混淆字体继续影响显示和后续文本比对。

## 列出字体 family

字体扫描通过 `serve` 子命令传入 `scanFonts` 请求执行；请求格式见[任务协议](./TASK_PROTOCOL.md)。

说明：

- `run` 会输出 JSON Lines 事件流。
- 成功时退出码为 `0`，存在失败项时为 `1`。
- 日志文件固定写入仓库根目录 `log.txt`。
