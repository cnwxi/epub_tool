# 文档索引

`assets/docs/` 保留当前实现需要维护的说明：

- `ARCHITECTURE.md`：统一 Rust 核心、Android 运行时和平台边界。
- `LOCAL_DEVELOPMENT.md`：Android 环境、启动、验证与构建命令。
- `BUILD_AND_BUNDLE.md`：Android 构建矩阵、CI、签名与发布边界。
- `TASK_PROTOCOL.md`：Protobuf wire contract、类型化核心协议和事件。
- `UI_DESIGN_GUIDELINES.md`：统一 UI、动画与轻磨砂玻璃设计规范。
- `CHANGELOG.md`：历史版本记录。

开发、测试、构建、CI、打包与发布使用 Rust/Node 工具链。架构或平台支持发生变化时，应同时更新代码、工作流、README 与上述对应文档。
