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

一个面向 EPUB 批量处理的桌面工具，当前默认入口为 `Tauri 2 + Vue 3 + TypeScript + Python sidecar`。支持格式化、文件解密、文件加密、字体加密和图片转换，适合单本处理、批量处理和目录扫描。

## 功能概览

- 支持导入单个文件、多个文件或扫描目录中的 `.epub`
- 支持 `reformat / decrypt / encrypt / font_encrypt / transfer_img`
- 每个子功能独立保存自己的默认输出目录
- 支持查看处理日志、执行摘要、失败原因和跳过原因
- 支持自动检查 GitHub Release 更新

## 获取与使用

1. 从 [Releases](https://github.com/cnwxi/epub_tool/releases/latest) 下载对应系统的桌面版安装包或压缩包。
2. 安装并启动应用。若首次运行遇到系统安全提示，可在系统安全设置中手动允许应用继续打开；Mac 可参考 [Apple 官方说明](https://support.apple.com/zh-cn/guide/mac-help/mchleab3a043/mac)，Windows 若出现误报拦截，也需手动允许保留或运行。
3. 导入 EPUB 文件或扫描目录。
4. 选择当前功能的输出目录并执行任务。
5. 处理完成后，可在结果区打开输出文件夹，并查看失败或跳过原因。

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

### 启动桌面版

```bash
npm run tauri:dev
```

开发态会优先复用已有的 Python sidecar；仅当 sidecar 不存在时，才会自动构建。

### 单独调试 Python 处理逻辑

```bash
python utils/reformat_epub.py ./book.epub
python utils/decrypt_epub.py ./book.epub
python utils/encrypt_epub.py ./book.epub
python utils/encrypt_font.py ./book.epub
python utils/transfer_img.py ./book.epub
```

这些脚本入口主要用于排障与单功能调试，不作为默认使用入口。

## 本地打包

```bash
python -m pip install -r requirements.txt pyinstaller
npm run tauri:build
```

打包链会自动完成以下步骤：

1. 构建前端资源
2. 强制重建 Python sidecar
3. 准备 `src-tauri/bundle-resources/`
4. 执行 Tauri 打包

## 仓库结构

- `frontend/`：桌面前端界面
- `src-tauri/`：Tauri 壳层、命令桥接与打包配置
- `python_backend/`：统一 CLI、任务协议与运行器
- `utils/`：底层 EPUB 处理能力实现
- `build_tool/`：sidecar 构建与 bundle 资源准备脚本
- `doc/`：运行、协议与打包说明

## 文档索引

- [`doc/README.md`](./doc/README.md)：文档总览
- [`doc/CLI_USAGE.md`](./doc/CLI_USAGE.md)：Python 后端 CLI 用法
- [`doc/TASK_PROTOCOL.md`](./doc/TASK_PROTOCOL.md)：前后端任务协议
- [`doc/TAURI_PYTHON_BRIDGE.md`](./doc/TAURI_PYTHON_BRIDGE.md)：Tauri 与 Python 桥接说明
- [`doc/BUILD_AND_BUNDLE.md`](./doc/BUILD_AND_BUNDLE.md)：本地构建、打包与发布说明

## 常见排查

- 处理失败时，先查看结果区的失败原因、跳过原因和处理日志
- 如果书籍结构异常，可先尝试执行“格式化”再继续其他操作
- 字体加密仅处理 EPUB 内已嵌入的字体文件，不处理系统字体
- 如果 EPUB 内部缺少关键文件，例如 `content.opf`，相关任务可能直接失败
- 反馈问题时，建议一并提供样本文件、处理模式、结果区提示和 `log.txt`

## 相关项目

引用本仓库进行二次开发并扩展功能的项目：

- [epub-gadget](https://github.com/wangyyyqw/epub-gadget)

## 更新日志

- [CHANGELOG.md](./CHANGELOG.md)

## 鸣谢

- [遥遥心航](https://tieba.baidu.com/home/main?id=tb.1.7f262ae1.5_dXQ2Jp0F0MH9YJtgM2Ew)
- [lgernier](https://github.com/lgernierO)
- [fontObfuscator](https://github.com/solarhell/fontObfuscator)
