use crate::{
    engine_adapter,
    engine_protocol::v1::{
        engine_event, engine_request, engine_response, EngineEvent, EngineRequest, EngineResponse,
        FontScanProgress, FontScanResult, ProtocolVersion,
    },
    rust_backend,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{ipc::Channel, AppHandle, Manager, State};

#[cfg(mobile)]
use std::str::FromStr;

#[cfg(mobile)]
use tauri::path::BaseDirectory;

#[cfg(mobile)]
use tauri_plugin_fs::{FilePath, FsExt, OpenOptions};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(all(unix, not(mobile)))]
use std::os::unix::process::CommandExt;

#[cfg(target_os = "macos")]
use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};

#[cfg(target_os = "windows")]
use window_vibrancy::{apply_blur, apply_mica};

const COVER_PREVIEW_MAX_BYTES: u64 = 20 * 1024 * 1024;
const RUST_TASK_RUNNER_NAME: &str = if cfg!(target_os = "windows") {
    "rust-task-runner.exe"
} else {
    "rust-task-runner"
};
const WORKER_STDERR_MAX_LINES: usize = 100;

#[cfg(mobile)]
const MOBILE_OPENCC_RESOURCE_FILES: [&str; 7] = [
    "NOTICE.txt",
    "STCharacters.txt",
    "STPhrases.txt",
    "TSCharacters.txt",
    "TSPhrases.txt",
    "s2t.json",
    "t2s.json",
];

#[cfg(mobile)]
const MOBILE_OCR_RESOURCE_FILES: [&str; 2] = ["inference.onnx", "inference.yml"];

#[cfg(mobile)]
const MOBILE_RESOURCE_VERSION_FILE: &str = ".epub-tool-resource-version";

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ImagePreviewResponse {
    bytes: Vec<u8>,
    mime_type: String,
}

fn detect_preview_image_mime(bytes: &[u8]) -> Option<&'static str> {
    if bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return Some("image/jpeg");
    }
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        return Some("image/png");
    }
    if bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        return Some("image/webp");
    }
    None
}

struct PersistedStore {
    path: Option<PathBuf>,
    data: Mutex<BTreeMap<String, Value>>,
}

#[cfg(mobile)]
struct OpenedUrls(Mutex<Vec<String>>);

struct RustWorker {
    child: Arc<Mutex<Child>>,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    stderr_lines: Arc<Mutex<Vec<String>>>,
}

struct RustBackendState {
    worker: Mutex<Option<RustWorker>>,
    active_child: Mutex<Option<Arc<Mutex<Child>>>>,
    manual_restart_requested: AtomicBool,
    recovery_epoch: AtomicU64,
    status: Mutex<EngineStatus>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct EngineStatus {
    state: String,
    message: String,
    last_error: Option<String>,
    pid: Option<u32>,
    recovery_attempts: u8,
    auto_restart_limit: u8,
    manual_restart_count: u32,
}

impl Default for EngineStatus {
    fn default() -> Self {
        Self {
            state: "stopped".to_string(),
            message: "Rust 处理引擎尚未启动".to_string(),
            last_error: None,
            pid: None,
            recovery_attempts: 0,
            auto_restart_limit: 2,
            manual_restart_count: 0,
        }
    }
}

fn initial_engine_status() -> EngineStatus {
    #[cfg(mobile)]
    {
        let mut status = EngineStatus::default();
        status.state = "ready".to_string();
        status.message = "移动端 Rust 处理引擎已就绪".to_string();
        status
    }
    #[cfg(not(mobile))]
    {
        EngineStatus::default()
    }
}

fn ready_rust_backend_status(
    auto_restart_limit: u8,
    manual_restart_count: u32,
    pid: u32,
) -> EngineStatus {
    EngineStatus {
        state: "ready".to_string(),
        message: "Rust Worker 已就绪".to_string(),
        last_error: None,
        pid: Some(pid),
        recovery_attempts: 0,
        auto_restart_limit,
        manual_restart_count,
    }
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

fn resolve_config_store_path(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(root) = workspace_root() {
        return Ok(root.join("app-state.json"));
    }

    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|error| format!("无法定位配置目录: {error}"))?;
    fs::create_dir_all(&config_dir)
        .map_err(|error| format!("创建配置目录失败 {}: {error}", config_dir.display()))?;
    Ok(config_dir.join("app-state.json"))
}

fn corrupt_store_backup_path(path: &Path) -> PathBuf {
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("app-state.json");
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0);
    path.with_file_name(format!("{file_name}.corrupt-{timestamp}"))
}

fn cleanup_corrupt_store_backups(path: &Path, retain: usize) -> Result<(), String> {
    let parent = match path.parent() {
        Some(parent) => parent,
        None => return Ok(()),
    };
    let file_name = match path.file_name().and_then(|value| value.to_str()) {
        Some(file_name) => file_name,
        None => return Ok(()),
    };
    let backup_prefix = format!("{file_name}.corrupt-");

    let mut backups = Vec::new();
    for entry in fs::read_dir(parent)
        .map_err(|error| format!("读取配置目录失败 {}: {error}", parent.display()))?
    {
        let entry = entry.map_err(|error| format!("读取配置目录项失败: {error}"))?;
        let entry_path = entry.path();
        let entry_name = match entry_path.file_name().and_then(|value| value.to_str()) {
            Some(entry_name) => entry_name,
            None => continue,
        };
        if !entry_name.starts_with(&backup_prefix) {
            continue;
        }

        let modified_at = entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .unwrap_or(UNIX_EPOCH);
        backups.push((modified_at, entry_path));
    }

    backups.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    for (_, backup_path) in backups.into_iter().skip(retain) {
        fs::remove_file(&backup_path).map_err(|error| {
            format!("清理旧损坏配置备份失败 {}: {error}", backup_path.display())
        })?;
    }

    Ok(())
}

