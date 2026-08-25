# Android 本地开发

项目当前只维护 Android 开发、构建和发布链路。

## 环境

- Node.js 版本见 `.nvmrc`
- JDK 17
- Android SDK 36
- Android NDK `29.0.13846066`
- Rust targets：`aarch64-linux-android`、`armv7-linux-androideabi`、`x86_64-linux-android`、`i686-linux-android`

## 命令

```bash
npm ci
npm --prefix frontend ci
npm run tauri:android:init -- --ci
npm run tauri:android:dev
npm run tauri:android:build -- aarch64 --split-per-abi --apk --ci
```

文件选择通过 Android URI 进入应用，处理结果使用导出操作写回用户选择的位置。应用内 Rust 引擎负责全部 EPUB 处理。
