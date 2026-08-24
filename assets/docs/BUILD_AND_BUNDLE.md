# 构建、打包与发布

## 构建组成

应用由 Vue 前端、Tauri 壳层、统一 Rust EPUB 核心和运行资源组成：

- Windows、macOS、Linux、Android、iOS 都将同一 Rust 核心链接进应用进程；
- 所有平台携带 OpenCC 词典；
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
2. 由 Tauri 生成包含 Rust 核心的目标平台 bundle。

发布 workflow 的桌面矩阵：

| 平台 | 架构 | Bundle |
| --- | --- | --- |
| Linux | x64、arm64 | deb、rpm |
| Windows | x64、arm64 | NSIS |
| macOS | x64、arm64 | app、DMG |

当前 macOS 配置使用 ad-hoc identity，Windows 安装包也未配置生产证书。CI 能验证构建和打包；正式代码签名、公证和信誉链需要仓库外凭据。

## 移动构建

移动端构建直接调用 Tauri，不再下载额外 OCR/ONNX Runtime 依赖。

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

本地与 CI 均固定构建 `aarch64`（`arm64-v8a`），避免生成包含全部 ABI 的 universal APK。该 APK 是无签名编译产物；Play 发布需要 keystore、签名配置和商店凭据。

## iOS

iOS 最低版本为 15.1：

```bash
npm run tauri:ios:init -- --ci
npm run tauri:ios:build -- aarch64-sim --debug --ci
```

CI 执行两层验证：

- `aarch64-apple-ios`：直接编译 device Rust static library，不生成 IPA；
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
```

安装包还应在目标系统上做启动、任务执行、输出、日志和真实 EPUB 回归。宿主测试不能替代 Android/iOS 目标链接、真实设备或签名验证。

## 版本与 Release

版本唯一来源是 `src-tauri/Cargo.toml` 的 `package.version`，Vite、Tauri 与 Release workflow 均读取该值。版本采用“年.月.日”形式，同日修订可加 `-1`、`-2` 后缀。

GitHub Release 发布桌面安装包和未签名的 `arm64-v8a` Android debug APK，命名为 `Epub.Tool.NewUI_{version}_android_arm64_small.apk`。iOS CI 产物仍仅用于编译验证。发布前在 `assets/docs/CHANGELOG.md` 添加对应版本记录。

Homebrew Cask 更新由 `xtask update-homebrew-cask` 完成，主发布和手动 fallback workflow 共用同一 Rust 实现。
