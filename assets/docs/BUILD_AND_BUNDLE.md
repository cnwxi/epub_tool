# Android 构建与发布

GitHub Actions 仅构建四种 Android ABI，并在配置 keystore 时签名 APK；未配置 keystore 时使用临时签名，仅用于验证。

```bash
npm run tauri:android:build -- aarch64 --split-per-abi --apk --ci
```

| Tauri target | Android ABI |
| --- | --- |
| `aarch64` | `arm64-v8a` |
| `armv7` | `armeabi-v7a` |
| `x86_64` | `x86_64` |
| `i686` | `x86` |

正式升级需要长期保存的 Android keystore；没有签名凭据时只能声明编译验证。
