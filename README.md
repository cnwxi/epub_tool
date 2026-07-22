# Epub Tool

<p align="center">
  <img src="./assets/img/icon.ico" alt="Epub Tool Icon" width="120">
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

![Epub Tool 桌面端界面预览](./assets/img/epub_tool_newui.png)

支持的处理能力：

- `reformat`：重构 EPUB 目录结构、OPF 清单与资源引用，标准化文件布局
- `decrypt`：还原 EPUB 内文件名与资源引用混淆，不提供 DRM 内容解密
- `encrypt`：生成文件名与资源引用混淆版 EPUB
- `font_encrypt`：按每本 EPUB 单独选择字体 family，对内嵌字体与正文映射执行字形混淆
- `font_decrypt`：按每本 EPUB 单独选择字体 family，渲染混淆字形，经内置 ONNX OCR 识别后回写正文，并用可见占位符标记低置信度字符
- `transfer_img`：批量转换 EPUB 内 WEBP 图片为 PNG 或 JPEG，并同步更新 OPF 引用

## 当前桌面版实现

桌面版通过统一任务界面提供文件导入、参数配置、批量执行、进度与日志查看、结果回顾等能力。不同处理功能复用同一套前后端任务协议，并可按各自需求扩展配置和交互。

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

## 本地开发与编译

详见 [本地开发指南](./assets/docs/LOCAL_DEVELOPMENT.md)。其中包括 macOS、Windows、Linux 的
系统依赖，Python/Node.js 环境配置、桌面端启动、单独调试、打包依赖准备、Python sidecar
二进制编译、OCR 模型维护以及 `cargo metadata` 报错排查。

## 仓库结构

- `frontend/`：Vue 3 桌面前端
- `src-tauri/`：Tauri 壳层、命令桥接与打包配置
- `python_backend/`：统一 CLI、任务协议、运行器与 EPUB 处理服务
- `python_backend/services/`：各类底层 EPUB 处理实现与共享日志工具
- `scripts/`：sidecar 构建、资源准备与维护脚本
- `assets/docs/`：构建、协议与桥接说明
- `assets/img/`：README、前端与应用打包共用图像资源
- `tests/`：自动化测试
- `fixtures/`：本地测试样本与验证素材（默认不提交）

## 文档索引

- [`assets/docs/README.md`](./assets/docs/README.md)：文档总览
- [`assets/docs/LOCAL_DEVELOPMENT.md`](./assets/docs/LOCAL_DEVELOPMENT.md)：本地开发环境、启动、打包与排查
- [`assets/docs/CLI_USAGE.md`](./assets/docs/CLI_USAGE.md)：Python 后端 CLI 用法
- [`assets/docs/TASK_PROTOCOL.md`](./assets/docs/TASK_PROTOCOL.md)：前后端任务协议
- [`assets/docs/TAURI_PYTHON_BRIDGE.md`](./assets/docs/TAURI_PYTHON_BRIDGE.md)：Tauri 与 Python 桥接说明
- [`assets/docs/BUILD_AND_BUNDLE.md`](./assets/docs/BUILD_AND_BUNDLE.md)：本地构建、打包与发布说明

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

- [更新日志](./assets/docs/CHANGELOG.md)

## 鸣谢

- [遥遥心航](https://tieba.baidu.com/home/main?id=tb.1.7f262ae1.5_dXQ2Jp0F0MH9YJtgM2Ew)
- [lgernier](https://github.com/lgernierO)
- [fontObfuscator](https://github.com/solarhell/fontObfuscator)
