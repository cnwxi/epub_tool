#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::{BufRead, BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, Stdio},
};
use tauri::{ipc::Channel, AppHandle, Manager};

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

const SIDECAR_NAME: &str = if cfg!(target_os = "windows") {
    "epub-tool-python.exe"
} else {
    "epub-tool-python"
};

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FrontendTaskRequest {
    task_id: String,
    task_type: String,
    input_files: Vec<String>,
    output_dir: Option<String>,
    #[serde(default)]
    options: Value,
}

#[derive(Debug, Serialize, Deserialize)]
struct FontTargetResponse {
    ok: bool,
    input_file: String,
    font_families: Vec<String>,
}

fn workspace_root() -> Option<PathBuf> {
    if !cfg!(debug_assertions) {
        return None;
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let root = manifest_dir.parent()?.to_path_buf();
    root.exists().then_some(root)
}

fn resolve_runtime_root(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(root) = workspace_root() {
        return Ok(root);
    }

    if let Ok(dir) = app.path().app_data_dir() {
        fs::create_dir_all(&dir)
            .map_err(|error| format!("创建应用数据目录失败 {}: {error}", dir.display()))?;
        return Ok(dir);
    }

    app.path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录: {error}"))
}

fn resolve_log_path(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(root) = workspace_root() {
        return Ok(root.join("log.txt"));
    }

    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|error| format!("无法定位日志目录: {error}"))?;
    fs::create_dir_all(&log_dir)
        .map_err(|error| format!("创建日志目录失败 {}: {error}", log_dir.display()))?;
    Ok(log_dir.join("log.txt"))
}

fn resolve_path(app: &AppHandle, path: &str) -> Result<PathBuf, String> {
    let path_buf = PathBuf::from(path);
    if path_buf.is_absolute() {
        return Ok(path_buf);
    }

    if path == "log.txt" {
        return resolve_log_path(app);
    }

    if let Some(root) = workspace_root() {
        return Ok(root.join(path_buf));
    }

    Ok(resolve_runtime_root(app)?.join(path_buf))
}

fn is_external_target(target: &str) -> bool {
    let lower = target.to_ascii_lowercase();
    lower.starts_with("https://") || lower.starts_with("http://")
}

fn collect_epubs_recursive(directory: &Path, result: &mut Vec<String>) -> Result<(), String> {
    let entries = fs::read_dir(directory)
        .map_err(|error| format!("读取目录失败 {}: {error}", directory.display()))?;

    for entry in entries {
        let entry = entry.map_err(|error| format!("读取目录项失败: {error}"))?;
        let path = entry.path();
        if path.is_dir() {
            collect_epubs_recursive(&path, result)?;
            continue;
        }
        let is_epub = path
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("epub"))
            .unwrap_or(false);
        if is_epub {
            result.push(path.to_string_lossy().to_string());
        }
    }

    Ok(())
}

fn sidecar_candidates(app: &AppHandle) -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    if let Ok(explicit_path) = std::env::var("EPUB_TOOL_PYTHON_SIDECAR") {
        if !explicit_path.is_empty() {
            candidates.push(PathBuf::from(explicit_path));
        }
    }

    if let Some(root) = workspace_root() {
        candidates.push(root.join("src-tauri").join("binaries").join(SIDECAR_NAME));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("binaries").join(SIDECAR_NAME));
    }

    candidates
}

#[cfg(unix)]
fn ensure_executable_permission(path: &Path) -> Result<(), String> {
    let metadata = fs::metadata(path)
        .map_err(|error| format!("读取 sidecar 权限失败 {}: {error}", path.display()))?;
    let current_mode = metadata.permissions().mode();
    if current_mode & 0o111 != 0 {
        return Ok(());
    }

    let mut permissions = metadata.permissions();
    permissions.set_mode(current_mode | 0o755);
    fs::set_permissions(path, permissions)
        .map_err(|error| format!("修复 sidecar 可执行权限失败 {}: {error}", path.display()))
}

#[cfg(not(unix))]
fn ensure_executable_permission(_path: &Path) -> Result<(), String> {
    Ok(())
}

