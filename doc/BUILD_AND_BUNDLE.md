# Build And Bundle

## 构建目标

仓库当前使用 `Tauri + Vue + Python sidecar` 打包桌面版应用：

- 前端由 `frontend/` 构建
- 桌面壳层由 `src-tauri/` 构建
- Python 后端通过 `build_tool/build_python_sidecar.py` 打包为内置 sidecar

## 本地开发

```bash
python -m pip install -r requirements.txt
npm install
npm --prefix frontend install
npm run tauri:dev
```

## 本地打包

```bash
python -m pip install -r requirements.txt pyinstaller
python build_tool/build_python_sidecar.py
npm run tauri:build
```

打包时，桌面应用会优先调用内置的 `src-tauri/binaries/epub-tool-python(.exe)`；只有本地开发环境中 sidecar 不存在时，才会回退到系统 Python。
正式 bundle 前会自动生成一份独立的 `src-tauri/bundle-resources/` 资源目录，只复制当前平台的 sidecar，避免把 `.gitkeep`、日志文件等无关文件打进安装包。

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

- 发布前先确认 `python build_tool/build_python_sidecar.py` 能正常生成 sidecar
- 发布前可执行 `python build_tool/prepare_bundle_resources.py` 检查 bundle 资源目录是否只包含当前平台 sidecar
- 发布前至少在目标平台上验证一次安装包启动、任务执行与输出目录打开
- 如需升级版本，只修改 `src-tauri/Cargo.toml`
