# CLI Usage

统一入口：`python -m python_backend.cli`

## 安装依赖

```bash
python -m pip install -r requirements.txt
```

## 查看帮助

```bash
python -m python_backend.cli --help
python -m python_backend.cli run --help
```

## 直接执行任务

```bash
python -m python_backend.cli run \
  --task-type reformat \
  --input-file /path/book.epub \
  --output-dir /path/output
```

```bash
python -m python_backend.cli run \
  --task-type encrypt \
  --input-file /path/book.epub
```

## 使用完整请求 JSON

```bash
python -m python_backend.cli run --request-json '{
  "task_id": "demo-task",
  "task_type": "font_encrypt",
  "input_files": ["/path/book.epub"],
  "output_dir": "/path/output",
  "options": {
    "target_font_families_by_file": {
      "/path/book.epub": ["KaiTi", "Source Han Serif SC"]
    }
  }
}'
```

## 列出字体 family

```bash
python -m python_backend.cli list-fonts /path/book.epub
```

说明：

- `run` 会输出 JSON Lines 事件流。
- 成功时退出码为 `0`，存在失败项时为 `1`。
- 日志文件固定写入仓库根目录 `log.txt`。

