# 构建、打包与发布

## 构建组成

应用由 Vue 前端、Tauri 壳层、统一 Rust EPUB 核心和运行资源组成：

- Windows、macOS、Linux、Android、iOS 都将同一 Rust 核心链接进应用进程；
- 所有平台携带 `PP-OCRv6_small_rec_onnx` 与 OpenCC 词典；
- Protobuf 只用于 Tauri IPC，业务核心使用类型化 `TaskSpec`、`TaskOptions`、`TaskEvent`、`TaskResult`。

## 桌面构建

```bash
npm ci
npm --prefix frontend ci
npm run build:bundle-assets
npm run tauri:build
```

桌面 `beforeBuildCommand` 会执行 `build:bundle-assets`：

1. 构建 Vue 前端；
2. 以真实 Rust ONNX Runtime session 校验 OCR 模型；
3. 由 Tauri 生成包含 Rust 核心的目标平台 bundle；macOS 会先准备官方 ONNX Runtime xcframework 并静态链接其通用切片，因此 Intel 与 Apple Silicon 都不依赖 `ort-sys` 的预编译下载。

发布 workflow 的桌面矩阵：

| 平台 | 架构 | Bundle |
| --- | --- | --- |
| Linux | x64、arm64 | deb、rpm |
| Windows | x64、arm64 | NSIS |
| macOS | x64、arm64 | app、DMG |

当前 macOS 配置使用 ad-hoc identity，Windows 安装包也未配置生产证书。CI 能验证构建和打包；正式代码签名、公证和信誉链需要仓库外凭据。

## 移动 ONNX Runtime

`xtask` 固定并校验以下官方 ONNX Runtime `1.24.3` 归档：

| 平台 | 官方归档 | SHA-256 | 切片 / ABI |
| --- | --- | --- | --- |
| Android | `onnxruntime-android-1.24.3.aar` | `67397e4a970e75617f765d2015ceaf911917e1d822276cfb5792744e8085cbce` | arm64-v8a、armeabi-v7a、x86、x86_64 |
| iOS | `onnxruntime-c-1.24.3.zip` | `b7eedc45932bac758ffd057cac0feb3f682269e47750b159e4c865145cbf0a8e` | ios-arm64、ios-arm64_x86_64-simulator |

来源：

- Android：`https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.24.3/onnxruntime-android-1.24.3.aar`
- iOS：`https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.24.3.zip`

Android 使用动态 `libonnxruntime.so`，由 `ORT_LIB_PATH` 与 `ORT_PREFER_DYNAMIC_LINK=1` 驱动 `ort-sys`，并复制到生成工程 `jniLibs`。iOS 使用静态 xcframework，由 `ORT_IOS_XCFWK_PATH` 驱动 `ort-sys`。生成和下载内容位于已忽略的 `src-tauri/.mobile-runtime/`。

## Android

构建环境：JDK 17、Android SDK 36、NDK `29.0.13846066`、Rustup 和目标 Rust standard library。最低 API 为 24。

```bash
npm run tauri:android:init -- --ci
npm run tauri:android:build -- --debug --apk --ci
```

目标映射：

| Tauri target | Rust target | APK ABI |
| --- | --- | --- |
| `aarch64` | `aarch64-linux-android` | `arm64-v8a` |

本地与 CI 均固定构建 `aarch64`（`arm64-v8a`），避免生成包含全部 ABI 的 universal APK。CI 会检查 `lib/arm64-v8a/libonnxruntime.so` 已实际打入 APK。该 APK 是无签名编译产物；Play 发布需要 keystore、签名配置和商店凭据。

## iOS

iOS 最低版本为 15.1：

```bash
npm run tauri:ios:init -- --ci
npm run tauri:ios:build -- aarch64-sim --debug --ci
```

CI 执行两层验证：

- `aarch64-apple-ios`：直接编译 device Rust static library，验证 device slice 和 ORT 静态链接，不生成 IPA；
- `aarch64-apple-ios-sim`：通过 Tauri 构建 arm64 simulator app。

device archive、IPA export、TestFlight/App Store 上传需要完整 Apple Team、证书和 provisioning profile；没有这些凭据时不能声明 device 发布完成。

## 质量门槛

发布 workflow 在桌面和移动矩阵前执行：

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

安装包还应在目标系统上做启动、任务执行、输出、日志和真实 EPUB 回归。宿主测试不能替代 Android/iOS 目标链接、真实设备或签名验证。

## 版本与 Release

版本唯一来源是 `src-tauri/Cargo.toml` 的 `package.version`，Vite、Tauri 与 Release workflow 均读取该值。版本采用“年.月.日”形式，同日修订可加 `-1`、`-2` 后缀。

GitHub Release 发布桌面安装包和未签名的 `arm64-v8a` Android debug APK，命名为 `Epub.Tool.NewUI_{version}_android_arm64_small.apk`。iOS CI 产物仍仅用于编译验证。发布前在 `assets/docs/CHANGELOG.md` 添加对应版本记录。

Homebrew Cask 更新由 `xtask update-homebrew-cask` 完成，主发布和手动 fallback workflow 共用同一 Rust 实现。
