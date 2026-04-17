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
</p>

一个面向 EPUB 批量处理的桌面工具。当前主入口已经切换到 `Tauri 2 + Vue 3 + TypeScript + Python sidecar`，围绕“批量导入、统一执行、结果回看、日志定位”组织桌面工作流。

支持的处理能力：

- `reformat`：重构 EPUB 结构，标准化文件布局
- `decrypt`：处理文件名混淆
- `encrypt`：生成混淆版 EPUB
- `font_encrypt`：按每本 EPUB 单独选择字体范围并执行字体混淆
- `transfer_img`：批量转换 EPUB 内 WEBP 图片

## 当前桌面版实现

### 功能执行页

- 支持拖入文件、拖入文件夹、系统文件选择、目录扫描
- 队列按任务类型独立保存，切换任务不会互相覆盖
- 每个任务分别保存默认输出目录
- 任务页内统一展示：
  - 待处理列表
  - 字体范围面板（仅 `font_encrypt`）
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
- 展示当前五类处理能力说明
- 汇总各子功能默认输出目录
- 展示开发态与打包态日志路径说明

## 使用方式

1. 从 [Releases](https://github.com/cnwxi/epub_tool/releases/latest) 下载对应系统的桌面包。
2. 安装并启动应用。
3. 在左侧切换功能类型。
4. 拖入 EPUB、选择文件，或扫描目录收集 `.epub`。
5. 根据当前任务选择输出目录。
6. 若当前任务为 `font_encrypt`，先为每本书选择需要参与处理的字体 family。
7. 点击“开始执行”，在结果区查看摘要、失败原因、跳过原因，并按需打开输出目录或日志文件。

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
python -m python_backend.cli run --task-type reformat --input-file ./book.epub
python -m python_backend.cli run --task-type decrypt --input-file ./book.epub
python -m python_backend.cli run --task-type encrypt --input-file ./book.epub
python -m python_backend.cli run --task-type font_encrypt --input-file ./book.epub
python -m python_backend.cli run --task-type transfer_img --input-file ./book.epub
python -m python_backend.cli list-fonts ./book.epub
```

这些入口适合排障、协议验证和单功能调试，不是默认使用方式。

## 本地打包

```bash
python -m pip install -r requirements.txt pyinstaller
npm run tauri:build
```

打包流程会自动完成：

1. 构建前端资源
2. 构建 Python sidecar
3. 准备 `src-tauri/bundle-resources/`
4. 执行 Tauri 打包

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
- 如果 `content.opf` 等关键文件缺失或异常，相关任务可能直接失败
- 反馈问题时，建议同时提供：
  - 样本文件
  - 当前任务类型
  - 结果区提示
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
