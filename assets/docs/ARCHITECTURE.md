# 架构

## 目标状态

Epub Tool 的所有任务运行于一个平台无关 Rust 业务核心。Tauri 只负责权限、资源定位、平台文件操作和 IPC：

```text
Vue / TypeScript
  -> Protobuf JSON IPC
  -> Tauri commands + engine_adapter
  -> typed TaskSpec
  -> in-process EngineRuntime (spawn_blocking)
  -> rust_backend::run
  -> typed TaskEvent + TaskResult
```

所有平台使用同一个进程内运行时，不启动任务子进程。Tauri 异步命令通过阻塞线程池调用业务核心，避免长任务阻塞 UI 事件循环。

## 核心 contract

`src-tauri/src/task_types.rs` 定义：

- `TaskType`
- `TaskOptions` 及其类型化 option structs
- `TaskSpec`
- `TaskEvent`
- `TaskResult` / `TaskSummary` / `FileIssue`

`proto/epub_tool/v1/engine.proto` 保留跨前端 IPC 的 wire contract。`engine_adapter` 在边界一次性转换 wire/core 类型。状态存储可以使用 JSON value，但任务请求、选项、事件与结果不能使用动态 value 中转。

## 统一任务引擎

每个任务实现 `EpubTask`：

- 声明 `TaskType`；
- 校验对应的 `TaskOptions`；
- 校验输入；
- 决定稳定输出后缀；
- 在 `EpubWorkspace` 上处理单本 EPUB。

`rust_backend::run` 统一处理输出目录、日志、批量进度、错误、跳过、结果汇总和最终事件。所有 EPUB 输出使用安全临时文件写入，并规范 `mimetype` 为第一个未压缩 ZIP 成员。

## 字体流水线

生产字体决策只有一条路径：

```text
EPUB members
  -> XHTML DOM + linked/inline CSS
  -> Stylo selector matching / cascade / computed style
  -> FontRequest(family stack, weight, style, stretch, character)
  -> FontFaceResolver(@font-face descriptors, unicode-range, src order)
  -> per-character font assignment
  -> FontEncryptionPlan
       -> font target scan
       -> encrypt_font
       -> decrypt_font
```

Stylo 负责选择器、级联和计算样式。外链 CSS、递归 `@import`、screen media、`@supports`、`@layer`、变量、继承、`!important` 和复杂选择器在同一引擎内处理。无法构建可靠计算结果时任务显式失败；没有第二套 cascade、手写 selector 或静默 fallback。

`FontFaceResolver` 保存多个 `@font-face`，按 family stack、style、weight、stretch、`unicode-range`、可解析 `src` 与来源顺序逐字符选择实际内嵌字体。字体扫描、加密与解密消费同一个 `FontEncryptionPlan`，不会各自重新判断字体。

字体容器层支持 TTF、OTF、WOFF、WOFF2：先得到 sfnt cmap，完成映射后重新写入原容器格式。CSS/OPF 的字体引用清理只消费已经选定的 family/path 集合，是任务完成后的资源清理，不参与 selector、cascade 或字符字体决策。

## 字体解密证据

每个候选混淆字符按以下步骤处理：

1. 从同一字体计划得到字体文件与字符；
2. 渲染实际字形；
3. 用内置 PP-OCRv6 ONNX 模型推理；
4. 保留每个时间步的 Top-5 token，并汇总稳定排序的文本候选；
5. 仅接受单字符且达到最低置信度的结果；
6. 其它结果写入复核标记。

复核标记包含原 codepoint、状态码、字体路径、置信度、Top-K JSON、原因和字形 PNG。低置信度、空结果、多字符结果或异常不会被猜测为某个字符。

## 运行资源

- OCR：`bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`
- OpenCC：`bundle-resources/opencc/`
- 移动 ONNX Runtime：由 `xtask` 下载、校验并解压到 `.mobile-runtime/`

桌面直接从开发目录或 Tauri resource dir 定位资源。移动端首次运行将已打包文件复制到应用数据目录，校验文件完整性后配置相同核心。

## 平台矩阵

| 平台 | 架构 / ABI | Runtime | OCR | CI 验证 |
| --- | --- | --- | --- | --- |
| Windows | x64、arm64 | in-process | ONNX Runtime | NSIS |
| macOS | x64、arm64 | in-process | ONNX Runtime | app、DMG |
| Linux | x64、arm64 | in-process | ONNX Runtime | deb、rpm |
| Android | arm64-v8a | in-process | ORT Android 1.24.3 | unsigned debug APK |
| iOS | arm64 device、arm64 simulator | in-process | ORT iOS 1.24.3 | device library、unsigned simulator app |

目录选择、目录扫描和打开路径是桌面能力；移动端使用文件 URI、缓存暂存与结果导出。任务类型、运行时与字体 OCR 不因平台而分叉。

## 验证边界

宿主单元/集成测试验证业务逻辑、类型化 contract 和进程内运行时。Android/iOS 必须由相应 SDK、NDK、Xcode 和 Rust target 完成真实交叉链接。CI 的移动产物不带生产签名；Android release keystore、Apple Team、证书、provisioning、device archive、IPA、商店上传和公证属于外部凭据边界。
