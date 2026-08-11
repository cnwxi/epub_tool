# 构建、打包与发布

## 构建目标

应用由 Vue 前端、Tauri 壳层和共享 Rust EPUB 任务核心组成。桌面端通过常驻
`rust-task-runner` 子进程执行任务；Android/iOS 在应用进程内执行同一套任务核心。
发布产物不包含 Python 解释器、Conda 环境或 Python sidecar。

安装包会携带：

- Tauri/Rust 可执行程序；
- `frontend/` 构建出的静态资源；
- 桌面包携带 `src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`；
- `src-tauri/bundle-resources/opencc/` 词典资源。

移动包暂不携带 ONNX OCR 模型。移动端原生 ONNX Runtime 接入前，字体 OCR 解密会由
平台能力接口禁用，其它处理任务不受影响。

## 本地开发与构建

先安装 Node.js（版本见 `.nvmrc`）、Rust stable 与平台所需 Tauri 库，再安装依赖：

```bash
npm install
npm --prefix frontend install
```

启动开发应用：

```bash
npm run tauri:dev
```

构建前端并校验 OCR 资源：

```bash
npm run build:bundle-assets
```

构建当前平台安装包：

```bash
npm run tauri:build
```

`tauri:build` 的 `beforeBuildCommand` 会执行 `build:bundle-assets`。因此正常发布不需要
执行任何 Python、Conda、PyInstaller 或 sidecar 准备步骤。

## Android 与 iOS

首次构建前安装对应 Tauri 前置依赖并初始化原生工程：

```bash
npm run tauri:android:init
npm run tauri:ios:init
```

iOS 初始化和构建只能在安装完整 Xcode 的 macOS 上进行。随后可执行：

```bash
npm run tauri:android:dev
npm run tauri:android:build
npm run tauri:ios:dev
npm run tauri:ios:build
```

`tauri.android.conf.json` 和 `tauri.ios.conf.json` 会覆盖桌面构建钩子：移动构建只生成
前端资源，不构建桌面 Worker，也不校验或打包桌面 ONNX OCR 模型。

## 发布前验证

至少执行：

```bash
cargo test --manifest-path src-tauri/Cargo.toml
npm run build:bundle-assets
npm run tauri:build
```

建议在目标平台安装包上验证启动、各任务执行、输出目录和日志定位。Python 黄金回归是可选的
开发验证，不是构建或发布依赖；需要时按 [本地开发指南](./LOCAL_DEVELOPMENT.md#可选-python-工作)
单独准备环境。

## CI 构建矩阵

[`.github/workflows/build.yml`](../../.github/workflows/build.yml) 支持：

- Linux x64 / arm64
- Windows x64 / arm64
- macOS x64 / arm64

当前 CI 会安装 Python 来运行现有黄金样本回归，并配置 OCR 模型变体；这些步骤不会构建或
打包 Python sidecar。安装包本身仍只有 Rust 后端和已提交资源。纯 Rust 的构建门槛是
`cargo test`、前端构建、资源校验与 Tauri 打包；Python 回归是额外的兼容性验证。

Android/iOS 依赖树已与桌面 `ort` 依赖隔离。将移动原生工程纳入 CI 前，还需确定 Android
签名、Apple Team/证书及移动 ONNX Runtime 的发布方式。

## 版本号与 Release

应用版本以 `src-tauri/Cargo.toml` 的 `package.version` 为唯一来源：

- 前端显示版本使用该值；
- Tauri 应用版本使用该值；
- Release workflow 默认由该值生成发布标签。

版本采用“年.月.日”格式，例如 `26.7.26`；同日修订使用 `-1`、`-2` 后缀。
发布前在 `assets/docs/CHANGELOG.md` 添加对应三级版本记录。GitHub Release 标签可添加
`v` 前缀，例如 `v26.7.26`。

## OCR 模型维护

默认发布使用已提交的 `PP-OCRv6_small_rec_onnx`，构建过程只校验资源，不下载或转换模型。
维护者刷新模型时才需要 Python 与 Conda：

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements-ocr-conversion.txt
conda run -n epub_tool npm run maintenance:fetch-ocr-model
conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```