fn read_config_store(path: &Path) -> Result<BTreeMap<String, Value>, String> {
    if !path.is_file() {
        return Ok(BTreeMap::new());
    }

    let raw = fs::read_to_string(path)
        .map_err(|error| format!("读取配置文件失败 {}: {error}", path.display()))?;
    if raw.trim().is_empty() {
        return Ok(BTreeMap::new());
    }

    serde_json::from_str::<BTreeMap<String, Value>>(&raw).or_else(|error| {
        let backup_path = corrupt_store_backup_path(path);
        fs::rename(path, &backup_path).map_err(|rename_error| {
            format!(
                "解析配置文件失败 {}: {error}；备份损坏文件到 {} 失败: {rename_error}",
                path.display(),
                backup_path.display()
            )
        })?;
        cleanup_corrupt_store_backups(path, 3)?;
        eprintln!(
            "检测到损坏的 app-state.json，已备份到 {} 并重置为默认状态。",
            backup_path.display()
        );
        Ok(BTreeMap::new())
    })
}

fn write_config_store(path: &Path, store: &BTreeMap<String, Value>) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("配置文件路径无父目录: {}", path.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("创建配置父目录失败 {}: {error}", parent.display()))?;

    let content =
        serde_json::to_vec_pretty(store).map_err(|error| format!("序列化配置文件失败: {error}"))?;
    fs::write(path, content)
        .map_err(|error| format!("写入配置文件失败 {}: {error}", path.display()))
}

impl PersistedStore {
    fn load(app: &AppHandle) -> Self {
        match resolve_config_store_path(app) {
            Ok(path) => match read_config_store(&path) {
                Ok(data) => Self {
                    path: Some(path),
                    data: Mutex::new(data),
                },
                Err(error) => {
                    eprintln!("加载 app-state.json 失败，将以默认状态继续启动：{error}");
                    Self {
                        path: Some(path),
                        data: Mutex::new(BTreeMap::new()),
                    }
                }
            },
            Err(error) => {
                eprintln!("无法定位 app-state.json 存储路径，将以仅内存状态继续启动：{error}");
                Self {
                    path: None,
                    data: Mutex::new(BTreeMap::new()),
                }
            }
        }
    }

    fn load_value(&self, key: &str) -> Result<Option<Value>, String> {
        let store = self
            .data
            .lock()
            .map_err(|_| "配置存储锁已损坏，无法读取状态。".to_string())?;
        Ok(store.get(key).cloned())
    }

