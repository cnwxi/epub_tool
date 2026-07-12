# AGENTS.md

本文件为 Codex (Codex.ai/code) 在当前仓库中工作时提供指引。

## 项目概览

面向 EPUB 批量处理的桌面工具，技术栈为 Tauri 2 + Vue 3 + TypeScript + Python sidecar。支持六种任务类型：`reformat`、`decrypt`、`encrypt`、`font_encrypt`、`font_decrypt`、`transfer_img`。

## 常用命令

```bash
# 启动完整桌面开发环境（构建 sidecar + 前端 + Tauri）
npm run tauri:dev

# 仅启动前端（无 Tauri Runtime，任务执行不可用）
npm run dev

# Python CLI 单独调试
conda run -n epub_tool python -m python_backend.cli run --task-type reformat --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type decrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type encrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type font_encrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type font_decrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type transfer_img --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli list-fonts ./book.epub

# 仅构建 ONNX-only sidecar
npm run build:python-sidecar

# 生产打包
npm run tauri:build
```

Node 版本：`24.18.0`（见 `.nvmrc`）。Python 依赖见 `requirements/requirements.txt`。

## 架构

### 数据流

```
Vue 3 界面 ──invoke──> Rust (Tauri) ──spawn 子进程──> Python sidecar/后端
                          │                                    │
                          │  stdout/stderr 输出 JSON Lines      │
                          │<────────────────────────────────────│
                          │                                    │
           Tauri IPC channel                                    │
           推送 TaskEvent 到前端                                 │
```

### 各层职责

- **`frontend/`** — Vue 3 单页应用。单个大 `App.vue` 承载所有页面状态（队列、设置、历史记录、更新检查）。三个子组件：`SideNav`、`DropZone`、`TaskConsole`。两个 composable：`useTaskBridge`（封装 IPC 调用）、`usePersistentState`（双层持久化：Tauri Rust store + localStorage）。
- **`src-tauri/src/main.rs`** — 全部 Tauri 命令（共 9 个）：`run_epub_task`、`list_font_targets`、`resolve_input_sources`、`collect_epub_files`、`open_path`、`get_log_path`、`get_persisted_store_path`、`load_persisted_state`、`save_persisted_state`。JSON 文件持久化到 `app-state.json`，损坏文件自动备份为 `.corrupt-{timestamp}` 后缀。
- **`src-tauri/tauri.conf.json`** — 开发 URL `localhost:5173`，透明窗口（macOS 毛玻璃效果），sidecar 从 `bundle-resources/binaries/` 打包，OCR 模型从 `bundle-resources/ocr-models/` 打包。
- **`python_backend/cli.py`** — Sidecar 的 CLI 入口。两个子命令：`run`（通过 `--request-json` 或 `--request-file` 接收 TaskRequest JSON）、`list-fonts`（输出 EPUB 内嵌字体的 family 列表）。
- **`python_backend/task_runner.py`** — 编排批量任务执行。按任务类型动态导入 `python_backend/services/` 下的处理模块，将其 `logger` 替换为 `BroadcastLogger`，同时写入 `log.txt` 和 stdout JSON Lines 事件。按 `{stem}_{suffix}.epub` 规则推断输出路径。
- **`python_backend/protocol.py`** — 数据类定义：`TaskRequest`、`TaskEvent`、`TaskResult`。
- **`python_backend/services/`** — 六个 EPUB 处理模块（`reformat_epub.py`、`decrypt_epub.py`、`encrypt_epub.py`、`encrypt_font.py`、`decrypt_font.py`、`transfer_img.py`），各自对外暴露 `run()` 或等价入口，内部使用共享的 `logger` 对象，运行时由 task_runner 替换。另含 `log.py`（`logwriter` 类）。
- **`scripts/`** — `verify_ocr_onnx_models.py`（校验已提交 ONNX OCR 模型）、`prepare_ocr_models.py`（维护者刷新模型时准备官方 Paddle 源模型）、`prepare_ocr_onnx_models.py`（维护者刷新模型时转换 ONNX OCR 模型）、`build_python_sidecar.py`（PyInstaller `--onefile` 构建 ONNX-only sidecar）、`prepare_bundle_resources.py`（将 sidecar 复制到 `bundle-resources/`）。
- **`tests/`** — 自动化测试。
- **`fixtures/`** — 本地测试用 EPUB 样本（默认不提交）。
- **`assets/docs/`** — 协议、构建与 UI 设计规范文档。
- **`assets/img/`** — README、前端与 Tauri 打包共用的图像资源。

### Sidecar 查找顺序

1. `EPUB_TOOL_PYTHON_SIDECAR` 环境变量
2. `src-tauri/binaries/epub-tool-python`（开发工作区）
3. `<resource_dir>/binaries/epub-tool-python`（打包态）
4. 回退到系统 `python3 -m python_backend.cli`（仅开发态，需能定位工作区根目录）

### 关键设计细节

- Python sidecar 通过 stdout 输出 JSON Lines 协议通信。每行一个 `TaskEvent`。最后一行 `event: "task.finished"`，其中 `result` 字段包含完整 `TaskResult`。
- `python_backend/services/` 模块的 `logger` 属性在运行时会通过 `task_runner.patched_loggers()` 被替换——`BroadcastLogger` 同时写入 `log.txt` 和 stdout JSON Lines。
- `font_encrypt` 与 `font_decrypt` 的 options 使用 `target_font_families_by_file`（按文件指定字体）和 `target_font_families`（默认字体）；`font_decrypt` 默认使用内置 `PP-OCRv6_small_rec_onnx` ONNX OCR 模型，不加载 Paddle Python 运行时；`PP-OCRv6_medium_rec` 仅作为高准确率可选构建档。
- 应用版本号在 Vite 构建时从 `src-tauri/Cargo.toml` 读取，注入为 `__APP_VERSION__`。
- `app-state.json` 已被 gitignore；损坏时自动备份并重置为默认状态。
- 输出文件命名规则为 `{原文件名}_{后缀}.epub`，如 `book_reformat.epub`、`book_encrypt.epub`。


## Codex执行规范
- 开辟新分支不要使用codex/前缀