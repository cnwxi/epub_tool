use std::fs;

use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Copy)]
pub struct RuntimeResources;

const OPENCC_FILES: [&str; 7] = [
    "NOTICE.txt",
    "STCharacters.txt",
    "STPhrases.txt",
    "TSCharacters.txt",
    "TSPhrases.txt",
    "s2t.json",
    "t2s.json",
];

pub fn prepare(app: &AppHandle) -> Result<RuntimeResources, String> {
    use tauri::path::BaseDirectory;

    let root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位 Android 应用数据目录: {error}"))?
        .join("runtime-resources");
    let opencc_dir = root.join("opencc");
    let version_path = root.join(".epub-tool-resource-version");
    fs::create_dir_all(&root)
        .map_err(|error| format!("创建 Android 资源目录失败 {}: {error}", root.display()))?;

    let resources_are_current = fs::read_to_string(&version_path)
        .map(|value| value.trim() == env!("CARGO_PKG_VERSION"))
        .unwrap_or(false)
        && OPENCC_FILES
            .iter()
            .all(|name| opencc_dir.join(name).is_file());

    if !resources_are_current {
        for name in OPENCC_FILES {
            copy_resource(
                app,
                &format!("opencc/{name}"),
                &opencc_dir.join(name),
                BaseDirectory::Resource,
            )?;
        }
        fs::write(&version_path, env!("CARGO_PKG_VERSION")).map_err(|error| {
            format!(
                "写入 Android 资源版本标记失败 {}: {error}",
                version_path.display()
            )
        })?;
    }

    crate::rust_backend::text::configure_resource_dir(opencc_dir.clone())?;
    Ok(RuntimeResources)
}

fn copy_resource(
    app: &AppHandle,
    relative_path: &str,
    destination: &std::path::Path,
    base: tauri::path::BaseDirectory,
) -> Result<(), String> {
    use tauri_plugin_fs::FsExt;

    let source = app
        .path()
        .resolve(relative_path, base)
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