    fn save_value(&self, key: String, value: Value) -> Result<(), String> {
        let mut store = self
            .data
            .lock()
            .map_err(|_| "配置存储锁已损坏，无法写入状态。".to_string())?;
        store.insert(key, value);
        let path = self
            .path
            .as_ref()
            .ok_or_else(|| "当前运行环境未提供配置存储路径，无法持久化状态。".to_string())?;
        write_config_store(path, &store)
    }
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

#[cfg(mobile)]
fn mobile_resource_root(app: &AppHandle) -> Result<PathBuf, String> {
    let root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位移动端应用数据目录: {error}"))?
        .join("runtime-resources");
    fs::create_dir_all(&root)
        .map_err(|error| format!("创建移动端资源目录失败 {}: {error}", root.display()))?;
    Ok(root)
}

#[cfg(mobile)]
fn copy_mobile_resource(
    app: &AppHandle,
    relative_path: &str,
    destination: &Path,
) -> Result<(), String> {
    let source = app
        .path()
        .resolve(relative_path, BaseDirectory::Resource)
        .map_err(|error| format!("定位内置资源 {relative_path} 失败: {error}"))?;
    let bytes = app
        .fs()
        .read(source)
        .map_err(|error| format!("读取内置资源 {relative_path} 失败: {error}"))?;
    let parent = destination
        .parent()
        .ok_or_else(|| format!("资源目标路径无父目录: {}", destination.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("创建资源目标目录 {} 失败: {error}", parent.display()))?;
    fs::write(destination, bytes)
        .map_err(|error| format!("写入内置资源 {} 失败: {error}", destination.display()))
}

#[cfg(mobile)]
fn initialize_mobile_runtime_resources(app: &AppHandle) -> Result<(), String> {
    let resource_root = mobile_resource_root(app)?;
    let opencc_dir = resource_root.join("opencc");
    let ocr_dir = resource_root
        .join("ocr-models")
        .join("PP-OCRv6_small_rec_onnx");
    let version_path = resource_root.join(MOBILE_RESOURCE_VERSION_FILE);
    let current_version = env!("CARGO_PKG_VERSION");
    let resources_are_current = fs::read_to_string(&version_path)
        .map(|value| value.trim() == current_version)
        .unwrap_or(false)
        && MOBILE_OPENCC_RESOURCE_FILES
            .iter()
            .all(|name| opencc_dir.join(name).is_file())
        && MOBILE_OCR_RESOURCE_FILES
            .iter()
            .all(|name| ocr_dir.join(name).is_file());

    if !resources_are_current {
        for name in MOBILE_OPENCC_RESOURCE_FILES {
            copy_mobile_resource(app, &format!("opencc/{name}"), &opencc_dir.join(name))?;
        }
        for name in MOBILE_OCR_RESOURCE_FILES {
            copy_mobile_resource(
                app,
                &format!("ocr-models/PP-OCRv6_small_rec_onnx/{name}"),
                &ocr_dir.join(name),
            )?;
        }
        fs::write(&version_path, current_version).map_err(|error| {
            format!(
                "写入移动端资源版本标记失败 {}: {error}",
                version_path.display()
            )
        })?;
    }

    rust_backend::text::configure_resource_dir(opencc_dir)?;
    rust_backend::font::decrypt_font::configure_ocr_resources(
        rust_backend::font::decrypt_font::OcrResourcePaths { model_dir: ocr_dir },
    )?;
    Ok(())
}

#[cfg(not(mobile))]
fn initialize_mobile_runtime_resources(_app: &AppHandle) -> Result<(), String> {
    Ok(())
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

fn resolve_ocr_model_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(explicit_path) = std::env::var("EPUB_TOOL_OCR_ONNX_MODEL_DIR") {
        if !explicit_path.is_empty() {
            return Some(PathBuf::from(explicit_path));
        }
    }
    let model_dir_name = std::env::var("EPUB_TOOL_OCR_MODEL_NAME")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "PP-OCRv6_small_rec".to_string())
        + "_onnx";
    if let Some(root) = workspace_root() {
        let model_dir = root
            .join("src-tauri")
            .join("bundle-resources")
            .join("ocr-models")
            .join(&model_dir_name);
        if model_dir.is_dir() {
            return Some(model_dir);
        }
    }
    app.path()
        .resource_dir()
        .ok()
        .map(|resource_dir| resource_dir.join("ocr-models").join(model_dir_name))
        .filter(|model_dir| model_dir.is_dir())
}

fn configure_system_open_command(_command: &mut Command) {
    #[cfg(target_os = "windows")]
    _command.creation_flags(CREATE_NO_WINDOW);
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct RustWorkerRequest<'a> {
    request_id: &'a str,
    request: &'a crate::FrontendTaskRequest,
    log_path: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RustWorkerEnvelope {
    kind: String,
    request_id: String,
    event: Option<Value>,
    result: Option<Value>,
    error: Option<String>,
}

fn rust_runner_path() -> Result<PathBuf, String> {
    if let Ok(path) = std::env::var("EPUB_TOOL_RUST_TASK_RUNNER") {
        if !path.is_empty() {
            return Ok(PathBuf::from(path));
        }
    }
    if let Some(root) = workspace_root() {
        let path = root
            .join("src-tauri")
            .join("target")
            .join("debug")
            .join(RUST_TASK_RUNNER_NAME);
        if path.is_file() {
            return Ok(path);
        }
    }
    let executable =
        std::env::current_exe().map_err(|error| format!("无法定位桌面应用可执行文件: {error}"))?;
    let path = executable
        .parent()
        .ok_or_else(|| format!("桌面应用可执行文件没有父目录: {}", executable.display()))?
        .join(RUST_TASK_RUNNER_NAME);
    if path.is_file() {
        return Ok(path);
    }
    Err(format!(
        "未找到 Rust Worker 可执行文件。开发态请先构建 {RUST_TASK_RUNNER_NAME}，打包态请确认它已随应用打包。"
    ))
}

fn build_rust_worker_command(app: &AppHandle) -> Result<Command, String> {
    let mut command = Command::new(rust_runner_path()?);
    command
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(resource_dir) = resolve_opencc_resource_dir(app) {
        command.env("EPUB_TOOL_OPENCC_RESOURCE_DIR", resource_dir);
    }
    if let Some(model_dir) = resolve_ocr_model_dir(app) {
        command.env("EPUB_TOOL_OCR_ONNX_MODEL_DIR", model_dir);
    }
    #[cfg(all(unix, not(mobile)))]
    command.process_group(0);
    configure_system_open_command(&mut command);
    Ok(command)
}

fn start_rust_worker(app: &AppHandle) -> Result<RustWorker, String> {
    let mut child = build_rust_worker_command(app)?
        .spawn()
        .map_err(|error| format!("启动 Rust Worker 失败: {error}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "无法读取 Rust Worker stdin".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取 Rust Worker stdout".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "无法读取 Rust Worker stderr".to_string())?;
    let stderr_lines = Arc::new(Mutex::new(Vec::new()));
    let stderr_lines_for_thread = Arc::clone(&stderr_lines);
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if let Ok(mut lines) = stderr_lines_for_thread.lock() {
                lines.push(line);
                if lines.len() > WORKER_STDERR_MAX_LINES {
                    let overflow = lines.len() - WORKER_STDERR_MAX_LINES;
                    lines.drain(..overflow);
                }
            }
        }
    });
    Ok(RustWorker {
        child: Arc::new(Mutex::new(child)),
        stdin,
        stdout: BufReader::new(stdout),
        stderr_lines,
    })
}

fn worker_pid(worker: &RustWorker) -> Option<u32> {
    worker.child.lock().ok().map(|child| child.id())
}

fn worker_stderr_tail(worker: &RustWorker) -> String {
    worker
        .stderr_lines
        .lock()
        .ok()
        .filter(|lines| !lines.is_empty())
        .map(|lines| format!(" Worker stderr: {}", lines.join(" | ")))
        .unwrap_or_default()
}

fn terminate_worker_process_tree(child: &mut Child) -> Result<(), String> {
    #[cfg(unix)]
    {
        let process_group = -(child.id() as i32);
        let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
        if result == 0 {
            return Ok(());
        }
    }
    child
        .kill()
        .map_err(|error| format!("终止 Rust Worker 失败: {error}"))
}

fn stop_rust_worker(worker: &mut RustWorker) -> Result<(), String> {
    let mut child = worker
        .child
        .lock()
        .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?;
    if child
        .try_wait()
        .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?
        .is_none()
    {
        terminate_worker_process_tree(&mut child)?;
    }
    Ok(())
}

fn set_active_worker_child(store: &RustBackendState, child: Option<Arc<Mutex<Child>>>) {
    if let Ok(mut active_child) = store.active_child.lock() {
        *active_child = child;
    }
}

