# 文档索引

`assets/docs/` 目录只保留当前仍然需要维护的核心说明：

- `LOCAL_DEVELOPMENT.md`：纯 Rust 桌面端的环境、启动、测试、打包与排查说明。
- `BUILD_AND_BUNDLE.md`：Rust/Tauri 本地构建、打包与发布说明。
- `TASK_PROTOCOL.md`：前端 IPC 与 Rust 任务事件/结果协议。
- `TAURI_PYTHON_BRIDGE.md`：Tauri Rust 任务桥接说明；文件名为兼容旧链接而保留。
- `CLI_USAGE.md`：Python 黄金样本 CLI 用法，不属于桌面运行或发布链路。
- `UI_DESIGN_GUIDELINES.md`：统一的 UI、动画与轻磨砂玻璃设计规范。

Python 仅用于黄金样本测试和 OCR 模型维护；本地运行、打包和发布不需要 Conda 或 sidecar。
