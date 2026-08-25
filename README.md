# Epub Tool Android

面向 Android 的 EPUB 批量处理工具，使用 Tauri 2、Vue 3、TypeScript 和 Rust。所有任务在应用进程内执行，不使用桌面端窗口、sidecar 或外部解释器。

支持的任务：文件重构、文件加密、文件解密、图片压缩、WebP 与常规图片转换、更换封面、简繁转换。

## 开发

```bash
npm ci
npm --prefix frontend ci
npm run tauri:android:init -- --ci
npm run tauri:android:dev
```

## 构建

```bash
npm run tauri:android:build -- aarch64 --split-per-abi --apk --ci
```

可用目标：`aarch64`、`armv7`、`x86_64`、`i686`，分别对应 `arm64-v8a`、`armeabi-v7a`、`x86_64`、`x86`。

Android 构建需要 JDK 17、Android SDK 36、NDK `29.0.13846066` 和对应 Rust target。

## 验证

```bash
npm run build
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo test --locked --manifest-path xtask/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
npm run protocol:generate
```
