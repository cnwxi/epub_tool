#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader, Read, Write},
    net::{Ipv4Addr, SocketAddrV4, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{ipc::Channel, AppHandle, Manager, State};

#[cfg(unix)]
use std::os::unix::{fs::PermissionsExt, process::CommandExt};
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "macos")]
use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};

#[cfg(target_os = "windows")]
use window_vibrancy::{apply_blur, apply_mica};

const SIDECAR_NAME: &str = if cfg!(target_os = "windows") {
    "epub-tool-python.exe"
} else {
    "epub-tool-python"
};
const SIDECAR_DIR_NAME: &str = "epub-tool-python";
const SIDECAR_RESOURCE_DIR_NAME: &str = "epub-tool-python-runtime";
const FALLBACK_OCR_MODEL_NAME: &str = "PP-OCRv6_small_rec";
const WORKER_STDERR_MAX_LINES: usize = 100;
const PARENT_LIVENESS_ACCEPT_TIMEOUT: Duration = Duration::from_secs(30);

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
    #[serde(default)]
    error: Option<String>,
}

struct PersistedStore {
    path: Option<PathBuf>,
    data: Mutex<BTreeMap<String, Value>>,
}

struct PythonWorker {
    child: Arc<Mutex<Child>>,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    stderr_lines: Arc<Mutex<Vec<String>>>,
    _parent_liveness_stream: TcpStream,
}

struct PythonWorkerStore {
    worker: Mutex<Option<PythonWorker>>,
    // The active child stays separately accessible while stdout is being read under `worker`.
    active_child: Mutex<Option<Arc<Mutex<Child>>>>,
    manual_restart_requested: AtomicBool,
    // Invalidates queued automatic recoveries whenever a newer start or restart begins.
    recovery_epoch: AtomicU64,
    status: Mutex<PythonWorkerStatus>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct PythonWorkerStatus {
    state: String,
    message: String,
    last_error: Option<String>,
    pid: Option<u32>,
    recovery_attempts: u8,
    auto_restart_limit: u8,
}

impl Default for PythonWorkerStatus {
    fn default() -> Self {
        Self {
            state: "stopped".to_string(),
            message: "处理引擎尚未启动".to_string(),
            last_error: None,
            pid: None,
            recovery_attempts: 0,
            auto_restart_limit: 2,
        }
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
            let explicit_path = PathBuf::from(explicit_path);
            candidates.push(explicit_path.clone());
            candidates.push(explicit_path.join(SIDECAR_NAME));
        }
    }

    if let Some(root) = workspace_root() {
        let binaries_dir = root.join("src-tauri").join("binaries");
        candidates.push(binaries_dir.join(SIDECAR_NAME));
        candidates.push(binaries_dir.join(SIDECAR_DIR_NAME).join(SIDECAR_NAME));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let binaries_dir = resource_dir.join("binaries");
        candidates.push(binaries_dir.join(SIDECAR_NAME));
        candidates.push(
            binaries_dir
                .join(SIDECAR_RESOURCE_DIR_NAME)
                .join(SIDECAR_NAME),
        );
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
    let ocr_model_dir = resolve_ocr_model_dir(app);

    if let Some(sidecar_path) = resolve_sidecar(app)? {
        let mut command = Command::new(sidecar_path);
        command.current_dir(&work_dir);
        command.arg(subcommand);
        configure_backend_command(&mut command, &log_path, ocr_model_dir.as_deref());
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
    configure_backend_command(&mut command, &log_path, ocr_model_dir.as_deref());
    Ok(command)
}

fn resolve_ocr_model_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(explicit_path) = std::env::var("EPUB_TOOL_OCR_ONNX_MODEL_DIR") {
        if !explicit_path.is_empty() {
            return Some(PathBuf::from(explicit_path));
        }
    }
    let onnx_model_name = std::env::var("EPUB_TOOL_OCR_MODEL_NAME")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default_ocr_model_name().to_string())
        + "_onnx";