fn terminate_active_worker(store: &RustBackendState) -> Result<(), String> {
    let active_child = store
        .active_child
        .lock()
        .map_err(|_| "活动 Rust Worker 锁已损坏".to_string())?
        .take();
    if let Some(child) = active_child {
        let mut child = child
            .lock()
            .map_err(|_| "活动 Rust Worker 子进程锁已损坏".to_string())?;
        if child
            .try_wait()
            .map_err(|error| format!("检查活动 Rust Worker 状态失败: {error}"))?
            .is_none()
        {
            terminate_worker_process_tree(&mut child)?;
        }
    }
    Ok(())
}

fn set_worker_status(store: &RustBackendState, status: EngineStatus) {
    if let Ok(mut current) = store.status.lock() {
        *current = status;
    }
}

fn ensure_rust_worker(
    app: &AppHandle,
    store: &RustBackendState,
    worker_slot: &mut Option<RustWorker>,
) -> Result<(), String> {
    if let Some(worker) = worker_slot.as_mut() {
        if worker
            .child
            .lock()
            .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?
            .try_wait()
            .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?
            .is_none()
        {
            return Ok(());
        }
    }
    let worker = start_rust_worker(app)?;
    let pid = worker_pid(&worker).ok_or_else(|| "无法获取 Rust Worker PID".to_string())?;
    let status = store
        .status
        .lock()
        .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
    let auto_restart_limit = status.auto_restart_limit;
    let manual_restart_count = status.manual_restart_count;
    drop(status);
    *worker_slot = Some(worker);
    set_worker_status(
        store,
        ready_rust_backend_status(auto_restart_limit, manual_restart_count, pid),
    );
    Ok(())
}

fn recover_rust_worker(app: &AppHandle, store: &RustBackendState, error: &str) {
    let recovery_epoch = store.recovery_epoch.fetch_add(1, Ordering::AcqRel) + 1;
    let auto_restart_limit = match store.status.lock() {
        Ok(status) => status.auto_restart_limit,
        Err(_) => return,
    };
    let recovery_attempt = match store.status.lock() {
        Ok(status) => status.recovery_attempts.saturating_add(1),
        Err(_) => return,
    };
    if recovery_attempt > auto_restart_limit {
        if let Ok(mut worker_slot) = store.worker.lock() {
            if let Some(worker) = worker_slot.as_mut() {
                let _ = stop_rust_worker(worker);
            }
            *worker_slot = None;
        }
        set_worker_status(
            store,
            EngineStatus {
                state: "unavailable".to_string(),
                message: "Rust Worker 自动恢复次数已耗尽".to_string(),
                last_error: Some(error.to_string()),
                pid: None,
                recovery_attempts: auto_restart_limit,
                auto_restart_limit,
                manual_restart_count: store
                    .status
                    .lock()
                    .map(|status| status.manual_restart_count)
                    .unwrap_or(0),
            },
        );
        return;
    }
    let mut worker_slot = match store.worker.lock() {
        Ok(worker_slot) => worker_slot,
        Err(_) => return,
    };
    if let Some(worker) = worker_slot.as_mut() {
        let _ = stop_rust_worker(worker);
    }
    *worker_slot = None;
    if store.recovery_epoch.load(Ordering::Acquire) != recovery_epoch {
        return;
    }
    match ensure_rust_worker(app, store, &mut worker_slot) {
        Ok(()) => {
            if let Ok(mut status) = store.status.lock() {
                status.message = "Rust Worker 已自动恢复".to_string();
                status.recovery_attempts = recovery_attempt;
            }
        }
        Err(restart_error) => set_worker_status(
            store,
            EngineStatus {
                state: "unavailable".to_string(),
                message: "Rust Worker 自动恢复失败".to_string(),
                last_error: Some(format!("{error}; {restart_error}")),
                pid: None,
                recovery_attempts: recovery_attempt,
                auto_restart_limit,
                manual_restart_count: store
                    .status
                    .lock()
                    .map(|status| status.manual_restart_count)
                    .unwrap_or(0),
            },
        ),
    }
}

fn shutdown_rust_worker(store: &RustBackendState) {
    if let Ok(mut worker_slot) = store.worker.lock() {
        if let Some(worker) = worker_slot.as_mut() {
            let _ = stop_rust_worker(worker);
        }
        *worker_slot = None;
    }
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

#[tauri::command]
#[cfg(not(mobile))]
async fn get_python_worker_status(app: AppHandle) -> Result<EngineStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let store = app.state::<RustBackendState>();
        let mut worker_slot = store
            .worker
            .lock()
            .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
        let worker_exited = match worker_slot.as_mut() {
            Some(worker) => worker
                .child
                .lock()
                .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?
                .try_wait()
                .map(|status| status.is_some())
                .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?,
            None => false,
        };
        let worker_missing = worker_slot.is_none();
        let recovery_exhausted = store
            .status
            .lock()
            .map(|status| {
                status.state == "unavailable"
                    && status.recovery_attempts >= status.auto_restart_limit
            })
            .unwrap_or(false);
        if worker_exited {
            drop(worker_slot);
            if !recovery_exhausted {
                recover_rust_worker(&app, store.inner(), "Rust Worker 在空闲时意外退出");
            }
        } else if worker_missing && !recovery_exhausted {
            *worker_slot = None;
            if let Err(error) = ensure_rust_worker(&app, store.inner(), &mut worker_slot) {
                let auto_restart_limit = store
                    .status
                    .lock()
                    .map(|status| status.auto_restart_limit)
                    .unwrap_or(2);
                set_worker_status(
                    store.inner(),
                    EngineStatus {
                        state: "unavailable".to_string(),
                        message: "启动 Rust Worker 失败".to_string(),
                        last_error: Some(error),
                        pid: None,
                        recovery_attempts: 0,
                        auto_restart_limit,
                        manual_restart_count: 0,
                    },
                );
            }
            drop(worker_slot);
        } else {
            drop(worker_slot);
        }
        store
            .status
            .lock()
            .map(|status| status.clone())
            .map_err(|_| "Rust Worker 状态锁已损坏".to_string())
    })
    .await
    .map_err(|error| format!("读取 Rust Worker 状态失败: {error}"))?
}

