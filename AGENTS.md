# AGENTS.md

本文件为 Codex 在当前仓库中工作时提供仓库级指引。

## 项目概览

Epub Tool 是面向 EPUB 批量处理的跨平台应用，技术栈为 Tauri 2、Vue 3、TypeScript 与 Rust。Windows、macOS、Linux、Android、iOS 均在应用进程内执行同一个平台无关 Rust 业务核心。

当前任务类型：

- `reformat_epub`
- `decrypt_epub`
- `encrypt_epub`
- `encrypt_font`
- `decrypt_font`
- `webp_to_img`
- `image_compress`
- `image_to_webp`
- `chinese_convert`
- `replace_cover`

## 常用命令

```bash
# 安装依赖
npm ci
npm --prefix frontend ci

# 完整桌面开发环境
npm run tauri:dev

# 仅前端；没有 Tauri Runtime，不能执行任务
npm run dev

# Rust 核心与集成测试
cargo test --locked --manifest-path src-tauri/Cargo.toml

# Rust 维护工具测试
cargo test --locked --manifest-path xtask/Cargo.toml

# 协议生成与漂移检查
npm run protocol:generate
npm run protocol:check

# 前端类型检查和构建
npm run build

# 使用 Rust/ONNX Runtime 校验已提交 OCR 模型
npm run build:verify-ocr-model

# 当前桌面平台生产打包
npm run tauri:build

# 移动端无签名构建示例
npm run tauri:android:init -- --ci
npm run tauri:android:build -- aarch64 --debug --apk --ci
npm run tauri:ios:init -- --ci
npm run tauri:ios:build -- aarch64-sim --debug --ci
```

Node 版本见 `.nvmrc`。移动端必须使用 Rustup 安装相应 target；Android 还需要 SDK 36、NDK `29.0.13846066` 与 JDK 17，iOS 需要完整 Xcode。

## 架构

### 数据流

```text
Vue / generated TypeScript protobuf types
  -> Tauri IPC EngineRequest
  -> engine_adapter（wire -> typed core）
  -> TaskSpec / TaskOptions
  -> in-process EngineRuntime
  -> rust_backend::run
  -> typed TaskEvent / TaskResult
  -> engine_adapter（typed core -> wire）
  -> EngineEvent / EngineResponse
```

### 目录职责

- `frontend/`：Vue 单页应用、任务队列、设置、历史记录和生成的 TypeScript 协议类型。
- `proto/epub_tool/v1/engine.proto`：Tauri IPC wire contract 的唯一来源。
- `src-tauri/src/task_types.rs`：平台无关的 `TaskSpec`、`TaskOptions`、`TaskEvent`、`TaskResult`。
- `src-tauri/src/rust_backend/`：统一 EPUB 业务核心，按 `epub`、`font`、`image`、`text` 分类。
- `src-tauri/src/runtime/`：全平台进程内运行时、平台能力、路径和资源定位。
- `src-tauri/tests/`：跨模块任务与核心协议集成回归。
- `xtask/`：OCR 模型校验、移动 ONNX Runtime 准备、移动构建和发布维护工具。
- `src-tauri/bundle-resources/`：OCR 模型与 OpenCC 运行资源。
- `assets/docs/`：架构、协议、构建、发布和 UI 规范。

### 核心约束

- Protobuf 只属于 IPC 边界。业务服务不得接收 wire message、动态 JSON 或 Tauri 类型。
- 新任务必须实现统一 `EpubTask`，通过 `TaskSpec`/`TaskOptions` 输入并产生 `TaskEvent`/`TaskResult`。
- 桌面与移动必须调用同一 `rust_backend`；平台差异只能留在 `runtime`、权限、资源定位与 IPC 层。
- 所有平台必须通过 `spawn_blocking` 调用同一个进程内 `EngineRuntime`，不得新增任务子进程、sidecar 或动态适配层。
- `font_style.rs` 中的 Stylo 是生产环境唯一的 CSS 选择器、级联与计算样式引擎。不得添加第二套 cascade、旧规则 fallback 或按节点静默降级。
- 字体扫描、加密和解密必须复用 `FontEncryptionPlan` 及其字符级字体分配结果。
- 字体管线必须保留 family stack、weight、style、stretch、`unicode-range`、多 `src`、来源顺序、继承、变量、`!important` 与复杂选择器语义。
- TTF、OTF、WOFF、WOFF2 必须以原容器格式读取、改写 cmap 并回写。
- OCR 低置信度或非单字结果不得猜测替换；必须保留状态码、置信度、Top-K 候选与字形图片供复核。
- CSS/OPF 中已解密字体引用的清理只能消费字体决策结果，不得自行决定元素或字符使用哪个字体。

## 平台与发布

| 平台 | 架构 / ABI | 运行方式 | 字体 OCR | CI 产物 |
| --- | --- | --- | --- | --- |
| Windows | x64、arm64 | 进程内 | 启用 | NSIS |
| macOS | x64、arm64 | 进程内 | 启用 | app、DMG |
| Linux | x64、arm64 | 进程内 | 启用 | deb、rpm |
| Android | arm64-v8a、armeabi-v7a、x86、x86_64 | 进程内 | 启用 | 无签名 debug APK 编译验证 |
| iOS | arm64 device、arm64 simulator | 进程内 | 启用 | device Rust library 与无签名 simulator app 编译验证 |

Android release 签名、iOS device archive/IPA、商店上传、公证与生产代码签名需要外部凭据；没有凭据时只能声明编译验证，不能声明签名发布成功。

## 行为约定

- 输出名默认是 `{stem}_{task_type}.epub`。
- 简体转繁体使用 `_chinese_convert_tc.epub`，繁体转简体使用 `_chinese_convert_sc.epub`。
- 输入名已经包含当前任务后缀时跳过，不重复处理。
- `task.started` 是首个任务事件，`task.finished` 是最后一个事件并携带完整结果。
- `app-state.json` 已被忽略；损坏时备份为 `.corrupt-{timestamp}` 后重置。
- 文件加解密只处理 EPUB 内文件名与资源引用混淆，不处理 DRM。

## 功能扩展与文案

- 新增任务时同步修改 proto 枚举/options、`engine_adapter`、`TaskType`、`task_for`、前端导航/配置、输出后缀、集成测试和文档。
- 关于页使用动态任务数量与稳定能力概括，不写固定任务数量。
- 任务专属参数放在对应任务页面；关于页只描述统一工作流、协议和扩展方式。
- 不得重新引入解释器后端、生成代码或脚本依赖；开发、测试、构建、CI、打包与发布链路保持 Rust/Node 工具链。

## 验证要求

适用时执行：

```bash
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo fmt --manifest-path xtask/Cargo.toml -- --check
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo test --locked --manifest-path xtask/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo clippy --locked --manifest-path xtask/Cargo.toml --all-targets -- -D warnings
npm run protocol:check
npm run build
npm run build:verify-ocr-model
```

仅在安装对应 SDK、NDK、Xcode 与 Rust targets 的主机上声明移动构建通过。真实设备、代码签名、公证和商店发布未执行时必须明确说明。

## Codex 执行规范

- 开辟新分支不要使用 `codex/` 前缀。
