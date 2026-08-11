# 本地开发

本分支的桌面应用是纯 Rust 任务后端：Vite 提供前端开发服务器，Tauri 调用 Rust
任务引擎。日常开发、运行、构建和发布都不需要 Conda、Python 或 Python sidecar。

Python 仅保留为黄金样本测试和维护 OCR 模型的工具，见本文末尾的“可选 Python 工作”。

## 前置依赖

| 依赖 | 用途 | 验证命令 |
| --- | --- | --- |
| Node.js | 前端和 Tauri CLI | `node --version` |
| npm | 安装与运行脚本 | `npm --version` |
| Rust stable（含 Cargo） | Tauri 与 EPUB 任务引擎 | `rustc --version`、`cargo --version` |

项目 Node.js 版本见仓库根目录的 `.nvmrc`。

### macOS

先安装 Apple Command Line Tools：

```bash
xcode-select --install
```

随后通过 Rustup 安装 Rust stable，并按 `.nvmrc` 安装 Node.js。确认：

```bash
node --version
npm --version
rustc --version
cargo --version
```

### Windows

使用原生 Windows 的 PowerShell 或 Windows Terminal，不在 WSL 中运行桌面应用。

1. 安装 Visual Studio Build Tools，并选择 **Desktop development with C++**。
2. 安装 WebView2 Runtime（Windows 10 1803 及之后版本通常已包含）。
3. 安装 Rustup，并选择 `stable-msvc` 工具链。

### Linux

需安装 WebKitGTK 与 Tauri 系统库。Debian/Ubuntu 示例：

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

其他发行版请按 [Tauri 前置依赖](https://v2.tauri.app/start/prerequisites/) 安装对应包。

### Android

安装 Android Studio、Android SDK/NDK、JDK 和 Rustup，然后初始化原生工程：

```bash
npm run tauri:android:init
```

### iOS

在 macOS 安装完整 Xcode、xcodegen 和 Rustup，并准备 Apple 开发签名配置：

```bash
npm run tauri:ios:init
```

## 安装依赖

在仓库根目录执行：

```bash
npm install
npm --prefix frontend install
```

首次运行会由 Cargo 下载、编译 Rust 依赖。该过程不依赖 Python 环境。

## 启动桌面开发环境

```bash
npm run tauri:dev
```

该命令会校验已提交的 ONNX OCR 资源、启动 Vite，并启动 Tauri 桌面窗口。桌面任务执行路径为：

```text
Vue → Tauri command → EngineRuntime → rust-task-runner → rust_backend → EpubTask
```

Android/iOS 使用相同的 `EngineRuntime` 接口，但直接在应用进程内调用 `rust_backend`。

仅调试前端样式时可运行：

```bash
npm run dev
```

此模式没有 Tauri Runtime，不能执行 EPUB 任务。

## Rust 编译、测试与打包

仅编译 Rust 后端：

```bash
cargo build --manifest-path src-tauri/Cargo.toml
```

运行 Rust 单元测试：

```bash
cargo test --manifest-path src-tauri/Cargo.toml
```

构建前端并校验提交的 ONNX OCR 资源：

```bash
npm run build:bundle-assets
```

构建当前平台的桌面安装包：

```bash
npm run tauri:build
```

Tauri 打包只携带 Rust 可执行程序、前端静态资源、ONNX OCR 模型与 OpenCC 词典；不会构建、携带或启动 Python sidecar。

移动端开发和构建命令：

```bash
npm run tauri:android:dev
npm run tauri:android:build
npm run tauri:ios:dev
npm run tauri:ios:build
```

移动构建不生成桌面 Worker，也不携带 ONNX OCR 模型；字体 OCR 会在前端显示为当前平台不可用。

## `cargo metadata` 或 Cargo 不可用

若 Tauri 提示找不到 `cargo`，先在运行 `npm run tauri:dev` 的同一终端确认：

```bash
cargo --version
```

macOS/Linux 上使用 Rustup 安装时，可执行：

```bash
source "$HOME/.cargo/env"
cargo --version
```

Windows 上请重新打开 PowerShell 或 IDE，并确认 Rustup 的安装目录位于 `PATH`。

## 可选 Python 工作

Python 不属于桌面运行或发布依赖。仅在以下情况配置 Conda 环境：

- 使用 `python_backend/` 生成 Rust 迁移的黄金样本；
- 运行 Rust/Python 输出对比回归；
- 刷新或转换提交到仓库的 OCR ONNX 模型。

例如，执行黄金回归前：

```bash
conda create -n epub_tool python=3.12 -y
conda run -n epub_tool python -m pip install -r requirements/requirements.txt
conda run -n epub_tool python -m pytest -q tests/test_rust_image_golden.py
```

维护 OCR 模型才需要转换依赖：

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements-ocr-conversion.txt
conda run -n epub_tool npm run maintenance:fetch-ocr-model
conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```