#[tauri::command]
#[cfg(mobile)]
async fn get_python_worker_status(app: AppHandle) -> Result<EngineStatus, String> {
    app.state::<RustBackendState>()
        .status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "移动端 Rust 引擎状态锁已损坏".to_string())
}

#[tauri::command]
fn set_python_worker_auto_restart_limit(
    state: State<'_, RustBackendState>,
    limit: u8,
) -> Result<EngineStatus, String> {
    let mut status = state
        .status
        .lock()
        .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
    status.auto_restart_limit = limit.min(5);
    Ok(status.clone())
}

#[tauri::command]
#[cfg(not(mobile))]
async fn restart_python_worker(app: AppHandle) -> Result<EngineStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let store = app.state::<RustBackendState>();
        store
            .manual_restart_requested
            .store(true, Ordering::Release);
        store.recovery_epoch.fetch_add(1, Ordering::AcqRel);
        let result = (|| {
            terminate_active_worker(store.inner())?;
            let mut worker_slot = store
                .worker
                .lock()
                .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
            if let Some(worker) = worker_slot.as_mut() {
                stop_rust_worker(worker)?;
            }
            *worker_slot = None;
            ensure_rust_worker(&app, store.inner(), &mut worker_slot)?;
            if let Ok(mut status) = store.status.lock() {
                status.message = "Rust Worker 已手动重启".to_string();
                status.manual_restart_count = status.manual_restart_count.saturating_add(1);
            }
            store
                .status
                .lock()
                .map(|status| status.clone())
                .map_err(|_| "Rust Worker 状态锁已损坏".to_string())
        })();
        store
            .manual_restart_requested
            .store(false, Ordering::Release);
        result
    })
    .await
    .map_err(|error| format!("重启 Rust Worker 失败: {error}"))?
}

#[tauri::command]
#[cfg(mobile)]
async fn restart_python_worker(app: AppHandle) -> Result<EngineStatus, String> {
    let store = app.state::<RustBackendState>();
    let mut status = store
        .status
        .lock()
        .map_err(|_| "移动端 Rust 引擎状态锁已损坏".to_string())?;
    status.state = "ready".to_string();
    status.message = "移动端 Rust 处理引擎运行于应用进程，无需重启 Worker".to_string();
    status.last_error = None;
    status.manual_restart_count = status.manual_restart_count.saturating_add(1);
    Ok(status.clone())
}

#[tauri::command]
async fn list_font_targets_batch(
    request: EngineRequest,
    on_event: Channel<EngineEvent>,
) -> Result<EngineResponse, String> {
    validate_engine_request(&request)?;
    let request_id = request.request_id.clone();
    let Some(engine_request::Operation::ScanFonts(scan_request)) = request.operation else {
        return Err("字体扫描命令只接受 scanFonts operation".to_string());
    };

    tauri::async_runtime::spawn_blocking(move || -> Result<EngineResponse, String> {
        let total_files = u32::try_from(scan_request.input_files.len())
            .map_err(|_| "字体扫描文件数超出 Protobuf uint32 范围".to_string())?;
        let mut results = Vec::with_capacity(scan_request.input_files.len());
        for (position, input_file) in scan_request.input_files.into_iter().enumerate() {
            let result = engine_adapter::font_target_result(
                input_file.clone(),
                rust_backend::font::font_targets::list_font_targets(Path::new(&input_file)),
            );
            on_event
                .send(EngineEvent {
                    protocol_version: ProtocolVersion::V1 as i32,
                    request_id: request_id.clone(),
                    payload: Some(engine_event::Payload::FontScanProgress(FontScanProgress {
                        current_index: u32::try_from(position + 1)
                            .map_err(|_| "字体扫描索引超出 Protobuf uint32 范围".to_string())?,
                        total_files,
                        result: Some(result.clone()),
                    })),
                })
                .map_err(|error| format!("推送 Rust 字体扫描事件失败: {error}"))?;
            results.push(result);
        }
        Ok(EngineResponse {
            protocol_version: ProtocolVersion::V1 as i32,
            request_id,
            payload: Some(engine_response::Payload::FontScanResult(FontScanResult {
                results,
            })),
        })
    })
    .await
    .map_err(|error| format!("异步字体扫描失败: {error}"))?
}

#[tauri::command]
#[cfg(not(mobile))]
async fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    let mut command = if is_external_target(&path) {
        if cfg!(target_os = "macos") {
            let mut command = Command::new("open");
            command.arg(&path);
            command
        } else if cfg!(target_os = "windows") {
            let mut command = Command::new("cmd");
            command.args(["/C", "start", "", &path]);
            command
        } else {
            let mut command = Command::new("xdg-open");
            command.arg(&path);
            command
        }
    } else {
        let resolved = resolve_path(&app, &path)?;
        if cfg!(target_os = "macos") {
            let mut command = Command::new("open");
            command.arg(&resolved);
            command
        } else if cfg!(target_os = "windows") {
            let mut command = Command::new("cmd");
            command.args(["/C", "start", "", resolved.to_string_lossy().as_ref()]);
            command
        } else {
            let mut command = Command::new("xdg-open");
            command.arg(&resolved);
            command
        }
    };

    configure_system_open_command(&mut command);
    let status = command
        .status()
        .map_err(|error| format!("打开路径失败: {error}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!("系统命令返回失败状态: {status}"))
    }
}