    if let Some(root) = workspace_root() {
        let dev_model_dir = root
            .join("src-tauri")
            .join("bundle-resources")
            .join("ocr-models")
            .join(&onnx_model_name);
        if dev_model_dir.is_dir() {
            return Some(dev_model_dir);
        }
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_model_dir = resource_dir.join("ocr-models").join(&onnx_model_name);
        if bundled_model_dir.is_dir() {
            return Some(bundled_model_dir);
        }
    }

    None
}

fn default_ocr_model_name() -> &'static str {
    option_env!("EPUB_TOOL_DEFAULT_OCR_MODEL_NAME")
        .filter(|value| !value.is_empty())
        .unwrap_or(FALLBACK_OCR_MODEL_NAME)
}

fn configure_backend_command(command: &mut Command, log_path: &Path, ocr_model_dir: Option<&Path>) {
    command.env("EPUB_TOOL_LOG_PATH", log_path);
    if let Some(ocr_model_dir) = ocr_model_dir {
        command.env("EPUB_TOOL_OCR_ONNX_MODEL_DIR", ocr_model_dir);
    }
    command.env("PYTHONUTF8", "1");
    command.env("PYTHONIOENCODING", "utf-8");
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
}

fn configure_system_open_command(_command: &mut Command) {
    #[cfg(target_os = "windows")]
    _command.creation_flags(CREATE_NO_WINDOW);
}

