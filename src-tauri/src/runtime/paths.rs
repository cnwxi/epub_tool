use std::{fs, path::PathBuf};

use tauri::{AppHandle, Manager};

pub fn resolve_log_path(app: &AppHandle) -> Result<PathBuf, String> {
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|error| format!("无法定位日志目录: {error}"))?;
    fs::create_dir_all(&log_dir)
        .map_err(|error| format!("创建日志目录失败 {}: {error}", log_dir.display()))?;
    Ok(log_dir.join("log.txt"))
}
