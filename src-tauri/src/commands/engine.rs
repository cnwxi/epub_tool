use tauri::State;

use crate::runtime::{EngineStatus, PlatformCapabilities, RuntimeServices};

#[tauri::command]
pub async fn get_engine_status(
    services: State<'_, RuntimeServices>,
) -> Result<EngineStatus, String> {
    let engine = services.engine();
    tauri::async_runtime::spawn_blocking(move || engine.status())
        .await
        .map_err(|error| format!("读取 Rust 引擎状态失败: {error}"))?
}

#[tauri::command]
pub fn set_engine_auto_restart_limit(
    services: State<'_, RuntimeServices>,
    limit: u8,
) -> Result<EngineStatus, String> {
    services.engine().set_auto_restart_limit(limit)
}

#[tauri::command]
pub async fn restart_engine(services: State<'_, RuntimeServices>) -> Result<EngineStatus, String> {
    let engine = services.engine();
    tauri::async_runtime::spawn_blocking(move || engine.restart())
        .await
        .map_err(|error| format!("重启 Rust 引擎失败: {error}"))?
}

#[tauri::command]
pub fn get_platform_capabilities(services: State<'_, RuntimeServices>) -> PlatformCapabilities {
    services.capabilities.clone()
}