fn create_parent_liveness_listener() -> Result<(TcpListener, String, String), String> {
    let listener = TcpListener::bind(SocketAddrV4::new(Ipv4Addr::LOCALHOST, 0))
        .map_err(|error| format!("创建 worker liveness socket 失败: {error}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|error| format!("配置 worker liveness socket 失败: {error}"))?;
    let address = listener
        .local_addr()
        .map_err(|error| format!("读取 worker liveness socket 地址失败: {error}"))?
        .to_string();
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    let token = format!("{}-{nonce}", std::process::id());
    Ok((listener, address, token))
}

fn accept_parent_liveness_stream(listener: &TcpListener, token: &str) -> Result<TcpStream, String> {
    let expected = format!("{token}\n").into_bytes();
    let deadline = Instant::now() + PARENT_LIVENESS_ACCEPT_TIMEOUT;
    loop {
        if Instant::now() >= deadline {
            return Err("等待 Python worker liveness 握手超时".to_string());
        }
        match listener.accept() {
            Ok((mut stream, _)) => {
                let mut received = Vec::with_capacity(expected.len());
                while received.len() < expected.len() {
                    let remaining = deadline.saturating_duration_since(Instant::now());
                    if remaining.is_zero() {
                        break;
                    }
                    stream
                        .set_read_timeout(Some(remaining.min(Duration::from_secs(1))))
                        .map_err(|error| format!("配置 worker liveness 握手失败: {error}"))?;
                    let mut buffer = [0_u8; 64];
                    match stream.read(&mut buffer) {
                        Ok(0) => break,
                        Ok(length) => received.extend_from_slice(&buffer[..length]),
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => continue,
                        Err(error) if error.kind() == std::io::ErrorKind::TimedOut => continue,
                        Err(error) => {
                            return Err(format!("读取 worker liveness 握手失败: {error}"));
                        }
                    }
                }
                if received == expected {
                    stream.set_read_timeout(None).map_err(|error| {
                        format!("恢复 worker liveness socket 配置失败: {error}")
                    })?;
                    return Ok(stream);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(format!("接收 worker liveness 连接失败: {error}")),
        }
    }
}

fn terminate_worker_process_tree(child: &mut Child) -> Result<(), String> {
    #[cfg(unix)]
    {
        let result = unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL) };
        if result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        Err(format!(
            "终止 Python worker 进程组失败: {}",
            std::io::Error::last_os_error()
        ))
    }

    #[cfg(target_os = "windows")]
    {
        let mut command = Command::new("taskkill");
        command.args(["/PID", &child.id().to_string(), "/T", "/F"]);
        command.creation_flags(CREATE_NO_WINDOW);
        let status = command
            .status()
            .map_err(|error| format!("终止 Python worker 进程树失败: {error}"))?;
        if status.success() || matches!(child.try_wait(), Ok(Some(_))) {
            return Ok(());
        }
        return child
            .kill()
            .map_err(|error| format!("终止 Python worker 失败: {error}"));
    }

    #[cfg(not(any(unix, target_os = "windows")))]
    child
        .kill()
        .map_err(|error| format!("终止 Python worker 失败: {error}"))
}

fn start_python_worker(app: &AppHandle) -> Result<PythonWorker, String> {
    let mut command = build_backend_command(app, "serve")?;
    let (liveness_listener, liveness_address, liveness_token) = create_parent_liveness_listener()?;
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.env("EPUB_TOOL_PARENT_LIVENESS_ADDR", liveness_address);
    command.env("EPUB_TOOL_PARENT_LIVENESS_TOKEN", liveness_token.clone());
    #[cfg(unix)]
    command.process_group(0);
    let mut child = command
        .spawn()
        .map_err(|error| format!("启动常驻 Python worker 失败: {error}"))?;
    let parent_liveness_stream =
        match accept_parent_liveness_stream(&liveness_listener, &liveness_token) {
            Ok(stream) => stream,
            Err(error) => {
                let _ = terminate_worker_process_tree(&mut child);
                return Err(error);
            }
        };
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "无法读取 Python worker stdin".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取 Python worker stdout".to_string())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "无法读取 Python worker stderr".to_string())?;
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

    Ok(PythonWorker {
        child: Arc::new(Mutex::new(child)),
        stdin,
        stdout: BufReader::new(stdout),
        stderr_lines,
        _parent_liveness_stream: parent_liveness_stream,
    })
}

fn worker_pid(worker: &PythonWorker) -> Option<u32> {
    worker.child.lock().ok().map(|child| child.id())
}

fn set_active_worker_child(store: &PythonWorkerStore, child: Option<Arc<Mutex<Child>>>) {
    if let Ok(mut active_child) = store.active_child.lock() {
        *active_child = child;
    }
}

fn ensure_python_worker(
    app: &AppHandle,
    store: &PythonWorkerStore,
    worker_slot: &mut Option<PythonWorker>,
) -> Result<(), String> {
    if let Some(worker) = worker_slot.as_mut() {
        if worker
            .child
            .lock()
            .map_err(|_| "Python worker 子进程锁已损坏".to_string())?
            .try_wait()
            .map_err(|error| format!("检查 Python worker 状态失败: {error}"))?
            .is_none()
        {
            return Ok(());
        }
    }

    // The liveness handshake below may take up to 30 seconds.  Keep the
    // status mutex available so frontend polling can show this transition.
    {
        let mut status = store
            .status
            .lock()
            .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
        status.state = "starting".to_string();
        status.message = "正在启动处理引擎…".to_string();
        status.pid = None;
    }
    store.recovery_epoch.fetch_add(1, Ordering::AcqRel);
    match start_python_worker(app) {
        Ok(worker) => {
            let pid = worker_pid(&worker);
            *worker_slot = Some(worker);
            let mut status = store
                .status
                .lock()
                .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
            status.state = "ready".to_string();
            status.message = "处理引擎已就绪".to_string();
            status.last_error = None;
            status.pid = pid;
            Ok(())
        }
        Err(error) => {
            let mut status = store
                .status
                .lock()
                .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
            status.state = "unavailable".to_string();
            status.message = "处理引擎启动失败".to_string();
            status.last_error = Some(error.clone());
            status.pid = None;
            Err(error)
        }
    }
}

fn prewarm_python_worker(app: &AppHandle, store: &PythonWorkerStore) -> Result<(), String> {
    let mut worker_slot = store
        .worker
        .lock()
        .map_err(|_| "Python worker 锁已损坏".to_string())?;
    ensure_python_worker(app, store, &mut worker_slot)
}

fn stop_python_worker(worker: &mut PythonWorker) -> Result<(), String> {
    let mut child = worker
        .child
        .lock()
        .map_err(|_| "Python worker 子进程锁已损坏".to_string())?;
    if child
        .try_wait()
        .map_err(|error| format!("检查 Python worker 退出状态失败: {error}"))?
        .is_none()
    {
        terminate_worker_process_tree(&mut child)?;
    }
    child
        .wait()
        .map_err(|error| format!("等待 Python worker 退出失败: {error}"))?;
    Ok(())
}

fn shutdown_python_worker(store: &PythonWorkerStore) {
    if let Ok(mut worker_slot) = store.worker.try_lock() {
        if let Some(worker) = worker_slot.as_mut() {
            let _ = stop_python_worker(worker);
        }
        *worker_slot = None;
        set_active_worker_child(store, None);
        return;
    }

    let active_child = store
        .active_child
        .lock()
        .ok()
        .and_then(|child| child.clone());
    if let Some(active_child) = active_child {
        if let Ok(mut child) = active_child.lock() {
            if matches!(child.try_wait(), Ok(None)) {
                let _ = terminate_worker_process_tree(&mut child);
            }
        }
    }
}

fn recover_python_worker(
    app: &AppHandle,
    store: &PythonWorkerStore,
    error: &str,
    force_restart: bool,
    expected_recovery_epoch: Option<u64>,
) {
    let mut worker_slot = match store.worker.lock() {
        Ok(worker_slot) => worker_slot,
        Err(_) => return,
    };
    if let Some(expected_epoch) = expected_recovery_epoch {
        if store.recovery_epoch.load(Ordering::Acquire) != expected_epoch {
            return;
        }
    }
    if let Some(worker) = worker_slot.as_mut() {
        let _ = stop_python_worker(worker);
    }
    *worker_slot = None;

    let should_restart = {
        let mut status = match store.status.lock() {
            Ok(status) => status,
            Err(_) => return,
        };
        status.last_error = Some(error.to_string());
        status.pid = None;
        if !force_restart && status.recovery_attempts >= status.auto_restart_limit {
            status.state = "unavailable".to_string();
            status.message = format!(
                "自动恢复已达到上限（{}/{}）",
                status.recovery_attempts, status.auto_restart_limit
            );
            false
        } else {
            if force_restart {
                status.recovery_attempts = 0;
            } else {
                status.recovery_attempts += 1;
            }
            status.state = "recovering".to_string();
            status.message = if force_restart {
                "正在重新启动处理引擎…".to_string()
            } else {
                format!(
                    "正在自动恢复处理引擎（{}/{}）…",
                    status.recovery_attempts, status.auto_restart_limit
                )
            };
            true
        }
    };
    if !should_restart {
        return;
    }

    if let Err(start_error) = ensure_python_worker(app, store, &mut worker_slot) {
        if let Ok(mut status) = store.status.lock() {
            status.last_error = Some(format!("{error}\n自动恢复失败：{start_error}"));
        }
    } else if let Ok(mut status) = store.status.lock() {
        status.last_error = Some(error.to_string());
        status.message = if force_restart {
            "处理引擎已手动重启".to_string()
        } else {
            format!(
                "处理引擎已恢复（{}/{}）",
                status.recovery_attempts, status.auto_restart_limit
            )
        };
    }
}

fn worker_stderr_tail(worker: &PythonWorker) -> String {
    worker
        .stderr_lines
        .lock()
        .map(|lines| lines.join("\n"))
        .unwrap_or_else(|_| "无法读取 Python worker stderr".to_string())
}

fn terminate_active_worker(store: &PythonWorkerStore) -> Result<PythonWorkerStatus, String> {
    let active_child = store
        .active_child
        .lock()
        .map_err(|_| "Python worker 活动子进程锁已损坏".to_string())?
        .clone()
        .ok_or_else(|| "处理引擎没有正在执行的请求。".to_string())?;
    let mut child = active_child
        .lock()
        .map_err(|_| "Python worker 子进程锁已损坏".to_string())?;
    if child
        .try_wait()
        .map_err(|error| format!("检查 Python worker 状态失败: {error}"))?
        .is_some()
    {
        return Err("当前请求正在结束，请稍后再尝试重启处理引擎。".to_string());
    }
    terminate_worker_process_tree(&mut child)?;
    drop(child);
    store
        .manual_restart_requested
        .store(true, Ordering::Release);

    let mut status = store
        .status
        .lock()
        .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
    status.state = "recovering".to_string();
    status.message = "正在终止当前请求并重启处理引擎…".to_string();
    status.last_error = Some("用户手动重启处理引擎。".to_string());
    status.pid = None;
    status.recovery_attempts = 0;
    Ok(status.clone())
}

fn execute_worker_request(
    app: &AppHandle,
    store: &PythonWorkerStore,
    request: Value,
    on_event: &Channel<Value>,
) -> Result<Value, String> {
    let request_id = request
        .get("request_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "worker 请求缺少 request_id".to_string())?
        .to_string();
    let request_line = serde_json::to_string(&request)
        .map_err(|error| format!("序列化 worker 请求失败: {error}"))?;
    let mut worker_slot = store
        .worker
        .lock()
        .map_err(|_| "Python worker 锁已损坏".to_string())?;
    ensure_python_worker(app, store, &mut worker_slot)?;
    {
        let mut status = store
            .status
            .lock()
            .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
        status.state = "busy".to_string();
        status.message = "处理引擎正在执行请求".to_string();
    }
    let active_child = worker_slot
        .as_ref()
        .map(|worker| Arc::clone(&worker.child))
        .ok_or_else(|| "Python worker 未初始化".to_string())?;
    set_active_worker_child(store, Some(active_child));

    let result = (|| -> Result<Value, String> {
        let worker = worker_slot
            .as_mut()
            .ok_or_else(|| "Python worker 未初始化".to_string())?;
        worker
            .stdin
            .write_all(request_line.as_bytes())
            .and_then(|_| worker.stdin.write_all(b"\n"))
            .and_then(|_| worker.stdin.flush())
            .map_err(|error| format!("发送 Python worker 请求失败: {error}"))?;

        loop {
            let mut line = String::new();
            let bytes_read = worker
                .stdout
                .read_line(&mut line)
                .map_err(|error| format!("读取 Python worker 输出失败: {error}"))?;
            if bytes_read == 0 {
                let stderr = worker_stderr_tail(worker);
                return Err(format!("Python worker 意外退出。{stderr}"));
            }

            let payload: Value = serde_json::from_str(line.trim_end())
                .map_err(|error| format!("解析 Python worker 事件失败: {error}"))?;
            if payload.get("event").and_then(Value::as_str) == Some("worker.response") {
                let response_id = payload
                    .get("request_id")
                    .and_then(Value::as_str)
                    .ok_or_else(|| "Python worker 响应缺少 request_id".to_string())?;
                if response_id != request_id {
                    return Err(format!(
                        "Python worker 响应 ID 不匹配，期望 {request_id}，收到 {response_id}"
                    ));
                }
                if payload.get("ok").and_then(Value::as_bool) == Some(true) {
                    return payload
                        .get("result")
                        .cloned()
                        .ok_or_else(|| "Python worker 成功响应缺少 result".to_string());
                }
                let error = payload
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("Python worker 返回未知错误");
                return Err(error.to_string());
            }

            on_event
                .send(payload)
                .map_err(|error| format!("推送 Python worker 事件失败: {error}"))?;
        }
    })();
    set_active_worker_child(store, None);

    if let Err(error) = &result {
        if let Some(worker) = worker_slot.as_mut() {
            let _ = stop_python_worker(worker);
        }
        *worker_slot = None;
        drop(worker_slot);
        let force_restart = store.manual_restart_requested.swap(false, Ordering::AcqRel);
        recover_python_worker(app, store, error, force_restart, None);
    } else if let Ok(mut status) = store.status.lock() {
        status.state = "ready".to_string();
        status.message = "处理引擎已就绪".to_string();
        status.pid = worker_slot.as_ref().and_then(worker_pid);
    }
    result
}

fn worker_request_id(prefix: &str) -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or_default();
    format!("{prefix}-{timestamp}")
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
async fn list_font_targets(
    app: AppHandle,
    file_path: String,
) -> Result<FontTargetResponse, String> {
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

fn get_python_worker_status_blocking(
    app: &AppHandle,
    store: &PythonWorkerStore,
) -> Result<PythonWorkerStatus, String> {
    let exit_error = if let Ok(mut worker_slot) = store.worker.try_lock() {
        if let Some(worker) = worker_slot.as_mut() {
            let worker_status = worker
                .child
                .lock()
                .map_err(|_| "Python worker 子进程锁已损坏".to_string())?
                .try_wait();
            match worker_status {
                Ok(Some(exit_status)) => {
                    *worker_slot = None;
                    let recovery_epoch = store.recovery_epoch.fetch_add(1, Ordering::AcqRel) + 1;
                    Some((
                        format!("检测到处理引擎意外退出：{exit_status}"),
                        recovery_epoch,
                    ))
                }
                Ok(None) | Err(_) => None,
            }
        } else {
            None
        }
    } else {
        // 正在执行请求时由执行链路负责检测 stdout EOF，避免与任务线程争用 child。
        None
    };

    if let Some((error, recovery_epoch)) = exit_error {
        let (status_snapshot, should_recover) = {
            let mut status = store
                .status
                .lock()
                .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
            status.last_error = Some(error.clone());
            status.pid = None;
            if status.recovery_attempts >= status.auto_restart_limit {
                status.state = "unavailable".to_string();
                status.message = format!(
                    "自动恢复已达到上限（{}/{}）",
                    status.recovery_attempts, status.auto_restart_limit
                );
                (status.clone(), false)
            } else {
                status.state = "recovering".to_string();
                status.message = "检测到处理引擎退出，正在自动恢复…".to_string();
                (status.clone(), true)
            }
        };
        if should_recover {
            let recovery_app = app.clone();
            std::thread::spawn(move || {
                let recovery_store = recovery_app.state::<PythonWorkerStore>();
                recover_python_worker(
                    &recovery_app,
                    recovery_store.inner(),
                    &error,
                    false,
                    Some(recovery_epoch),
                );
            });
        }
        return Ok(status_snapshot);
    }

    store
        .status
        .lock()
        .map(|status| status.clone())
        .map_err(|_| "Python worker 状态锁已损坏".to_string())
}

#[tauri::command]
async fn get_python_worker_status(app: AppHandle) -> Result<PythonWorkerStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let store = app.state::<PythonWorkerStore>();
        get_python_worker_status_blocking(&app, store.inner())
    })
    .await
    .map_err(|error| format!("异步获取 Python worker 状态失败: {error}"))?
}

#[tauri::command]
fn set_python_worker_auto_restart_limit(
    store: State<'_, PythonWorkerStore>,
    limit: u8,
) -> Result<PythonWorkerStatus, String> {
    let mut status = store
        .status
        .lock()
        .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
    status.auto_restart_limit = limit.min(5);
    Ok(status.clone())
}

fn restart_python_worker_blocking(
    app: &AppHandle,
    store: &PythonWorkerStore,
) -> Result<PythonWorkerStatus, String> {
    store.recovery_epoch.fetch_add(1, Ordering::AcqRel);
    let mut worker_slot = match store.worker.try_lock() {
        Ok(worker_slot) => worker_slot,
        Err(_) => return terminate_active_worker(store),
    };
    {
        let mut status = store
            .status
            .lock()
            .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
        status.recovery_attempts = 0;
        status.last_error = None;
        status.state = "recovering".to_string();
        status.message = "正在重新启动处理引擎…".to_string();
        status.pid = None;
    }
    if let Some(worker) = worker_slot.as_mut() {
        stop_python_worker(worker)?;
    }
    *worker_slot = None;
    ensure_python_worker(app, store, &mut worker_slot)?;
    let mut status = store
        .status
        .lock()
        .map_err(|_| "Python worker 状态锁已损坏".to_string())?;
    status.message = "处理引擎已手动重启".to_string();
    Ok(status.clone())
}

#[tauri::command]
async fn restart_python_worker(app: AppHandle) -> Result<PythonWorkerStatus, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let store = app.state::<PythonWorkerStore>();
        restart_python_worker_blocking(&app, store.inner())
    })
    .await
    .map_err(|error| format!("异步重启 Python worker 失败: {error}"))?
}

