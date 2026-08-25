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

Android 使用同一个进程内运行时，不启动任务子进程。Tauri 异步命令通过阻塞线程池调用业务核心，避免长任务阻塞 UI 事件循环。

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

## 运行资源

- OpenCC：`bundle-resources/opencc/`

Android 首次运行会将已打包文件复制到应用数据目录，校验文件完整性后配置相同核心。

## 平台

| 平台 | 架构 / ABI | Runtime | CI 验证 |
| --- | --- | --- | --- |
| Android | arm64-v8a、armeabi-v7a、x86_64、x86 | in-process | release APK |

Android 使用文件 URI、缓存暂存与结果导出，不支持目录扫描和应用内打开本地路径。

## 验证边界

宿主单元/集成测试验证业务逻辑、类型化 contract 和进程内运行时。Android 必须由相应 SDK、NDK 和 Rust target 完成真实交叉链接。CI 的 Android 产物使用仓库配置的 keystore，未配置时使用临时签名；Android release keystore 与商店上传属于外部凭据边界。
