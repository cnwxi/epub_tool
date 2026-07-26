# CLI Usage

统一入口为 `python -m python_backend.cli`。任务模块只由统一后端加载。

```bash
conda create -n epub_tool python=3.12 -y
conda run -n epub_tool python -m pip install -r requirements/requirements.txt
conda run -n epub_tool python -m python_backend.cli run --help
```

## 执行请求

`run` 接受完整的 camelCase `EngineRequest`。输出为零或多个 `EngineEvent`
JSON Lines，最后一行是 `EngineResponse`；失败响应仍使用结构化 `error`。

```bash
conda run -n epub_tool python -m python_backend.cli run --requestJson '{
  "protocolVersion": "PROTOCOL_VERSION_V1",
  "requestId": "demo-request",
  "runTask": {
    "taskId": "demo-task",
    "taskType": "TASK_TYPE_WEBP_TO_IMG",
    "inputFiles": ["/path/book.epub"],
    "outputDir": "/path/output",
    "options": {
      "imageConversion": { "quality": 82, "pngQuantize": false }
    }
  }
}'
```

也可使用 camelCase 参数构造任务：

```bash
conda run -n epub_tool python -m python_backend.cli run \
  --requestId demo-request \
  --taskId demo-task \
  --taskType TASK_TYPE_REFORMAT_EPUB \
  --inputFile /path/book.epub \
  --outputDir /path/output
```

不再支持 `--request-json`、`--task-type`、`--input-file`、`--output-dir` 或
snake_case 请求字段。字体扫描通过 `serve` 的 `scanFonts` operation 进行，和任务
执行共享相同的 request/event/response 信封。

详见 [TASK_PROTOCOL.md](TASK_PROTOCOL.md)。
