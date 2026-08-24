# 构建、打包与发布

## 构建组成

应用由 Vue 前端、Tauri 壳层、统一 Rust EPUB 核心和运行资源组成：

- Windows、macOS、Linux、Android、iOS 都将同一 Rust 核心链接进应用进程；
- 所有平台携带 OpenCC 词典；
- Protobuf 只用于 Tauri IPC，业务核心使用类型化 `TaskSpec`、`TaskOptions`、`TaskEvent`、`TaskResult`。

## 本地桌面构建

```bash
npm ci
npm --prefix frontend ci
npm run build:bundle-assets
npm run tauri:build
```

桌面 `beforeBuildCommand` 会执行 `build:bundle-assets`：

1. 构建 Vue 前端；
2. 由 Tauri 生成包含 Rust 核心的目标平台 bundle。

GitHub Actions 不再构建桌面 bundle；该命令仅用于本地桌面打包。

## Android

构建环境：JDK 17、Android SDK 36、NDK `29.0.13846066`、Rustup 和目标 Rust standard library。最低 API 为 24。GitHub Actions 仅构建 Android ABI 矩阵，不再运行桌面或 iOS 打包。

```bash
npm run tauri:android:init -- --ci
npm run tauri:android:build -- aarch64 --split-per-abi --apk --ci
```

目标映射：

| Tauri target | Rust target | APK ABI |
| --- | --- | --- |
| `aarch64` | `aarch64-linux-android` | `arm64-v8a` |
| `armv7` | `armv7-linux-androideabi` | `armeabi-v7a` |
| `x86_64` | `x86_64-linux-android` | `x86_64` |
| `i686` | `i686-linux-android` | `x86` |

每个 target 单独生成无签名 release APK，避免把全部 ABI 打入 universal APK，并避免携带 Debug 符号。Play 发布需要 keystore、签名配置和商店凭据。

## 质量门槛

发布 workflow 在 Android ABI 矩阵前执行：

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

APK 还应在目标 Android 设备或模拟器上做启动、任务执行、导出、日志和真实 EPUB 回归。宿主测试不能替代 Android 目标链接、真实设备或签名验证。

## 版本与 Release

版本唯一来源是 `src-tauri/Cargo.toml` 的 `package.version`，Vite、Tauri 与 Release workflow 均读取该值。版本采用“年.月.日”形式，同日修订可加 `-1`、`-2` 后缀。

GitHub Release 发布 `arm64-v8a`、`armeabi-v7a`、`x86_64`、`x86` 四种未签名 Android release APK，命名为 `Epub.Tool.Android_{version}_android_{abi}_unsigned-release.apk`。发布前在 `assets/docs/CHANGELOG.md` 添加对应版本记录。
