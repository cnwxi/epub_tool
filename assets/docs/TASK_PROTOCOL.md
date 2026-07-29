# Engine Task Protocol

`proto/epub_tool/v1/engine.proto` 是 Vue、Tauri/Rust 与 Python 黄金样本之间唯一的
协议源。桌面应用通过 Tauri IPC 传递 Protobuf JSON 映射；Python 仅在黄金回归和维护
命令中使用同一映射，不参与桌面运行时；项目
规范的请求与输出字段均为 lower camel case。运行 `npm run protocol:generate`
生成 TypeScript 与 Python 类型；Rust 在 Cargo 构建时从同一文件生成类型。该命令固定
Buf CLI 与远程插件版本；提交前可用 `npm run protocol:check` 验证生成代码没有漂移。

## 请求

```json
{
  "protocolVersion": "PROTOCOL_VERSION_V1",
  "requestId": "request-uuid",
  "runTask": {
    "taskId": "task-uuid",
    "taskType": "TASK_TYPE_REFORMAT_EPUB",
    "inputFiles": ["/abs/path/book.epub"],
    "outputDir": "/abs/path/output",
    "options": { "empty": {} }
  }
}
```

`EngineRequest` 的 operation 是 `runTask` 或 `scanFonts`；`requestId` 必须在
响应和每个流事件中回显。项目仅保证 lower camel case 的请求格式与输出格式；其他
字段命名不是受支持的 API 契约。

Python 黄金样本 CLI 使用 `google.protobuf.json_format.ParseDict()` 解析请求。该上游解析器
可将 proto 原始字段名（snake_case）和 protobuf JSON 字段名（lower camel case）解析为
同一个内部消息字段，例如 `request_id` 与 `requestId` 都对应 `request.request_id`；它不会
修改原始 JSON 对象。Worker 使用 `MessageToDict(..., preserving_proto_field_name=False)`
输出消息，因此所有实际发出的 JSON Lines 均为 lower camel case。输入端的宽松解析是上游
实现行为，不构成对非规范字段名的兼容承诺。

Tauri IPC 直接传递完整的 `EngineEvent` 和 `EngineResponse` 信封。Rust 的
`engine_adapter` 负责将 `RunTaskRequest` 和 `TaskOptions` oneof 转为 Rust 任务引擎
的内部输入，再将内部事件和结果重新包装为协议消息；Vue 不接受未包裹的任务事件或结果。

任务枚举：`TASK_TYPE_REFORMAT_EPUB`、`TASK_TYPE_DECRYPT_EPUB`、
`TASK_TYPE_ENCRYPT_EPUB`、`TASK_TYPE_ENCRYPT_FONT`、`TASK_TYPE_DECRYPT_FONT`、
`TASK_TYPE_WEBP_TO_IMG`、`TASK_TYPE_IMAGE_COMPRESS`、`TASK_TYPE_IMAGE_TO_WEBP`、
`TASK_TYPE_CHINESE_CONVERT`、`TASK_TYPE_REPLACE_COVER`。

## 流事件与响应

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
    "currentFile": "/abs/path/book.epub",
    "currentIndex": 1,
    "totalFiles": 1,
    "level": "info"
  }
}
```

终止响应使用 `taskResult`、`fontScanResult` 或结构化 `error` oneof。`error`
包含稳定的 `code` 和面向用户的 `message`。字体扫描事件为 `fontScanProgress`，
包含 `currentIndex`、`totalFiles` 和 `result`。

当前错误码包括：参数或协议错误 `INVALID_ARGUMENT`、文件系统错误 `IO_ERROR`、
缺少处理依赖 `DEPENDENCY_ERROR`，以及未预期的内部错误 `INTERNAL`。协议错误必须
返回关联的 `EngineResponse`；Rust 任务执行错误会在 `taskResult.errors` 中按文件返回。

## Options oneof

- 无参数任务：`{ "empty": {} }`
- 字体任务：`font.targetFontFamiliesByFile`（值为 `{ "values": [...] }`），以及可选的 `ocrCharPolicy`、`minOcrConfidence`
- 图片压缩：`imageCompress.jpegQuality`、`webpQuality`、`pngToJpg`、`pngQuantize`
- WebP/图片转换：`imageConversion.quality`、`pngQuantize`
- 简繁转换：`chineseConvert.direction`（`s2t` 或 `t2s`）
- 更换封面：`replaceCover.coverPathByFile`

Rust 与 Python 黄金实现均只在各自 engine 边界转换这些字段；任务服务逻辑不应依赖
wire 格式。
