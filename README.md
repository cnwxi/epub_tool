# Epub Tool

<p align="center">
  <img src="./img/icon.ico" alt="Epub Tool Icon" width="120">
</p>

<p align="center">
  <a href="https://github.com/cnwxi/epub_tool/releases/latest">
    <img src="https://img.shields.io/github/v/release/cnwxi/epub_tool" alt="GitHub Releases">
  </a>
  <a href="https://github.com/cnwxi/epub_tool/stargazers">
    <img src="https://img.shields.io/github/stars/cnwxi/epub_tool" alt="GitHub stars">
  </a>
  <a href="https://github.com/cnwxi/epub_tool/network/members">
    <img src="https://img.shields.io/github/forks/cnwxi/epub_tool" alt="GitHub forks">
  </a>
  <a href="https://github.com/cnwxi/homebrew-tap">
    <img src="https://img.shields.io/badge/homebrew-cnwxi%2Ftap-FBB040" alt="Homebrew Tap">
  </a>
</p>

一个面向 EPUB 批量处理的桌面工具。当前主入口已经切换到 `Tauri 2 + Vue 3 + TypeScript + Python sidecar`，围绕“批量导入、统一执行、结果回看、日志定位”组织桌面工作流。文件解密/加密功能处理的是 EPUB 内文件名与资源引用混淆，不提供 DRM 内容解密。

支持的处理能力：

- `reformat`：重构 EPUB 目录结构、OPF 清单与资源引用，标准化文件布局
- `decrypt`：还原 EPUB 内文件名与资源引用混淆，不提供 DRM 内容解密
- `encrypt`：生成文件名与资源引用混淆版 EPUB
- `font_encrypt`：按每本 EPUB 单独选择字体 family，对内嵌字体与正文映射执行字形混淆
- `font_decrypt`：按每本 EPUB 单独选择字体 family，渲染混淆字形，经内置 ONNX OCR 识别后回写正文，并用可见占位符标记低置信度字符
- `transfer_img`：批量转换 EPUB 内 WEBP 图片为 PNG 或 JPEG，并同步更新 OPF 引用

## 当前桌面版实现

### 功能执行页

- 支持拖入文件、拖入文件夹、系统文件选择、目录扫描
- 队列按任务类型独立保存，切换任务不会互相覆盖
- 每个任务分别保存默认输出目录
- 任务页内统一展示：
  - 待处理列表
  - 字体范围面板（`font_encrypt`、`font_decrypt`），字体解密页额外提供 OCR 字符范围与最低置信度设置
  - 处理日志
  - 最近一次执行摘要
- 处理过程中实时刷新进度、日志、成功/失败/跳过结果
- 支持直接打开处理日志和输出目录

### 设置页

- 自动打开输出目录
- 自动打开处理日志
- 启动时自动检查更新
- 历史记录保留数量设置
- 检查 GitHub Release 最新版本
- 打开当前日志文件与日志目录
- 查看并清理任务历史记录

### 关于页

- 汇总展示历史执行统计
- 展示当前六类处理能力说明
- 汇总各子功能默认输出目录
- 展示开发态与打包态日志路径说明

## 安装

### macOS（Homebrew）

```bash
brew tap cnwxi/tap
brew install --cask epub-tool-newui
```

更新：

```bash
brew upgrade --cask epub-tool-newui
```

### 手动下载

