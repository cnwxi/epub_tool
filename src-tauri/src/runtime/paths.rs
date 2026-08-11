use std::{fs, path::PathBuf};

use tauri::{AppHandle, Manager};

pub fn workspace_root() -> Option<PathBuf> {
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

    if let Ok(directory) = app.path().app_data_dir() {
        fs::create_dir_all(&directory)
            .map_err(|error| format!("创建应用数据目录失败 {}: {error}", directory.display()))?;
        return Ok(directory);
    }

    app.path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录: {error}"))
}

pub fn resolve_log_path(app: &AppHandle) -> Result<PathBuf, String> {
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

pub fn resolve_path(app: &AppHandle, path: &str) -> Result<PathBuf, String> {
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
