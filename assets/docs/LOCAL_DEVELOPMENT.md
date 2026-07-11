# 本地开发

本文说明 Epub Tool 桌面端在 macOS、Windows 和 Linux 上的开发环境配置、启动方式与常见排查。

桌面开发会同时运行 Vite、Tauri/Rust 和 Python sidecar；因此只安装 Node.js 和 Python
还不够，必须先让终端能找到 Rust 的 `cargo` 命令。

## 前置依赖

| 依赖 | 用途 | 建议版本 / 验证命令 |
| --- | --- | --- |
| Node.js | 前端与 Tauri CLI | `24.18.0`（见 `.nvmrc`），`node --version` |
| npm | 安装与运行前端、Tauri CLI | `npm --version` |
| Rust stable（含 Cargo） | 编译和启动 Tauri 壳层 | `rustc --version`、`cargo --version` |
| Python | Python sidecar 与 EPUB 处理 | Python `3.12`，`python --version` |
| Conda | 隔离 Python 运行环境 | `conda --version` |

## macOS 系统依赖

macOS 必须先安装 Apple 的 Command Line Tools；它提供 Rust 编译所需的系统编译器，
没有 Homebrew 替代项：

```bash
xcode-select --install
```

随后从下面两种方式中选择一种安装 Node.js、Conda 和 Rust。不要同时用 Rustup 与
Homebrew 安装 Rust，以免终端优先找到非预期的 `cargo`。

### 方式一：原生/官方安装

