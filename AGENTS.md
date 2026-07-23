# AGENTS.md

本文件为 Codex (Codex.ai/code) 在当前仓库中工作时提供指引。

## 项目概览

面向 EPUB 批量处理的桌面工具，技术栈为 Tauri 2 + Vue 3 + TypeScript + Python sidecar。任务体系可持续扩展；当前已注册 `reformat_epub`、`decrypt_epub`、`encrypt_epub`、`encrypt_font`、`decrypt_font`、`webp_to_img` 等任务类型。

## 常用命令

```bash
# 启动完整桌面开发环境（构建 sidecar + 前端 + Tauri）
npm run tauri:dev

# 仅启动前端（无 Tauri Runtime，任务执行不可用）
npm run dev

# Python CLI 单独调试
conda run -n epub_tool python -m python_backend.cli run --task-type reformat_epub --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type decrypt_epub --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type encrypt_epub --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type encrypt_font --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type decrypt_font --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type webp_to_img --input-file ./book.epub
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

- **`frontend/`** — Vue 3 单页应用。`App.vue` 承载任务、队列、设置、历史记录和更新检查等页面状态；`SideNav`、`DropZone`、`TaskConsole` 提供主要界面组件；`useTaskBridge` 封装 IPC 调用，`usePersistentState` 提供 Tauri Rust store + localStorage 双层持久化。
- **`src-tauri/src/main.rs`** — Tauri 命令与 Python Worker 生命周期管理，包括任务执行、字体目标读取、输入解析、路径操作、状态持久化和 Worker 状态/重启配置。JSON 文件持久化到 `app-state.json`，损坏文件自动备份为 `.corrupt-{timestamp}` 后缀。
- **`src-tauri/tauri.conf.json`** — 开发 URL `localhost:5173`，透明窗口（macOS 毛玻璃效果），sidecar 从 `bundle-resources/binaries/` 打包，OCR 模型从 `bundle-resources/ocr-models/` 打包。
- **`python_backend/cli.py`** — Sidecar 的 CLI 入口。提供 `run`、`list-fonts`、`list-fonts-batch` 和常驻 Worker 使用的 `serve` 子命令；任务请求统一使用 `TaskRequest` 结构。
- **`python_backend/task_runner.py`** — 编排批量任务执行。按任务类型动态导入 `python_backend/services/` 下的处理模块，将其 `logger` 替换为 `BroadcastLogger`，同时写入 `log.txt` 和 stdout JSON Lines 事件。按 `{stem}_{suffix}.epub` 规则推断输出路径。
- **`python_backend/protocol.py`** — 数据类定义：`TaskRequest`、`TaskEvent`、`TaskResult`。
- **`python_backend/services/`** — EPUB 处理服务模块，按功能分为 `epub/`（格式化与文件加解密）、`font/`（字体加解密）、`image/`（图片转换、压缩、封面与图片处理共享逻辑）、`text/`（简繁转换）和 `utils/`（日志等跨领域共享工具）。任务模块对外暴露统一的 `run()` 入口，内部使用共享的 `logger` 对象，运行时由 `task_runner` 替换。
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
- `encrypt_font` 与 `decrypt_font` 的 options 使用 `target_font_families_by_file`（按文件指定字体）和 `target_font_families`（默认字体）；`decrypt_font` 默认使用内置 `PP-OCRv6_small_rec_onnx` ONNX OCR 模型，不加载 Paddle Python 运行时；`PP-OCRv6_medium_rec` 仅作为高准确率可选构建档。
- 应用版本号在 Vite 构建时从 `src-tauri/Cargo.toml` 读取，注入为 `__APP_VERSION__`。
- `app-state.json` 已被 gitignore；损坏时自动备份并重置为默认状态。
- 输出文件命名规则为 `{原文件名}_{任务脚本名}.epub`，如 `book_reformat_epub.epub`、`book_encrypt_epub.epub`；简繁转换额外使用方向后缀，如 `book_chinese_convert_sc.epub`、`book_chinese_convert_tc.epub`。

### 功能扩展与文案约束

- 新增任务必须沿用 `TaskRequest`、`TaskEvent`、`TaskResult` 统一协议，不为单个任务另建一套前后端通信格式。
- 新增任务时同步检查 Python CLI 与 `task_runner` 注册、前端 `TaskType` 与任务导航/概览、输出目录持久化、sidecar 打包依赖及相关测试。
- README、关于页和通用说明使用“各类任务”“处理能力”等可扩展表述，不使用“六类”“6 种”等固定数量描述。
- 关于页摘要只展示动态任务数量和稳定的能力概括，不在固定宽度摘要卡中罗列全部任务名称。
- 可增长的任务清单由功能概览、导航或对应任务页面承载；不要与内容基本固定的说明卡并排放置，避免新增模块后卡片高度和信息密度失衡。
- 任务专属参数和说明放在对应任务页面，关于页只描述统一工作流、协议和扩展方式。


## Codex执行规范
- 开辟新分支不要使用codex/前缀