fn resolve_sidecar(app: &AppHandle) -> Result<Option<PathBuf>, String> {
    for path in sidecar_candidates(app) {
        if path.is_file() {
            ensure_executable_permission(&path)?;
            return Ok(Some(path));
        }
    }

    Ok(None)
}

fn system_python_candidates() -> Vec<(String, Vec<String>)> {
    let mut candidates = vec![
        ("python3".to_string(), vec![]),
        ("python".to_string(), vec![]),
    ];

    if cfg!(target_os = "windows") {
        candidates.insert(0, ("py".to_string(), vec!["-3".to_string()]));
    }

    candidates
}

fn resolve_system_python() -> Result<(String, Vec<String>), String> {
    for (bin, prefix) in system_python_candidates() {
        let status = Command::new(&bin)
            .args(prefix.iter())
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();

        if let Ok(status) = status {
            if status.success() {
                return Ok((bin, prefix));
            }
        }
    }

    Err("未找到可用的系统 Python 运行时，请先安装 python3 或 python。".into())
}

fn build_backend_command(app: &AppHandle, subcommand: &str) -> Result<Command, String> {
    let log_path = resolve_log_path(app)?;
    let work_dir = resolve_runtime_root(app)?;

    if let Some(sidecar_path) = resolve_sidecar(app)? {
        let mut command = Command::new(sidecar_path);
        command.current_dir(&work_dir);
        command.arg(subcommand);
        configure_backend_command(&mut command, &log_path);
        return Ok(command);
    }

    let workspace = workspace_root().ok_or_else(|| {
        "未找到内置 Python sidecar，且当前运行环境也不是开发工作区，无法回退系统 Python。"
            .to_string()
    })?;
    let (bin, mut prefix) = resolve_system_python()?;
    prefix.extend([
        "-m".to_string(),
        "python_backend.cli".to_string(),
        subcommand.to_string(),
    ]);

    let mut command = Command::new(bin);
    command.current_dir(workspace);
    command.args(prefix);
    configure_backend_command(&mut command, &log_path);
    Ok(command)
}

fn configure_backend_command(command: &mut Command, log_path: &Path) {
    command.env("EPUB_TOOL_LOG_PATH", log_path);
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONIOENCODING", "utf-8");
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
}

fn stream_utf8ish_lines<R, F>(reader: R, mut on_line: F) -> Result<(), String>
where
    R: Read,
    F: FnMut(String) -> Result<(), String>,
{
    let mut reader = BufReader::new(reader);

    loop {
        let mut buffer = Vec::new();
        let bytes_read = reader
            .read_until(b'\n', &mut buffer)
            .map_err(|error| error.to_string())?;

        if bytes_read == 0 {
            break;
        }

        if buffer.ends_with(b"\n") {
            buffer.pop();
            if buffer.ends_with(b"\r") {
                buffer.pop();
            }
        }

        on_line(String::from_utf8_lossy(&buffer).into_owned())?;
    }

    Ok(())
}

fn append_input_source(path: &Path, result: &mut Vec<String>) -> Result<(), String> {
    if path.is_dir() {
        collect_epubs_recursive(path, result)?;
        return Ok(());
    }

    let is_epub = path
        .extension()
        .and_then(|value| value.to_str())
        .map(|value| value.eq_ignore_ascii_case("epub"))
        .unwrap_or(false);
    if is_epub && path.is_file() {
        result.push(path.to_string_lossy().to_string());
    }

    Ok(())
}

fn parse_json_line(line: &str) -> Value {
    serde_json::from_str::<Value>(line).unwrap_or_else(|_| {
        json!({
            "event": "task.log",
            "task_id": "rust-fallback",
            "status": "running",
            "progress": 0,
            "message": line,
            "level": "info"
        })
    })
}

