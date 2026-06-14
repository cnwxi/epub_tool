# Build And Bundle

## 构建目标

仓库当前使用 `Tauri + Vue + Python sidecar` 打包桌面版应用：

- 前端由 `frontend/` 构建
- 桌面壳层由 `src-tauri/` 构建
- Python 后端通过 `build_tool/build_python_sidecar.py` 打包为内置 sidecar

## 本地开发

```bash
conda run -n epub_tool python -m pip install -r requirements.txt
npm install
npm --prefix frontend install
npm run tauri:dev
```

## 本地打包

```bash
conda run -n epub_tool python -m pip install -r requirements.txt pyinstaller
conda run -n epub_tool npm run build:bundle-assets
npm run tauri:build
```

打包时，桌面应用会优先调用内置的 `src-tauri/binaries/epub-tool-python(.exe)`；只有本地开发环境中 sidecar 不存在时，才会回退到系统 Python。
`build_tool/build_python_sidecar.py` 只构建 ONNX Runtime 版 sidecar，不再收集 `paddle`、`paddleocr`、`paddlex`，也不保留 Paddle 回退模式。Paddle 相关依赖只存在于模型转换阶段，不参与默认构建。
依赖入口按角色拆分：`requirements-base.txt` 是 EPUB 处理基础依赖，`requirements-onnx.txt` 是冻结运行时 OCR 依赖，`requirements-paddle.txt` 只供 Paddle 源模型转 ONNX 使用。默认 `requirements.txt` 只聚合 base + ONNX。

正式 bundle 前会自动生成一份独立的 `src-tauri/bundle-resources/` 资源目录：

- `bundle-resources/binaries/` 只放当前平台的 sidecar
- `bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/` 放默认 ONNX 识别模型

默认模型为 `PP-OCRv6_small_rec`，Paddle 源模型目录约 20 MiB，转换后的
`PP-OCRv6_small_rec_onnx/` 目录也约 20 MiB。该模型的 ONNX 输出仍为单路
`CTCLabelDecode` logits，可复用当前 CTC 解码器。
默认构建只校验已提交的 ONNX 模型，不下载 Paddle 源模型，也不执行 Paddle2ONNX 转换。GitHub Actions 在三端构建时同样只执行 `build_tool/verify_ocr_onnx_models.py` 校验模型资源。

如需本地刷新默认 ONNX 模型，才需要安装构建期转换依赖并执行：

```bash
conda run -n epub_tool python -m pip install -r requirements-build-ocr.txt
conda run -n epub_tool npm run build:prepare-ocr-models
conda run -n epub_tool npm run build:prepare-ocr-onnx-models
```

如需本地生成高准确率档，可执行：

```bash
EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec conda run -n epub_tool npm run build:prepare-ocr-models
EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec conda run -n epub_tool npm run build:prepare-ocr-onnx-models
```

`PP-OCRv6_medium_rec` 不作为默认 bundle 资源，避免默认安装包重新回到 70 MiB 以上模型体积。

## CI 构建矩阵

当前 [`.github/workflows/build.yml`](/Users/xavier/Codes/personal/epub_tool/.github/workflows/build.yml) 支持以下平台与架构：

- Linux x64
- Windows x64
- macOS arm64

workflow 支持两种运行方式：

- 仅构建并上传 bundle artifact
- 构建并发布 GitHub Release

## 版本号与 Release

应用版本号统一以 `src-tauri/Cargo.toml` 中的 `package.version` 为唯一来源。

- 前端显示版本使用该值
- Tauri 应用版本使用该值
- workflow 默认会基于该值生成 GitHub Release 标签

版本号采用“年.月.日”作为基础编号，例如 `26.4.11` 表示 `2026-04-11` 当天的首个正式版本。

- `26.4.11`：当天首个正式版本
- `26.4.11-1`：同一天的第 1 次修订
- `26.4.11-2`：同一天的第 2 次修订
- `26.4.12`：下一天的新版本

注意：

- `Cargo.toml` 中不要添加 `v` 前缀，应写为 `26.4.11` 或 `26.4.11-1`
- GitHub Release 标签可使用 `v26.4.11`、`v26.4.11-1` 这类格式
- 当前项目约定中，`-1`、`-2` 表示同日修订版，而不是 `beta` 等预发布标记

## 维护注意事项

- 发布前先确认 `npm run build:python-sidecar` 能正常生成 ONNX-only sidecar
- 发布前可执行 `npm run build:verify-ocr-onnx-models` 检查已提交 ONNX 模型是否齐全且兼容 CTC 解码
- 发布前可执行 `npm run build:prepare-bundle-resources` 检查 bundle sidecar 资源是否齐全
- 发布前至少在目标平台上验证一次安装包启动、任务执行与输出目录打开
- 如需升级版本，只修改 `src-tauri/Cargo.toml`
