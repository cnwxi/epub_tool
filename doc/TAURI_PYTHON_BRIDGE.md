# Tauri Python Bridge

## 调用链

```text
Vue 组件
  -> invoke("run_epub_task")
  -> Rust command
  -> 优先调用 PyInstaller 生成的 python sidecar
  -> 若 sidecar 不存在且处于开发工作区，则回退 python -m python_backend.cli
  -> Python 输出 JSON Lines
  -> Rust 逐行读取并通过 Channel 推回前端
```

## Rust 侧职责

- 解析前端请求
- 优先查找 `src-tauri/binaries/epub-tool-python(.exe)` 或打包后资源目录中的 sidecar
- 仅在开发态回退到系统 Python：`python3` / `python` / Windows `py -3`
- 启动 sidecar 或 `python_backend.cli`
- 读取 stdout/stderr
- 将事件转发给前端
- 返回最终 `TaskResult`
- 递归扫描输入目录中的 `.epub` 文件，避免前端直接做本地文件系统递归

## Python 侧职责

- 将旧 `utils/` 包装成统一任务入口
- 统一计算输出路径
- 同时写 `log.txt` 和 stdout 事件
- 保持旧脚本逻辑不被重写

## 当前限制

- sidecar 需要在构建机额外安装 `pyinstaller`
- 当前只完成了 sidecar 构建骨架与运行时优先级，尚未做正式安装包实机验证
- 还未做任务取消