#[tauri::command]
async fn list_font_targets_batch(
    app: AppHandle,
    file_paths: Vec<String>,
    on_event: Channel<Value>,
) -> Result<Vec<FontTargetResponse>, String> {
    if file_paths.is_empty() {
        return Ok(Vec::new());
    }

    tauri::async_runtime::spawn_blocking(move || -> Result<Vec<FontTargetResponse>, String> {
        let store = app.state::<PythonWorkerStore>();
        let result = execute_worker_request(
            &app,
            store.inner(),
            json!({
                "request_id": worker_request_id("font-targets"),
                "command": "list-fonts-batch",
                "input_files": file_paths,
            }),
            &on_event,
        )?;
        serde_json::from_value(result).map_err(|error| format!("解析字体扫描结果失败: {error}"))
    })
    .await
    .map_err(|error| format!("异步字体扫描失败: {error}"))?
}

#[tauri::command]
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

#[tauri::command]
async fn run_epub_task(
    app: AppHandle,
    request: FrontendTaskRequest,
    on_event: Channel<Value>,
) -> Result<Value, String> {
    let task_id = request.task_id.clone();
    let total_files = request.input_files.len();
    on_event
        .send(json!({
            "event": "task.launching",
            "task_id": task_id,
            "status": "starting",
            "progress": 0,
            "message": "正在向处理引擎提交任务…",
            "total_files": total_files,
            "level": "info"
        }))
        .map_err(|error| format!("推送任务启动事件失败: {error}"))?;

    tauri::async_runtime::spawn_blocking(move || -> Result<Value, String> {
        let store = app.state::<PythonWorkerStore>();
        execute_worker_request(
            &app,
            store.inner(),
            json!({
                "request_id": task_id,
                "command": "run",
                "request": request,
            }),
            &on_event,
        )
    })
    .await
    .map_err(|error| format!("异步任务失败: {error}"))?
}

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

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            app.manage(PersistedStore::load(app.handle()));
            app.manage(PythonWorkerStore {
                worker: Mutex::new(None),
                active_child: Mutex::new(None),
                manual_restart_requested: AtomicBool::new(false),
                recovery_epoch: AtomicU64::new(0),
                status: Mutex::new(PythonWorkerStatus::default()),
            });
            setup_window_effects(app)?;
            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                let worker_store = app_handle.state::<PythonWorkerStore>();
                if let Err(error) = prewarm_python_worker(&app_handle, worker_store.inner()) {
                    eprintln!("Python worker 预热失败，将在首次任务时重试：{error}");
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            collect_epub_files,
            get_log_path,
            get_persisted_store_path,
            get_python_worker_status,
            list_font_targets,
            list_font_targets_batch,
            load_persisted_state,
            open_path,
            resolve_input_sources,
            run_epub_task,
            save_persisted_state,
            set_python_worker_auto_restart_limit,
            restart_python_worker
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            let worker_store = app_handle.state::<PythonWorkerStore>();
            shutdown_python_worker(worker_store.inner());
        }
    });
}
