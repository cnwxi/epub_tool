# Task Protocol

Python 后端与 Tauri 壳层之间采用 JSON Lines 协议。

## 请求结构

```json
{
  "task_id": "uuid-or-custom-id",
  "task_type": "reformat",
  "input_files": ["/abs/path/book.epub"],
  "output_dir": "/abs/path/output",
  "options": {}
}
```

`task_type` 当前支持：

- `reformat`
- `decrypt`
- `encrypt`
- `font_encrypt`
- `transfer_img`

## 运行时事件

每一行都是一个 JSON 对象，核心字段如下：

```json
{
  "event": "task.file.started",
  "task_id": "demo",
  "status": "running",
  "progress": 0,
  "message": "开始处理 book.epub",
  "current_file": "/abs/path/book.epub",
  "current_index": 1,
  "total_files": 3,
  "output_path": "/abs/path/book_reformat.epub",
  "level": "info"
}
```

常见事件：

- `task.started`
- `task.log`
- `task.file.started`
- `task.file.finished`
- `task.finished`
- `task.stderr`

## 最终结果

```json
{
  "ok": true,
  "status": "success",
  "outputs": ["/abs/path/book_reformat.epub"],
  "errors": [],
  "skipped": [],
  "summary": {
    "total": 1,
    "success": 1,
    "failed": 0,
    "skipped": 0
  },
  "log_path": "/repo/log.txt"
}
```

## 字体加密选项

```json
{
  "options": {
    "target_font_families_by_file": {
      "/abs/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}
```

