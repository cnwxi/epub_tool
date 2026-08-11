# 本地开发

应用由 Vue 前端、Tauri 壳层、统一 Rust EPUB 核心和 Rust `xtask` 维护工具组成。日常开发、测试、构建与打包只需要 Node.js、Rust 及目标平台工具链。

## 前置依赖

| 依赖 | 用途 | 验证命令 |
| --- | --- | --- |
| Node.js（版本见 `.nvmrc`） | 前端和 Tauri CLI | `node --version` |
| npm | 安装依赖和运行脚本 | `npm --version` |
| Rust stable / Cargo | 业务核心、Tauri、xtask | `rustc --version`、`cargo --version` |

### macOS

桌面构建至少需要 Apple Command Line Tools：

```bash
xcode-select --install
```

iOS 构建必须安装完整 Xcode，并使用 Rustup 安装 `aarch64-apple-ios` 和 `aarch64-apple-ios-sim` targets。

### Windows

使用原生 PowerShell 或 Windows Terminal：

1. 安装 Visual Studio Build Tools 的 **Desktop development with C++**。
2. 安装 WebView2 Runtime。
3. 通过 Rustup 安装 `stable-msvc`。

### Linux

Debian/Ubuntu 示例：

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev patchelf
```

其他发行版按 Tauri 2 对应平台前置依赖安装。

### Android

安装 JDK 17、Android SDK 36、NDK `29.0.13846066` 和 Rustup。按需要安装以下 Rust target：

| Tauri target | Rust target | Android ABI |
| --- | --- | --- |
| `aarch64` | `aarch64-linux-android` | `arm64-v8a` |

应用最低 Android API 为 24。

### iOS

iOS 应用最低系统版本为 15.1。模拟器构建使用 `aarch64-sim`，设备 Rust 库使用 `aarch64` / `aarch64-apple-ios`。生成签名 device archive 或 IPA 还需要 Apple Development Team、证书和 provisioning profile。

## 安装依赖

```bash
npm ci
npm --prefix frontend ci
```

## 启动与调试

完整桌面开发环境：

```bash
npm run tauri:dev
```

启动顺序是：校验内置 ONNX OCR 模型、启动 Vite、启动 Tauri。桌面与移动任务路径一致：

```text
Vue -> Tauri IPC -> spawn_blocking -> in-process EngineRuntime -> rust_backend
```

仅调试前端时：

```bash
npm run dev
```

此模式没有 Tauri Runtime，不能执行 EPUB 任务。

## 验证

```bash
# 格式
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo fmt --manifest-path xtask/Cargo.toml -- --check

# 单元和集成测试
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo test --locked --manifest-path xtask/Cargo.toml

# 静态检查
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
cargo clippy --locked --manifest-path xtask/Cargo.toml --all-targets -- -D warnings

# wire contract 与前端
npm run protocol:check
npm run build

# 使用真实 Rust ONNX Runtime session 校验模型输入、输出和字典维度
npm run build:verify-ocr-model
```

`src-tauri/tests/core_regression.rs` 使用运行时生成的稳定 EPUB fixture 覆盖输出后缀、跳过行为、加密/解密往返、简繁转换和任务事件/结果。

## 桌面构建

```bash
npm run build:bundle-assets
npm run tauri:build
```

`build:bundle-assets` 构建前端并验证 OCR 模型。安装包携带前端、进程内 Rust 核心、OCR 模型和 OpenCC 词典。

## Android 构建

首次生成原生工程：

```bash
npm run tauri:android:init -- --ci
```

默认构建 `arm64-v8a` 的无签名 debug APK：

```bash
npm run tauri:android:build -- --debug --apk --ci
```

连接相同 ABI 的设备进行开发：

```bash
npm run tauri:android:dev -- aarch64
```

该命令通过 Rust xtask：

1. 使用宿主 ONNX Runtime 验证 OCR 模型；
2. 下载并校验 ONNX Runtime Android `1.24.3` AAR；
3. 提取目标 ABI 的 `libonnxruntime.so`；
4. 复制到生成工程的 `app/src/main/jniLibs/<abi>/`；
5. 设置 `ORT_LIB_PATH` 与 `ORT_PREFER_DYNAMIC_LINK=1` 后调用 Tauri build。

离线时可设置 `EPUB_TOOL_ORT_ANDROID_ARCHIVE` 指向已下载且校验和匹配的 AAR。

## iOS 构建

首次生成原生工程：

```bash
npm run tauri:ios:init -- --ci
```

构建 arm64 simulator app：

```bash
npm run tauri:ios:build -- aarch64-sim --debug --ci
```

在 arm64 simulator 开发：

```bash
npm run tauri:ios:dev -- aarch64-sim
```

该命令验证 OCR 模型，下载并校验 ONNX Runtime iOS `1.24.3` xcframework，设置 `ORT_IOS_XCFWK_PATH` 后调用 Tauri build。离线时可设置 `EPUB_TOOL_ORT_IOS_ARCHIVE` 指向已下载且校验和匹配的归档。

只验证 device Rust library、避免进入签名 archive/export：

```bash
cargo run --locked --manifest-path xtask/Cargo.toml -- prepare-mobile-ort ios
ORT_IOS_XCFWK_PATH="$PWD/src-tauri/.mobile-runtime/onnxruntime-c-1.24.3/onnxruntime.xcframework" \
  cargo build --locked --manifest-path src-tauri/Cargo.toml \
  --target aarch64-apple-ios --lib
```

## Cargo 排查

若 Tauri 提示找不到 Cargo，在同一终端确认：

```bash
cargo --version
```

使用 Rustup 时重新加载其环境，或重新打开终端/IDE。移动编译失败时还需确认对应 Rust target、Android SDK/NDK 或完整 Xcode 已安装；缺失平台工具链时不能用宿主 `cargo check` 代替真实目标链接验证。
