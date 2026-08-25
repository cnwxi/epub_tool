use std::sync::Mutex;

use tauri::State;

use crate::runtime::RuntimeServices;

pub struct OpenedSources(Mutex<Vec<String>>);

impl OpenedSources {
    pub fn new() -> Self {
        Self(Mutex::new(Vec::new()))
    }

    #[cfg(mobile)]
    pub fn extend(&self, sources: impl IntoIterator<Item = String>) {
        if let Ok(mut stored) = self.0.lock() {
            stored.extend(sources);
        }
    }

    fn take(&self) -> Result<Vec<String>, String> {
        self.0
            .lock()
            .map(|mut sources| std::mem::take(&mut *sources))
            .map_err(|_| "已打开文件列表锁已损坏".to_string())
    }
}

#[tauri::command]
pub async fn take_opened_sources(state: State<'_, OpenedSources>) -> Result<Vec<String>, String> {
    state.take()
}

#[tauri::command]
pub async fn resolve_input_sources(
    services: State<'_, RuntimeServices>,
    input_paths: Vec<String>,
) -> Result<Vec<String>, String> {
    let files = services.files();
    tauri::async_runtime::spawn_blocking(move || files.resolve_input_sources(&input_paths))
        .await
        .map_err(|error| format!("解析输入来源失败: {error}"))?
}

#[tauri::command]
pub async fn stage_source_for_task(
    services: State<'_, RuntimeServices>,
    source_path: String,
    extension: String,
) -> Result<String, String> {
    let files = services.files();
    tauri::async_runtime::spawn_blocking(move || files.stage_source(&source_path, &extension))
        .await
        .map_err(|error| format!("暂存任务输入失败: {error}"))?
}

#[tauri::command]
pub async fn export_output(
    services: State<'_, RuntimeServices>,
    source_path: String,
    destination_path: String,
) -> Result<(), String> {
    let files = services.files();
    tauri::async_runtime::spawn_blocking(move || {
        files.export_output(&source_path, &destination_path)
    })
    .await
    .map_err(|error| format!("导出处理结果失败: {error}"))?
}
