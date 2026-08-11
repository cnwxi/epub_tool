use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Manager, State};

use crate::runtime::workspace_root;

pub struct PersistedStore {
    path: Option<PathBuf>,
    data: Mutex<BTreeMap<String, Value>>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PersistedStateResponse {
    found: bool,
    value: Value,
}

impl PersistedStore {
    pub fn load(app: &AppHandle) -> Self {
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
        self.data
            .lock()
            .map_err(|_| "配置存储锁已损坏，无法读取状态。".to_string())
            .map(|store| store.get(key).cloned())
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

#[tauri::command]
pub async fn get_persisted_store_path(store: State<'_, PersistedStore>) -> Result<String, String> {
    store
        .path
        .as_ref()
        .map(|path| path.to_string_lossy().to_string())
        .ok_or_else(|| "当前运行环境未提供配置存储路径。".to_string())
}

#[tauri::command]
pub async fn load_persisted_state(
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
pub async fn save_persisted_state(
    store: State<'_, PersistedStore>,
    key: String,
    value: Value,
) -> Result<(), String> {
    store.save_value(key, value)
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
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    let Some(file_name) = path.file_name().and_then(|value| value.to_str()) else {
        return Ok(());
    };
    let prefix = format!("{file_name}.corrupt-");
    let mut backups = Vec::new();
    for entry in fs::read_dir(parent)
        .map_err(|error| format!("读取配置目录失败 {}: {error}", parent.display()))?
    {
        let entry = entry.map_err(|error| format!("读取配置目录项失败: {error}"))?;
        let path = entry.path();
        if !path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|name| name.starts_with(&prefix))
        {
            continue;
        }
        let modified = entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .unwrap_or(UNIX_EPOCH);
        backups.push((modified, path));
    }
    backups.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    for (_, path) in backups.into_iter().skip(retain) {
        fs::remove_file(&path)
            .map_err(|error| format!("清理旧损坏配置备份失败 {}: {error}", path.display()))?;
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
