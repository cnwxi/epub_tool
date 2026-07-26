#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use epub_tool_newui::{rust_backend, FrontendTaskRequest};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};
use tauri::{ipc::Channel, AppHandle, Manager, State};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "macos")]
use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};

#[cfg(target_os = "windows")]
use window_vibrancy::{apply_blur, apply_mica};

const COVER_PREVIEW_MAX_BYTES: u64 = 20 * 1024 * 1024;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Debug, Serialize)]
struct FontTargetResponse {
    ok: bool,
    input_file: String,
    font_families: Vec<String>,
    #[serde(default)]
    error: Option<String>,
}

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

struct RustBackendState {
    auto_restart_limit: Mutex<u8>,
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
}

fn rust_backend_status(auto_restart_limit: u8) -> EngineStatus {
    EngineStatus {
        state: "ready".to_string(),
        message: "Rust 处理引擎已就绪".to_string(),
        last_error: None,
        pid: None,
        recovery_attempts: 0,
        auto_restart_limit,
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
    file_path: String,
) -> Result<FontTargetResponse, String> {
    rust_backend::font::font_targets::list_font_targets(Path::new(&file_path)).map(
        |font_families| FontTargetResponse {
            ok: true,
            input_file: file_path,
            font_families,
            error: None,
        },
    )
}


#[tauri::command]
fn get_python_worker_status(
    state: State<'_, RustBackendState>,
) -> Result<EngineStatus, String> {
    state
        .auto_restart_limit
        .lock()
        .map(|limit| rust_backend_status(*limit))
        .map_err(|_| "Rust 后端状态锁已损坏".to_string())
}

#[tauri::command]
fn set_python_worker_auto_restart_limit(
    state: State<'_, RustBackendState>,
    limit: u8,
) -> Result<EngineStatus, String> {
    let mut current_limit = state
        .auto_restart_limit
        .lock()
        .map_err(|_| "Rust 后端状态锁已损坏".to_string())?;
    *current_limit = limit.min(5);
    Ok(rust_backend_status(*current_limit))
}

#[tauri::command]
fn restart_python_worker(state: State<'_, RustBackendState>) -> Result<EngineStatus, String> {
    state
        .auto_restart_limit
        .lock()
        .map(|limit| rust_backend_status(*limit))
        .map_err(|_| "Rust 后端状态锁已损坏".to_string())
}

#[tauri::command]
async fn list_font_targets_batch(
    file_paths: Vec<String>,
    on_event: Channel<Value>,
) -> Result<Vec<FontTargetResponse>, String> {
    if file_paths.is_empty() {
        return Ok(Vec::new());
    }

    tauri::async_runtime::spawn_blocking(move || -> Result<Vec<FontTargetResponse>, String> {
        let results = file_paths
            .iter()
            .map(|file_path| match rust_backend::font::font_targets::list_font_targets(Path::new(file_path)) {
                Ok(font_families) => FontTargetResponse {
                        ok: true,
                        input_file: file_path.clone(),
                        font_families,
                        error: None,
                    },
                Err(error) => FontTargetResponse {
                    ok: false,
                    input_file: file_path.clone(),
                    font_families: Vec::new(),
                    error: Some(error),
                },
            })
            .collect::<Vec<_>>();
        let total_files = results.len();
        for (position, result) in results.iter().enumerate() {
            on_event
                .send(json!({
                    "event": "font-targets.progress",
                    "current_index": position + 1,
                    "total_files": total_files,
                    "result": result,
                }))
                .map_err(|error| format!("推送 Rust 字体扫描事件失败: {error}"))?;
        }
        Ok(results)
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
async fn validate_output_directory(app: AppHandle, directory_path: String) -> Result<(), String> {
    let resolved = resolve_path(&app, &directory_path)?;
    if !resolved.is_dir() {
        return Err(format!("不是有效目录: {}", resolved.display()));
    }
    Ok(())
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
    let task_id = request.taskId.clone();
    let total_files = request.inputFiles.len();
    if request.taskType == "chinese_convert" {
        if let Some(resource_dir) = resolve_opencc_resource_dir(&app) {
            rust_backend::text::configure_resource_dir(resource_dir)?;
        }
    }
    if request.taskType == "decrypt_font" {
        if let Some(resources) = resolve_rust_ocr_resources(&app) {
            rust_backend::font::decrypt_font::configure_ocr_resources(resources)?;
        }
    }
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
        let log_path = resolve_log_path(&app)?;
        rust_backend::run(&request, &log_path, &mut |event| {
            on_event
                .send(event)
                .map_err(|error| format!("推送 Rust 后端事件失败: {error}"))
        })
    })
    .await
    .map_err(|error| format!("异步任务失败: {error}"))?
}

fn resolve_opencc_resource_dir(app: &AppHandle) -> Option<PathBuf> {
    workspace_root()
        .map(|root| root.join("src-tauri").join("bundle-resources").join("opencc"))
        .or_else(|| app.path().resource_dir().ok().map(|directory| directory.join("opencc")))
}

fn resolve_rust_ocr_resources(
    app: &AppHandle,
) -> Option<rust_backend::font::decrypt_font::OcrResourcePaths> {
    let model_dir = resolve_ocr_model_dir(app)?;
    Some(rust_backend::font::decrypt_font::OcrResourcePaths {
        model_dir,
    })
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
            app.manage(RustBackendState {
                auto_restart_limit: Mutex::new(2),
            });
            setup_window_effects(app)?;
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
            read_image_preview,
            resolve_input_sources,
            run_epub_task,
            save_persisted_state,
            set_python_worker_auto_restart_limit,
            restart_python_worker,
            validate_output_directory
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|_, _| {});
}