1. 从 [Node.js](https://nodejs.org/) 安装 `24.18.0`，或安装
   [nvm](https://github.com/nvm-sh/nvm) 后在本仓库执行 `nvm use`。
2. 从 [Miniforge](https://github.com/conda-forge/miniforge) 安装 Conda。
3. 通过 Rustup 安装 Rust stable 工具链：

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustup default stable
```

Rustup 安装脚本完成后，请重新打开一个终端，或在当前终端再次执行
`source "$HOME/.cargo/env"`。以下命令都应能输出版本号：

```bash
xcode-select -p
rustc --version
cargo --version
```

若 Miniforge 安装后当前终端找不到 `conda`，执行 `conda init zsh` 后重新打开终端。

### 方式二：Homebrew

已安装 Homebrew 时，可用以下命令安装项目依赖：

```bash
brew install node@24 rust
brew install --cask miniforge
# node@24 是 versioned formula，需将其加入 zsh 的 PATH
echo 'export PATH="$(brew --prefix node@24)/bin:$PATH"' >> ~/.zshrc
```

安装完成后重新打开终端，并初始化 Conda：

```bash
conda init zsh
exec zsh -l
node --version
conda --version
rustc --version
cargo --version
```

Homebrew 的 `node@24` 会提供当期的 Node.js 24 补丁版本；如需严格复现 `.nvmrc` 中的
`v24.18.0`，请改用上面的 nvm 方式。无论使用哪种安装方式，运行 `npm run tauri:dev` 的
同一个终端中，`cargo --version` 必须成功。

## Windows 系统依赖

请在原生 Windows 的 PowerShell 或 Windows Terminal 中开发（本项目的桌面端不是在
WSL 中运行）。先完成以下配置：

1. 安装 [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)，并在安装器中勾选 **Desktop development with C++** 工作负载。
2. Windows 10 1803 及更高版本通常已自带 Microsoft Edge WebView2；其他系统请安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。
3. 安装 Rustup 与 MSVC Rust 工具链：

```powershell
winget install --id Rustlang.Rustup
# 完成安装后关闭并重新打开 PowerShell
rustup default stable-msvc
rustc --version
cargo --version
```

Rustup 安装器中应选择与系统架构对应的 `*-pc-windows-msvc` 默认工具链。若执行
`cargo --version` 时提示命令不存在，请关闭并重新打开终端和 IDE；Rustup 会将其
安装目录加入新的用户 `PATH`。

## Linux 系统依赖

Linux 需额外安装 WebKitGTK、编译工具和 Tauri 的系统库。以 Debian/Ubuntu 为例：

```bash
sudo apt update
sudo apt install libwebkit2gtk-4.1-dev \
  build-essential \
  curl \
  wget \
  file \
  libxdo-dev \
  libssl-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"
rustup default stable
rustc --version
cargo --version
```

Arch、Fedora、openSUSE、Alpine 等发行版的包名不同，请按
[Tauri Linux 前置依赖说明](https://v2.tauri.app/start/prerequisites/#linux) 安装对应发行版的包。
运行桌面程序时还需要图形桌面会话；纯服务器或纯 SSH 环境不能直接启动 Tauri 窗口。

## 安装项目依赖

先确保已安装与 `.nvmrc` 匹配的 Node.js 版本，以及 Conda（推荐
[Miniforge](https://github.com/conda-forge/miniforge)）。Windows 上可直接用 Node.js 安装包；
macOS/Linux 若使用 `nvm`，请先切换到项目声明的 Node.js 版本：

```bash
source "$HOME/.nvm/nvm.sh"
nvm use
node --version
```

然后创建 Python 环境并安装全部依赖：

```bash
conda create -n epub_tool python=3.12 -y
conda activate epub_tool
python -m pip install -r requirements/requirements.txt pyinstaller
npm install
npm --prefix frontend install
```

Windows 若首次在 PowerShell 中使用 Conda，可能需要先执行一次
`conda init powershell`，然后重新打开 PowerShell，再执行上述命令。

可用下面的检查确认当前 shell 已具备启动条件：

```bash
node --version
npm --version
python --version
conda --version
rustc --version
cargo --version
```

## 启动桌面开发环境

```bash
npm run tauri:dev
```

这会自动完成以下工作：

1. 预构建或复用 Python sidecar
2. 启动前端开发服务器
3. 启动 Tauri 桌面壳层

首次启动会编译 Rust 依赖和 Python sidecar，耗时会明显更长；后续启动通常会复用
已有构建产物。

## `cargo metadata` 报错

如果启动时出现下面的错误：

```text
failed to run 'cargo metadata' command ... No such file or directory (os error 2)
```

这表示 Tauri CLI 找不到 `cargo` 可执行文件，尚未进入项目代码或 Python 依赖的执行阶段。
在 macOS/Linux 上，按下面顺序处理：

```bash
# 1. 确认当前终端是否能找到 Cargo
cargo --version

# 2. 若命令不存在，载入 rustup 安装的环境变量
source "$HOME/.cargo/env"
cargo --version

# 3. 若仍不存在，按“前置依赖”安装 Rust 后重开终端
```

Windows 上应先重新打开 PowerShell 或 IDE；若仍失败，确认 Rustup 的安装目录已在用户
`PATH` 中，并执行 `rustup default stable-msvc` 后再次检查 `cargo --version`。

确认 `cargo --version` 成功后，重新激活 Python 环境并启动：

```bash
conda activate epub_tool
npm run tauri:dev
```

如果 IDE 内置终端中仍找不到 `cargo`，请完全退出并重新打开 IDE，确保它继承了更新后的
`PATH`（macOS/Linux 通常为 `$HOME/.cargo/bin`）。

## 仅启动前端界面

```bash
npm run dev
```

这个命令只启动 `frontend/` 下的 Vite 页面，用于样式开发或静态界面调试。没有 Tauri Runtime 时，任务执行、目录扫描、日志定位等桌面能力不会生效。

## 单独调试 Python 处理逻辑

```bash
conda run -n epub_tool python -m python_backend.cli run --task-type reformat --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type decrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type encrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type font_encrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type font_decrypt --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli run --task-type transfer_img --input-file ./book.epub
conda run -n epub_tool python -m python_backend.cli list-fonts ./book.epub
```

这些入口适合排障、协议验证和单功能调试，不是默认使用方式。

## 本地打包与二进制编译

打包使用本指南前文配置的 Node.js、Rust/Cargo、Python 3.12 与 Conda 环境。开始前请确认：

```bash
node --version
python --version
cargo --version
```

Node.js 应与 `.nvmrc` 一致；Python 必须使用已安装项目依赖的 `epub_tool` Conda 环境。

### 安装打包依赖

首次打包或 Python 依赖发生变化时，在仓库根目录执行：

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements.txt pyinstaller
npm install
npm --prefix frontend install
```

### 构建 Python sidecar 二进制

Python 后端会通过 PyInstaller 编译为当前平台的单文件 sidecar，输出到：

- macOS / Linux：`src-tauri/binaries/epub-tool-python`
- Windows：`src-tauri/binaries/epub-tool-python.exe`

可单独构建并验证该二进制：

```bash
conda run -n epub_tool npm run build:python-sidecar
```

### 构建桌面安装包

```bash
conda run -n epub_tool npm run build:bundle-assets
conda run -n epub_tool npm run tauri:build
```

`tauri:build` 会执行 Tauri 打包；在 macOS 上还会生成 DMG。打包流程会自动完成：

1. 构建前端资源
2. 校验已提交的内置 ONNX OCR 模型
3. 构建 ONNX-only Python sidecar
4. 准备 `src-tauri/bundle-resources/`
5. 执行 Tauri 打包

`font_decrypt` 默认使用随应用内置的 `PP-OCRv6_small_rec_onnx` 模型。模型文件位于
`src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`，落盘约 20 MiB。
该模型会在构建时直接打包进桌面安装包，运行时无需下载，也不会加载 Paddle Python 运行时。

如需本地验证高准确率模型，可设置 `EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec`
后重新准备模型并转换为 ONNX。GitHub Release 当前只发布 `_small` 安装包；
本地构建与 Homebrew 安装也默认使用 small 版，Homebrew 会自动匹配 Intel / Apple Silicon 架构。

OCR 默认最低置信度为 `0.8`。桌面 UI 支持将阈值下调至 `0`，并会随任务请求显式传入。
默认字符筛选策略为 `strict`，适合处理本工具生成的字体混淆 EPUB，
可识别同宽码位池混淆后的半角/全角拉丁字母和数字。处理外部混淆工具生成的文件时，
可将 `ocr_char_policy` 设为 `compatible`。该模式保留 `strict` 的识别范围，
并对用户选中目标字体命中的文本放宽筛选，允许更多非 ASCII 可见字符进入 OCR，
但仍排除空白、控制字符、真实中文标点、ASCII 标点和普通符号。需要注意，
识别范围扩大后，目标字体下的真实特殊符号也可能被 OCR 改写。

当 OCR 结果低于阈值、为空、非单字或发生异常时，正文会写入带 `ocr-failure`
class 的可视化占位，例如 `[字形缩略图 OCR_LOW_CONF]`。对应缩略图会保存到
`Images/ocr-failures/{font_hash}_U-E000_OCR_LOW_CONF.png`，HTML 属性会保留 `U+XXXX`、
状态码、字体路径和失败原因，便于人工回查和脚本统计。

输出 EPUB 会跳过目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，
避免混淆字体继续影响阅读器显示和后续文本比对。

默认构建不会下载 Paddle 源模型，也不会执行 Paddle2ONNX 转换。只有维护者需要刷新
已提交的 ONNX 模型时，才安装转换依赖并运行：

```bash
conda run -n epub_tool python -m pip install -r requirements/requirements-ocr-conversion.txt
conda run -n epub_tool npm run maintenance:fetch-ocr-model
conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```