#[tauri::command]
#[cfg(mobile)]
async fn open_path(_app: AppHandle, _path: String) -> Result<(), String> {
    Err("移动端不支持在应用内直接打开本地路径，请使用导出功能保存处理结果。".to_string())
}

#[tauri::command]
async fn get_log_path(app: AppHandle) -> Result<String, String> {
    Ok(resolve_log_path(&app)?.to_string_lossy().to_string())
}

#[tauri::command]
async fn get_persisted_store_path(store: State<'_, PersistedStore>) -> Result<String, String> {
    store
        .path
        .as_ref()
        .map(|path| path.to_string_lossy().to_string())
        .ok_or_else(|| "当前运行环境未提供配置存储路径。".to_string())
}

#[cfg(mobile)]
#[tauri::command]
async fn opened_urls(state: State<'_, OpenedUrls>) -> Result<Vec<String>, String> {
    state
        .0
        .lock()
        .map(|urls| urls.clone())
        .map_err(|_| "移动端已打开文件列表锁已损坏".to_string())
}

#[cfg(not(mobile))]
#[tauri::command]
async fn opened_urls() -> Result<Vec<String>, String> {
    Ok(Vec::new())
}

#[tauri::command]
async fn read_image_preview(path: String) -> Result<ImagePreviewResponse, String> {
    let image_path = PathBuf::from(path);
    let metadata =
        fs::metadata(&image_path).map_err(|error| format!("读取封面文件信息失败: {error}"))?;
    if !metadata.is_file() {
        return Err("选择的封面路径不是文件。".to_string());
    }
    if metadata.len() > COVER_PREVIEW_MAX_BYTES {
        return Err("封面文件超过 20 MB，无法生成预览。".to_string());
    }
    let bytes = fs::read(&image_path).map_err(|error| format!("读取封面文件失败: {error}"))?;
    let mime_type = detect_preview_image_mime(&bytes)
        .ok_or_else(|| "封面预览仅支持有效的 JPG、PNG 或 WebP 图片。".to_string())?;
    Ok(ImagePreviewResponse {
        bytes,
        mime_type: mime_type.to_string(),
    })
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PersistedStateResponse {
    found: bool,
    value: Value,
}

#[tauri::command]
async fn load_persisted_state(
    store: State<'_, PersistedStore>,
    key: String,
) -> Result<PersistedStateResponse, String> {
    if let Some(value) = store.load_value(&key)? {
        return Ok(PersistedStateResponse { found: true, value });
    }

    Ok(PersistedStateResponse {
        found: false,
        value: Value::Null,
    })
}

#[tauri::command]
async fn save_persisted_state(
    store: State<'_, PersistedStore>,
    key: String,
    value: Value,
) -> Result<(), String> {
    store.save_value(key, value)
}

#[tauri::command]
#[cfg(not(mobile))]
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
#[cfg(mobile)]
async fn collect_epub_files(
    _app: AppHandle,
    _directory_path: String,
) -> Result<Vec<String>, String> {
    Err("Android 和 iOS 不支持目录扫描，请直接选择 EPUB 文件。".to_string())
}

#[tauri::command]
#[cfg(not(mobile))]
async fn validate_output_directory(app: AppHandle, directory_path: String) -> Result<(), String> {
    let resolved = resolve_path(&app, &directory_path)?;
    if !resolved.is_dir() {
        return Err(format!("不是有效目录: {}", resolved.display()));
    }
    Ok(())
}

#[tauri::command]
#[cfg(mobile)]
async fn validate_output_directory(_app: AppHandle, _directory_path: String) -> Result<(), String> {
    Err("移动端不支持自定义输出目录，请在任务完成后导出结果。".to_string())
}

#[cfg(not(mobile))]
#[tauri::command]
async fn resolve_input_sources(
    app: AppHandle,
    input_paths: Vec<String>,
) -> Result<Vec<String>, String> {
    let mut files = Vec::new();

    for input_path in input_paths {
        let resolved = resolve_path(&app, &input_path)?;
        append_input_source(&resolved, &mut files)?;
    }

    files.sort();
    files.dedup();
    Ok(files)
}

#[cfg(mobile)]
fn mobile_staging_directory(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = app
        .path()
        .app_cache_dir()
        .map_err(|error| format!("无法定位移动端临时目录: {error}"))?
        .join("epub-tool-inputs");
    fs::create_dir_all(&directory)
        .map_err(|error| format!("创建移动端临时目录失败 {}: {error}", directory.display()))?;
    Ok(directory)
}

#[cfg(mobile)]
fn stage_mobile_source(
    app: &AppHandle,
    source_path: &str,
    extension: &str,
) -> Result<String, String> {
    let source = match FilePath::from_str(source_path) {
        Ok(source) => source,
        Err(never) => match never {},
    };
    let mut source = app
        .fs()
        .open(source, OpenOptions::new().read(true))
        .map_err(|error| format!("读取所选文件失败: {error}"))?;
    let safe_extension = extension
        .trim_start_matches('.')
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .collect::<String>();
    if safe_extension.is_empty() {
        return Err("移动端暂存文件缺少有效扩展名。".to_string());
    }
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    let destination = mobile_staging_directory(app)?.join(format!("{timestamp}.{safe_extension}"));
    let mut destination_file = fs::File::create(&destination)
        .map_err(|error| format!("创建暂存文件失败 {}: {error}", destination.display()))?;
    std::io::copy(&mut source, &mut destination_file)
        .map_err(|error| format!("暂存所选文件失败 {}: {error}", destination.display()))?;
    Ok(destination.to_string_lossy().to_string())
}

#[cfg(mobile)]
#[tauri::command]
async fn resolve_input_sources(
    app: AppHandle,
    input_paths: Vec<String>,
) -> Result<Vec<String>, String> {
    input_paths
        .iter()
        .map(|path| stage_mobile_source(&app, path, "epub"))
        .collect()
}

#[cfg(not(mobile))]
#[tauri::command]
async fn stage_mobile_source_for_task(
    _app: AppHandle,
    _source_path: String,
    _extension: String,
) -> Result<String, String> {
    Err("仅 Android 和 iOS 需要暂存所选文件。".to_string())
}

#[cfg(mobile)]
#[tauri::command]
async fn stage_mobile_source_for_task(
    app: AppHandle,
    source_path: String,
    extension: String,
) -> Result<String, String> {
    stage_mobile_source(&app, &source_path, &extension)
}

#[cfg(not(mobile))]
#[tauri::command]
async fn export_mobile_output(
    _app: AppHandle,
    _source_path: String,
    _destination_path: String,
) -> Result<(), String> {
    Err("仅 Android 和 iOS 支持导出暂存结果。".to_string())
}

#[cfg(mobile)]
#[tauri::command]
async fn export_mobile_output(
    app: AppHandle,
    source_path: String,
    destination_path: String,
) -> Result<(), String> {
    let mut source = fs::File::open(&source_path)
        .map_err(|error| format!("读取处理结果失败 {source_path}: {error}"))?;
    let destination = match FilePath::from_str(&destination_path) {
        Ok(destination) => destination,
        Err(never) => match never {},
    };
    let mut output = app
        .fs()
        .open(
            destination,
            OpenOptions::new().write(true).truncate(true).create(true),
        )
        .map_err(|error| format!("创建导出文件失败: {error}"))?;
    std::io::copy(&mut source, &mut output)
        .map_err(|error| format!("导出处理结果失败: {error}"))?;
    output
        .flush()
        .map_err(|error| format!("刷新导出文件失败: {error}"))
}

#[cfg(not(mobile))]
fn execute_rust_worker_request(
    app: &AppHandle,
    store: &RustBackendState,
    request_id: &str,
    frontend_request: &crate::FrontendTaskRequest,
    on_event: &Channel<EngineEvent>,
) -> Result<Value, String> {
    let log_path = resolve_log_path(app)?.to_string_lossy().to_string();
    let worker_request = RustWorkerRequest {
        request_id,
        request: frontend_request,
        log_path,
    };
    let request_line = serde_json::to_string(&worker_request)
        .map_err(|error| format!("序列化 Rust Worker 请求失败: {error}"))?;
    let mut worker_slot = store
        .worker
        .lock()
        .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
    ensure_rust_worker(app, store, &mut worker_slot)?;
    let active_child = worker_slot
        .as_ref()
        .map(|worker| Arc::clone(&worker.child))
        .ok_or_else(|| "Rust Worker 未初始化".to_string())?;
    set_active_worker_child(store, Some(active_child));
    if let Ok(mut status) = store.status.lock() {
        status.state = "busy".to_string();
        status.message = "Rust Worker 正在执行请求".to_string();
    }
    let result = (|| -> Result<Value, String> {
        let worker = worker_slot
            .as_mut()
            .ok_or_else(|| "Rust Worker 未初始化".to_string())?;
        worker
            .stdin
            .write_all(request_line.as_bytes())
            .and_then(|_| worker.stdin.write_all(b"\n"))
            .and_then(|_| worker.stdin.flush())
            .map_err(|error| format!("发送 Rust Worker 请求失败: {error}"))?;
        loop {
            let mut line = String::new();
            let bytes_read = worker
                .stdout
                .read_line(&mut line)
                .map_err(|error| format!("读取 Rust Worker 输出失败: {error}"))?;
            if bytes_read == 0 {
                return Err(format!(
                    "Rust Worker 意外退出。{}",
                    worker_stderr_tail(worker)
                ));
            }
            let envelope: RustWorkerEnvelope = serde_json::from_str(line.trim_end())
                .map_err(|error| format!("解析 Rust Worker 响应失败: {error}"))?;
            if envelope.request_id != request_id {
                return Err(format!(
                    "Rust Worker 响应 ID 不匹配，期望 {request_id}，收到 {}",
                    envelope.request_id
                ));
            }
            match envelope.kind.as_str() {
                "event" => {
                    let event = envelope
                        .event
                        .ok_or_else(|| "Rust Worker 事件缺少内容".to_string())?;
                    let task_event = engine_adapter::task_event_from_value(event)?;
                    on_event
                        .send(EngineEvent {
                            protocol_version: ProtocolVersion::V1 as i32,
                            request_id: request_id.to_string(),
                            payload: Some(engine_event::Payload::TaskEvent(task_event)),
                        })
                        .map_err(|error| format!("推送 Rust Worker 事件失败: {error}"))?;
                }
                "result" => {
                    return envelope
                        .result
                        .ok_or_else(|| "Rust Worker 响应缺少任务结果".to_string());
                }
                "error" => {
                    return Err(envelope
                        .error
                        .unwrap_or_else(|| "Rust Worker 返回未知错误".to_string()));
                }
                _ => return Err(format!("Rust Worker 返回未知响应类型: {}", envelope.kind)),
            }
        }
    })();
    set_active_worker_child(store, None);
    match &result {
        Ok(_) => {
            if let Ok(mut status) = store.status.lock() {
                status.state = "ready".to_string();
                status.message = "Rust Worker 已就绪".to_string();
                status.last_error = None;
                status.recovery_attempts = 0;
                status.pid = worker_slot.as_ref().and_then(worker_pid);
            }
        }
        Err(error) => {
            drop(worker_slot);
            if !store.manual_restart_requested.load(Ordering::Acquire) {
                recover_rust_worker(app, store, error);
            }
            return Err(error.clone());
        }
    }
    result
}

#[cfg(mobile)]
fn execute_rust_worker_request(
    app: &AppHandle,
    store: &RustBackendState,
    request_id: &str,
    frontend_request: &crate::FrontendTaskRequest,
    on_event: &Channel<EngineEvent>,
) -> Result<Value, String> {
    if let Ok(mut status) = store.status.lock() {
        status.state = "busy".to_string();
        status.message = "Rust 处理引擎正在执行请求".to_string();
        status.last_error = None;
    }

    let log_path = resolve_log_path(app)?;
    let result = rust_backend::run(frontend_request, &log_path, &mut |event| {
        let task_event = engine_adapter::task_event_from_value(event)?;
        on_event
            .send(EngineEvent {
                protocol_version: ProtocolVersion::V1 as i32,
                request_id: request_id.to_string(),
                payload: Some(engine_event::Payload::TaskEvent(task_event)),
            })
            .map_err(|error| format!("推送移动端 Rust 引擎事件失败: {error}"))
    });

    if let Ok(mut status) = store.status.lock() {
        match &result {
            Ok(_) => {
                status.state = "ready".to_string();
                status.message = "移动端 Rust 处理引擎已就绪".to_string();
                status.last_error = None;
            }
            Err(error) => {
                status.state = "ready".to_string();
                status.message = "移动端 Rust 处理引擎已就绪".to_string();
                status.last_error = Some(error.clone());
            }
        }
    }

    result
}

#[tauri::command]
async fn run_epub_task(
    app: AppHandle,
    request: EngineRequest,
    on_event: Channel<EngineEvent>,
) -> Result<EngineResponse, String> {
    validate_engine_request(&request)?;
    let request_id = request.request_id.clone();
    let Some(engine_request::Operation::RunTask(run_request)) = request.operation else {
        return Err("任务命令只接受 runTask operation".to_string());
    };
    let frontend_request = engine_adapter::frontend_task_request(&run_request)?;
    tauri::async_runtime::spawn_blocking(move || -> Result<EngineResponse, String> {
        let store = app.state::<RustBackendState>();
        let result = execute_rust_worker_request(
            &app,
            store.inner(),
            &request_id,
            &frontend_request,
            &on_event,
        )?;
        Ok(EngineResponse {
            protocol_version: ProtocolVersion::V1 as i32,
            request_id,
            payload: Some(engine_adapter::task_result_response(
                engine_adapter::task_result_from_value(result)?,
            )),
        })
    })
    .await
    .map_err(|error| format!("异步任务失败: {error}"))?
}

fn validate_engine_request(request: &EngineRequest) -> Result<(), String> {
    if request.protocol_version != ProtocolVersion::V1 as i32 {
        return Err("请求使用了不支持的 protocolVersion".to_string());
    }
    if request.request_id.trim().is_empty() {
        return Err("请求缺少 requestId".to_string());
    }
    if request.operation.is_none() {
        return Err("请求缺少 operation".to_string());
    }
    Ok(())
}

fn resolve_opencc_resource_dir(app: &AppHandle) -> Option<PathBuf> {
    workspace_root()
        .map(|root| {
            root.join("src-tauri")
                .join("bundle-resources")
                .join("opencc")
        })
        .or_else(|| {
            app.path()
                .resource_dir()
                .ok()
                .map(|directory| directory.join("opencc"))
        })
}

#[cfg(not(mobile))]
fn setup_window_effects(app: &tauri::App) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "未找到主窗口 main".to_string())?;

    #[cfg(target_os = "macos")]
    {
        apply_vibrancy(&window, NSVisualEffectMaterial::HudWindow, None, None)
            .map_err(|error| format!("应用 macOS 毛玻璃效果失败: {error}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        window
            .set_decorations(true)
            .map_err(|error| format!("恢复 Windows 原生窗口装饰失败: {error}"))?;

        apply_mica(&window, None)
            .or_else(|_| apply_blur(&window, Some((245, 239, 231, 180))))
            .map_err(|error| format!("应用 Windows 毛玻璃效果失败: {error}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        let _ = window;
    }

    Ok(())
}

#[cfg(mobile)]
fn setup_window_effects(_app: &tauri::App) -> Result<(), String> {
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            app.manage(PersistedStore::load(app.handle()));
            initialize_mobile_runtime_resources(app.handle())?;
            #[cfg(mobile)]
            app.manage(OpenedUrls(Mutex::new(Vec::new())));
            app.manage(RustBackendState {
                worker: Mutex::new(None),
                active_child: Mutex::new(None),
                manual_restart_requested: AtomicBool::new(false),
                recovery_epoch: AtomicU64::new(0),
                status: Mutex::new(initial_engine_status()),
            });
            setup_window_effects(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            collect_epub_files,
            get_log_path,
            get_persisted_store_path,
            opened_urls,
            get_python_worker_status,
            list_font_targets_batch,
            load_persisted_state,
            open_path,
            read_image_preview,
            resolve_input_sources,
            run_epub_task,
            save_persisted_state,
            set_python_worker_auto_restart_limit,
            stage_mobile_source_for_task,
            restart_python_worker,
            validate_output_directory,
            export_mobile_output
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::Exit => {
            let store = app_handle.state::<RustBackendState>();
            shutdown_rust_worker(store.inner());
        }
        #[cfg(mobile)]
        tauri::RunEvent::Opened { urls } => {
            use tauri::Emitter;

            let opened_urls = urls
                .into_iter()
                .map(|url| url.to_string())
                .collect::<Vec<_>>();
            if let Ok(mut stored_urls) = app_handle.state::<OpenedUrls>().0.lock() {
                stored_urls.extend(opened_urls.iter().cloned());
            }
            let _ = app_handle.emit("opened", opened_urls);
        }
        _ => {}
    });
}
