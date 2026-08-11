# Engine Task Protocol

`proto/epub_tool/v1/engine.proto` 是 Tauri IPC wire contract 的唯一来源。规范 JSON 映射使用 lower camel case；不兼容变更通过新的 proto package version 演进。

```bash
npm run protocol:generate
npm run protocol:check
```

`protocol:generate` 生成前端 TypeScript 类型；Rust 在 Cargo build script 中从同一 proto 生成 wire types。生成流程固定 Buf CLI 和远程插件版本。

## 分层

```text
EngineRequest / EngineEvent / EngineResponse (protobuf wire)
                   |
             engine_adapter
                   |
TaskSpec / TaskOptions / TaskEvent / TaskResult (typed Rust core)
```

`engine_adapter` 是唯一 wire/core 转换边界。业务任务不接收 Protobuf message、Tauri command 参数或动态 JSON。所有平台的进程内运行时直接调用相同 core，不做 wire -> 动态 JSON -> wire 往返。

## 请求

```json
{
  "protocolVersion": "PROTOCOL_VERSION_V1",
  "requestId": "request-uuid",
  "runTask": {
    "taskId": "task-uuid",
    "taskType": "TASK_TYPE_REFORMAT_EPUB",
    "inputFiles": ["/absolute/book.epub"],
    "outputDir": "/absolute/output",
    "options": { "empty": {} }
  }
}
```

`EngineRequest.operation` 是 `runTask` 或 `scanFonts`。`protocolVersion` 必须是 `PROTOCOL_VERSION_V1`；`requestId` 必须在每个事件与终止响应中回显。

任务枚举：

- `TASK_TYPE_REFORMAT_EPUB`
- `TASK_TYPE_DECRYPT_EPUB`
- `TASK_TYPE_ENCRYPT_EPUB`
- `TASK_TYPE_ENCRYPT_FONT`
- `TASK_TYPE_DECRYPT_FONT`
- `TASK_TYPE_WEBP_TO_IMG`
- `TASK_TYPE_IMAGE_COMPRESS`
- `TASK_TYPE_IMAGE_TO_WEBP`
- `TASK_TYPE_CHINESE_CONVERT`
- `TASK_TYPE_REPLACE_COVER`

## Options oneof

- 无参数任务：`{ "empty": {} }`
- 字体任务：`font.targetFontFamiliesByFile`、`targetFontFamilies`、`ocrCharPolicy`、`minOcrConfidence`
- 图片压缩：`imageCompress.jpegQuality`、`webpQuality`、`pngToJpg`、`pngQuantize`
- 图片格式转换：`imageConversion.quality`、`pngQuantize`
- 简繁转换：`chineseConvert.direction`，值为 `s2t` 或 `t2s`
- 更换封面：`replaceCover.coverPathByFile`

`targetFontFamiliesByFile` 的 map value 是 `FontFamilies`，JSON 形式为 `{ "values": ["Family"] }`。任务类型和 option kind 不匹配时 adapter 直接拒绝请求，不让无效组合进入核心。

## 流事件

```json
{
  "protocolVersion": "PROTOCOL_VERSION_V1",
  "requestId": "request-uuid",
  "taskEvent": {
    "event": "task.file.started",
    "taskId": "task-uuid",
    "status": "running",
    "progress": 0,
    "message": "开始处理 book.epub",
    "currentFile": "/absolute/book.epub",
    "currentIndex": 1,
    "totalFiles": 1,
    "outputPath": "/absolute/output/book_reformat_epub.epub",
    "level": "info"
  }
}
```

稳定事件序列：

1. `task.started`
2. 每个文件的 `task.file.started`
3. 零个或多个 `task.log`
4. 每个文件的 `task.file.finished`
5. `task.finished`

最后一个事件携带完整 `TaskResult`。单文件失败与跳过分别进入 `errors`、`skipped`；`summary` 包含 `total`、`success`、`failed`、`skipped`。`status` 为 `success`、`partial` 或 `error`。

`progress` 始终表示整个批量任务的 `0..100` 进度。核心任务可通过类型化文件内进度更新推进 `task.log`；运行时按当前文件索引折算为总体进度并保证不回退。字体 OCR 在首个字符、每 100 个字符及最后一个字符更新进度，OCR 阶段最高到当前文件的 99%，文件改写和写盘完成后的 `task.file.finished` 才到达该文件终点。

字体扫描用 `fontScanProgress` 流式返回每本 EPUB 的 `fontFamilies` 或结构化错误，终止响应使用 `fontScanResult`。

## 响应与错误

`EngineResponse.payload` 是：

- `taskResult`
- `fontScanResult`
- `error`

协议/参数错误使用结构化 `EngineError`；任务处理错误按输入文件进入 `TaskResult.errors`。适配层当前使用的错误类别包括 `INVALID_ARGUMENT`、`IO_ERROR`、`DEPENDENCY_ERROR` 和 `INTERNAL`。