#[tauri::command]
async fn list_font_targets(app: AppHandle, file_path: String) -> Result<FontTargetResponse, String> {
    let output = build_backend_command(&app, "list-fonts")?
        .arg(file_path)
        .output()
        .map_err(|error| format!("调用 Python 后端失败: {error}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        return Err(format!("列出字体失败: {stderr}"));
    }

    serde_json::from_slice::<FontTargetResponse>(&output.stdout)
        .map_err(|error| format!("解析字体结果失败: {error}"))
}

#[tauri::command]
async fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    let status = if is_external_target(&path) {
        if cfg!(target_os = "macos") {
            Command::new("open").arg(&path).status()
        } else if cfg!(target_os = "windows") {
            Command::new("cmd").args(["/C", "start", "", &path]).status()
        } else {
            Command::new("xdg-open").arg(&path).status()
        }
    } else {
        let resolved = resolve_path(&app, &path)?;
        if cfg!(target_os = "macos") {
            Command::new("open").arg(&resolved).status()
        } else if cfg!(target_os = "windows") {
            Command::new("cmd")
                .args(["/C", "start", "", resolved.to_string_lossy().as_ref()])
                .status()
        } else {
            Command::new("xdg-open").arg(&resolved).status()
        }
    }
    .map_err(|error| format!("打开路径失败: {error}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!("系统命令返回失败状态: {status}"))
    }
}

#[tauri::command]
async fn get_log_path(app: AppHandle) -> Result<String, String> {
    Ok(resolve_log_path(&app)?.to_string_lossy().to_string())
}

#[tauri::command]
async fn collect_epub_files(app: AppHandle, directory_path: String) -> Result<Vec<String>, String> {
    let resolved = resolve_path(&app, &directory_path)?;
    if !resolved.is_dir() {
        return Err(format!("不是有效目录: {}", resolved.display()));
    }

    let mut files = Vec::new();
    collect_epubs_recursive(&resolved, &mut files)?;
    files.sort();
    Ok(files)
}

#[tauri::command]
async fn resolve_input_sources(app: AppHandle, input_paths: Vec<String>) -> Result<Vec<String>, String> {
    let mut files = Vec::new();

    for input_path in input_paths {
        let resolved = resolve_path(&app, &input_path)?;
        append_input_source(&resolved, &mut files)?;
    }

    files.sort();
    files.dedup();
    Ok(files)
}

#[tauri::command]
async fn run_epub_task(
    app: AppHandle,
    request: FrontendTaskRequest,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || -> Result<Value, String> {
        let request_json =
            serde_json::to_string(&request).map_err(|error| format!("序列化请求失败: {error}"))?;
        let mut child = build_backend_command(&app, "run")?
            .args(["--request-json".to_string(), request_json])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("启动 Python 后端任务失败: {error}"))?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "无法读取 Python stdout".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "无法读取 Python stderr".to_string())?;

        let mut final_result = json!({
            "ok": false,
            "status": "error",
            "outputs": [],
            "errors": [],
            "skipped": [],
            "summary": { "total": 0, "success": 0, "failed": 0, "skipped": 0 }
        });

        stream_utf8ish_lines(stdout, |line| {
            let payload = parse_json_line(&line);
            if payload.get("event") == Some(&Value::String("task.finished".into())) {
                if let Some(result) = payload.get("result") {
                    final_result = result.clone();
                }
            }
            on_event
                .send(payload)
                .map_err(|error| format!("推送任务事件失败: {error}"))
        })
        .map_err(|error| format!("读取 Python stdout 失败: {error}"))?;

        stream_utf8ish_lines(stderr, |line| {
            on_event
                .send(json!({
                    "event": "task.stderr",
                    "task_id": request.task_id,
                    "status": "error",
                    "progress": 0,
                    "message": line,
                    "level": "error"
                }))
                .map_err(|error| format!("推送 stderr 失败: {error}"))
        })
        .map_err(|error| format!("读取 Python stderr 失败: {error}"))?;

        let status = child
            .wait()
            .map_err(|error| format!("等待 Python 任务结束失败: {error}"))?;

        if status.success() {
            Ok(final_result)
        } else {
            Err(format!("Python 任务失败，退出码: {status}"))
        }
    })
    .await
    .map_err(|error| format!("异步任务失败: {error}"))?
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            collect_epub_files,
            get_log_path,
            list_font_targets,
            open_path,
            resolve_input_sources,
            run_epub_task
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
