# Tauri Rust 任务桥接

此文件保留原文件路径，避免旧链接失效；当前实现不再存在 Tauri-Python bridge 或 Python sidecar。

## 调用链

```text
Vue 组件
  → invoke("run_epub_task")
  → Tauri Rust command
  → rust_backend::run
  → EpubTask 注册表
  → EPUB workspace 读写与任务实现
  → Tauri Channel 推送 TaskEvent
  → 返回 TaskResult
```

## Rust 侧职责

- 解析并校验前端 `FrontendTaskRequest`；
- 递归扫描输入目录中的 `.epub`；
- 以统一 `EpubTask` trait 注册 EPUB、图片、文本和字体任务；
- 使用同一事件和结果 JSON 结构推送进度、日志、成功、跳过与失败；
- 读取打包资源中的 ONNX OCR 模型与 OpenCC 词典；
- 写入输出 EPUB 和 `log.txt`。

任务类型由 `src-tauri/src/rust_backend/mod.rs` 的注册表统一分发。前端 IPC 命令名和事件字段
保持稳定，以避免前端协议因后端迁移发生变化。

## 资源与运行时

- `decrypt_font` 使用 Rust `ort` 运行 ONNX OCR 模型；
- `chinese_convert` 使用打包的 OpenCC 词典；
- 默认运行不会下载模型、调用系统 Python 或启动外部后端进程。

## Python 的保留用途

`python_backend/` 与 Python CLI 仅用于迁移期黄金输出对比，以及维护者刷新 OCR 模型。
它们不在 Tauri 的开发、构建、打包或发布调用链中。
