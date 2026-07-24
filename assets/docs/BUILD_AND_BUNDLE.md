# Build And Bundle

## 构建目标

仓库当前使用 `Tauri + Vue + Python sidecar` 打包桌面版应用：

- 前端由 `frontend/` 构建
- 桌面壳层由 `src-tauri/` 构建
- Python 后端通过 `scripts/build_python_sidecar.py` 打包为内置 sidecar

## 本地开发

```bash
conda create -n epub_tool python=3.12 -y
conda activate epub_tool
conda run -n epub_tool python -m pip install -r requirements/requirements.txt
npm install
npm --prefix frontend install
npm run tauri:dev
```

## 本地打包

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements.txt pyinstaller
conda run -n epub_tool npm run build:bundle-assets
conda run -n epub_tool npm run tauri:build
```

打包时，桌面应用会优先调用目录式 sidecar `src-tauri/binaries/epub-tool-python/epub-tool-python(.exe)`；只有本地开发环境中 sidecar 不存在时，才会回退到系统 Python。
`scripts/build_python_sidecar.py` 只构建 ONNX Runtime 版 sidecar，不再收集 `paddle`、`paddleocr`、`paddlex`，也不保留 Paddle 回退模式。Paddle 相关依赖只存在于维护者刷新 ONNX 模型阶段，不参与默认构建。
依赖入口按角色拆分：`requirements/requirements-base.txt` 是 EPUB 处理基础依赖，`requirements/requirements-onnx.txt` 是冻结运行时 OCR 依赖，`requirements/requirements-ocr-conversion.txt` 只供官方 Paddle 模型转 ONNX 使用。默认 `requirements/requirements.txt` 只聚合 base + ONNX。

正式 bundle 前会自动生成一份独立的 `src-tauri/bundle-resources/` 资源目录：

- `bundle-resources/binaries/epub-tool-python/` 放当前平台的目录式 sidecar
- `bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/` 放默认 ONNX 识别模型

默认模型为 `PP-OCRv6_small_rec`，Paddle 源模型目录约 20 MiB，转换后的
`PP-OCRv6_small_rec_onnx/` 目录也约 20 MiB。该模型的 ONNX 输出仍为单路
`CTCLabelDecode` logits，可复用当前 CTC 解码器。
默认构建只校验已提交的 ONNX 模型，不下载 Paddle 源模型，也不执行 Paddle2ONNX 转换。GitHub Actions 在三端构建时同样只执行 `scripts/verify_ocr_onnx_models.py` 校验模型资源。

如需本地刷新默认 ONNX 模型，才需要安装维护期转换依赖并执行：

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements-ocr-conversion.txt
conda run -n epub_tool npm run maintenance:fetch-ocr-model
conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```

如需本地生成高准确率档，可执行：

```bash
EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec conda run -n epub_tool npm run maintenance:fetch-ocr-model
EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```

`PP-OCRv6_medium_rec` 不改变本地默认 bundle 资源，避免本地默认安装包重新回到 70 MiB 以上模型体积。
GitHub Actions 发布构建当前只发布 small 版产物：small 版内置
`PP-OCRv6_small_rec_onnx`，发布资产文件名以 `_small` 结尾；Homebrew Tap 继续选择
small 版安装包，并通过 cask `arch` 自动匹配 `macos_x64_small.dmg` 与
`macos_arm64_small.dmg`。medium 版发布矩阵已在 workflow 中注释，仍可按需在本地准备和验证。

## CI 构建矩阵

当前 [`.github/workflows/build.yml`](/Users/xavier/Codes/personal/epub_tool/.github/workflows/build.yml) 支持以下平台与架构：

- Linux x64
- Linux arm64
- Windows x64
- Windows arm64
- macOS x64
- macOS arm64

workflow 支持两种运行方式：

- 仅构建并上传 bundle artifact
- 构建并发布 GitHub Release

CI 只安装 `requirements/requirements.txt` 与 `pyinstaller`，随后执行 ONNX 模型校验和 `python -m unittest discover -s tests`。它不会安装 `requirements/requirements-ocr-conversion.txt`，也不会下载或转换 Paddle 源模型。

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

### 更新日志与发布正文

发布前必须在 `assets/docs/CHANGELOG.md` 新增对应记录。每个记录使用三级版本标题，格式为
`### 年份后两位.月.日`，例如 `### 26.7.23`；标题不包含同日修订后缀。因此，
`26.7.23-1` 与 `26.7.23-2` 都对应 `### 26.7.23`。

手动运行 `Build And Release` 工作流时：

- `release`：关闭时只构建并上传 artifact；开启时创建 GitHub Release。
- `version`：留空时使用 `Cargo.toml` 的版本号，也可传入不带或带 `v` 前缀的版本号。
- `release_type`：默认 `latest`，会发布正式版本并更新 Homebrew Tap；选择 `pre_release` 会创建 GitHub 预发布版本，不设为 Latest，也不会更新 Homebrew Tap。
- `body`：留空时，工作流会从 CHANGELOG 提取与发布版本匹配的记录，生成“版本信息”后再附加默认安装说明；填写后会完全覆盖自动生成的正文。

自动正文提取会移除发布版本中的第一个 `-` 及其后缀，再匹配对应的三级标题。找不到匹配记录时，发布会失败，避免产生没有版本说明的 Release。

## 维护注意事项

- 发布前先确认 `npm run build:python-sidecar` 能正常生成 ONNX-only sidecar
- 发布前可执行 `npm run build:verify-ocr-onnx-models` 检查已提交 ONNX 模型是否齐全且兼容 CTC 解码
- 发布前可执行 `npm run build:prepare-bundle-resources` 检查 bundle sidecar 资源是否齐全
- 发布前至少在目标平台上验证一次安装包启动、任务执行与输出目录打开
- 如需升级版本，只修改 `src-tauri/Cargo.toml`