1. 从 [Releases](https://github.com/cnwxi/epub_tool/releases/latest) 下载对应系统的桌面包。
2. 安装并启动应用。

## 使用方式

1. 在左侧切换功能类型。
2. 拖入 EPUB、选择文件，或扫描目录收集 `.epub`。
3. 根据当前任务选择输出目录。
4. 若当前任务为 `font_encrypt` 或 `font_decrypt`，先为每本书选择需要参与处理的字体 family；`font_decrypt` 可按需调整 OCR 字符范围和最低置信度。
5. 点击“开始执行”，在结果区查看摘要、失败原因、跳过原因，并按需打开输出目录或日志文件。

## 日志与输出

- 开发环境默认写入仓库根目录的 `log.txt`
- 打包版默认写入系统应用日志目录
- 设置页与关于页都会显示当前日志位置或默认日志路径说明
- 任务完成后可按设置自动打开输出目录或日志文件

常见日志目录：

- Windows：`%LOCALAPPDATA%\com.cnwxi.epubtool.newui\logs\log.txt`
- macOS：`~/Library/Logs/com.cnwxi.epubtool.newui/log.txt`
- Linux：`~/.local/share/com.cnwxi.epubtool.newui/logs/log.txt`

## 本地开发

### 环境准备

```bash
conda create -n epub_tool python=3.12 -y
conda activate epub_tool
python -m pip install -r requirements.txt
npm install
npm --prefix frontend install
```

如果使用 `nvm`，先执行：

```bash
source "$HOME/.nvm/nvm.sh"
nvm use
```

### 启动桌面开发环境

```bash
npm run tauri:dev
```

这会自动完成以下工作：

1. 预构建或复用 Python sidecar
2. 启动前端开发服务器
3. 启动 Tauri 桌面壳层

### 仅启动前端界面

```bash
npm run dev
```

这个命令只启动 `frontend/` 下的 Vite 页面，用于样式开发或静态界面调试。没有 Tauri Runtime 时，任务执行、目录扫描、日志定位等桌面能力不会生效。

### 单独调试 Python 处理逻辑

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

## 本地打包

```bash
conda run -n epub_tool python -m pip install -r requirements.txt pyinstaller
conda run -n epub_tool npm run build:bundle-assets
conda run -n epub_tool npm run tauri:build
```

打包流程会自动完成：

1. 构建前端资源
2. 校验已提交的内置 ONNX OCR 模型
3. 构建 ONNX-only Python sidecar
4. 准备 `src-tauri/bundle-resources/`
5. 执行 Tauri 打包

`font_decrypt` 默认使用固定内置模型 `PP-OCRv6_small_rec_onnx`。模型文件位于
`src-tauri/bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/`，当前落盘约 20 MiB，
构建时会直接打进桌面安装包，运行时不再下载模型，也不加载 Paddle Python 运行时。
如需本地验证高准确率档，可设置 `EPUB_TOOL_OCR_MODEL_NAME=PP-OCRv6_medium_rec` 后重新准备模型并转换 ONNX。
GitHub Release 会同时提供 `_small` 和 `_medium` 两类安装包；本地构建默认仍只使用 small，
Homebrew 也继续安装 small 版。
`font_decrypt` 默认最低 OCR 置信度为 `0.8`；桌面 UI 可将阈值下调到 `0.4`，并会随任务请求显式传入。默认 OCR 字符筛选策略为 `strict`，适合处理本工具生成的字体混淆 EPUB，会识别同宽码位池混淆后的半角/全角拉丁字母数字。需要处理外部混淆工具生成的文件时，可将 `ocr_char_policy` 设为 `compatible`，该模式会保留 `strict` 的全部识别范围，并对用户选中的目标字体命中文本放宽筛选，额外允许非 ASCII 可见字符进入 OCR，但仍排除空白、控制字符、真实中文标点和 ASCII 标点/普通符号；识别面扩大后，目标字体作用下的真实特殊符号也可能被 OCR 改写。低于阈值、空结果、非单字结果或异常结果会在正文中写入带 `ocr-failure` class 的可视化占位，形如 `[字形缩略图 OCR_LOW_CONF]`；缩略图资源写入 `Images/ocr-failures/{font_hash}_U-E000_OCR_LOW_CONF.png`，HTML 属性中保留 `U+XXXX`、状态码、字体路径和失败原因，便于人工回查与脚本统计。输出 EPUB 会跳过目标反混淆字体文件，并同步清理 OPF manifest 与 CSS 中的目标字体引用，避免混淆字体继续影响阅读器显示和后续文本比对。

默认构建不会下载 Paddle 源模型，也不会执行 Paddle2ONNX 转换。只有维护者需要刷新已提交的 ONNX 模型时，才安装转换依赖并运行：

```bash
conda run -n epub_tool python -m pip install -r requirements-ocr-conversion.txt
conda run -n epub_tool npm run maintenance:fetch-ocr-model
conda run -n epub_tool npm run maintenance:convert-ocr-onnx
```

## 仓库结构

- `frontend/`：Vue 3 桌面前端
- `src-tauri/`：Tauri 壳层、命令桥接与打包配置
- `python_backend/`：统一 CLI、任务协议与运行器
- `utils/`：底层 EPUB 处理脚本
- `build_tool/`：sidecar 构建与资源准备脚本
- `doc/`：构建、协议与桥接说明
- `test/`：本地测试样本与验证素材

## 文档索引

- [`doc/README.md`](./doc/README.md)：文档总览
- [`doc/CLI_USAGE.md`](./doc/CLI_USAGE.md)：Python 后端 CLI 用法
- [`doc/TASK_PROTOCOL.md`](./doc/TASK_PROTOCOL.md)：前后端任务协议
- [`doc/TAURI_PYTHON_BRIDGE.md`](./doc/TAURI_PYTHON_BRIDGE.md)：Tauri 与 Python 桥接说明
- [`doc/BUILD_AND_BUNDLE.md`](./doc/BUILD_AND_BUNDLE.md)：本地构建、打包与发布说明

## 常见排查

- 处理失败时，先看“最近一次执行摘要”中的失败原因、跳过原因，再看“处理日志”
- 如果书籍结构异常，可先执行“格式化”再继续其他流程
- `font_encrypt` 只处理 EPUB 内已嵌入的字体，不处理系统字体
- `font_decrypt` 只使用内置 ONNX OCR 模型，不依赖系统 OCR 工具、Paddle Python 运行时或运行时联网下载
- `decrypt` 只还原文件名与资源引用混淆；如果 EPUB 内容本身被 DRM 或加密资源保护，工具无法还原明文
- 如果 `content.opf` 等关键文件缺失或异常，相关任务可能直接失败
- 反馈问题时，可使用 [问题反馈模板](https://github.com/cnwxi/epub_tool/issues/new?template=bug_report.yml)，建议同时提供：
  - 当前任务类型
  - 问题描述
  - 样本文件
  - `log.txt`

## 相关项目

引用本仓库进行二次开发并扩展功能的项目：

- [epub-gadget](https://github.com/wangyyyqw/epub-gadget)

## 更新日志

- [CHANGELOG.md](./CHANGELOG.md)

## 鸣谢

- [遥遥心航](https://tieba.baidu.com/home/main?id=tb.1.7f262ae1.5_dXQ2Jp0F0MH9YJtgM2Ew)
- [lgernier](https://github.com/lgernierO)
- [fontObfuscator](https://github.com/solarhell/fontObfuscator)
