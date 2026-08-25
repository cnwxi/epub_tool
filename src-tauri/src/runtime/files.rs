use std::{fs, io::Write, path::PathBuf, sync::Arc};

use tauri::{AppHandle, Manager};

pub trait PlatformFiles: Send + Sync {
    fn resolve_input_sources(&self, input_paths: &[String]) -> Result<Vec<String>, String>;
    fn stage_source(&self, source_path: &str, extension: &str) -> Result<String, String>;
    fn export_output(&self, source_path: &str, destination_path: &str) -> Result<(), String>;
}

pub fn create(app: AppHandle) -> Arc<dyn PlatformFiles> {
    Arc::new(AndroidFiles { app })
}

struct AndroidFiles {
    app: AppHandle,
}

impl AndroidFiles {
    fn staging_directory(&self) -> Result<PathBuf, String> {
        let directory = self
            .app
            .path()
            .app_cache_dir()
            .map_err(|error| format!("无法定位 Android 临时目录: {error}"))?
            .join("epub-tool-inputs");
        fs::create_dir_all(&directory).map_err(|error| {
            format!("创建 Android 临时目录失败 {}: {error}", directory.display())
        })?;
        Ok(directory)
    }
}

fn staged_file_name(source_path: &str, extension: &str) -> String {
    use percent_encoding::percent_decode_str;

    let source = source_path.split(['?', '#']).next().unwrap_or(source_path);
    let decoded = percent_decode_str(source).decode_utf8_lossy().into_owned();
    let candidate = decoded
        .rsplit(['/', '\\'])
        .next()
        .filter(|name| !name.is_empty() && *name != "." && *name != "..")
        .unwrap_or("input");
    let mut safe = candidate
        .chars()
        .map(|character| match character {
            '/' | '\\' | ':' => '_',
            character if character.is_control() => '_',
            character => character,
        })
        .collect::<String>();
    if safe.is_empty() {
        safe = "input".to_string();
    }
    if !safe
        .rsplit_once('.')
        .is_some_and(|(_, value)| value.eq_ignore_ascii_case(extension))
    {
        safe.push('.');
        safe.push_str(extension);
    }
    safe
}

impl PlatformFiles for AndroidFiles {
    fn resolve_input_sources(&self, input_paths: &[String]) -> Result<Vec<String>, String> {
        input_paths
            .iter()
            .map(|path| self.stage_source(path, "epub"))
            .collect()
    }

    fn stage_source(&self, source_path: &str, extension: &str) -> Result<String, String> {
        use std::{str::FromStr, time::SystemTime};
        use tauri_plugin_fs::{FilePath, FsExt, OpenOptions};

        let source =
            FilePath::from_str(source_path).map_err(|_| "无效的 Android 文件 URI。".to_string())?;
        let mut source_options = OpenOptions::new();
        source_options.read(true);
        let mut source = self
            .app
            .fs()
            .open(source, source_options)
            .map_err(|error| format!("读取所选文件失败: {error}"))?;
        let extension = extension
            .trim_start_matches('.')
            .chars()
            .filter(|character| character.is_ascii_alphanumeric())
            .collect::<String>();
        if extension.is_empty() {
            return Err("Android 暂存文件缺少有效扩展名。".to_string());
        }
        let timestamp = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_nanos())
            .unwrap_or_default();
        let directory = self.staging_directory()?;
        let file_name = staged_file_name(source_path, &extension);
        let initial_destination = directory.join(&file_name);
        let destination = if initial_destination.exists() {
            let path = std::path::Path::new(&file_name);
            let stem = path
                .file_stem()
                .and_then(|value| value.to_str())
                .unwrap_or("input");
            let suffix = path
                .extension()
                .and_then(|value| value.to_str())
                .unwrap_or(&extension);
            directory.join(format!("{stem}-{timestamp}.{suffix}"))
        } else {
            initial_destination
        };
        let mut destination_file = fs::File::create(&destination)
            .map_err(|error| format!("创建暂存文件失败 {}: {error}", destination.display()))?;
        std::io::copy(&mut source, &mut destination_file)
            .map_err(|error| format!("暂存所选文件失败 {}: {error}", destination.display()))?;
        Ok(destination.to_string_lossy().to_string())
    }

    fn export_output(&self, source_path: &str, destination_path: &str) -> Result<(), String> {
        use std::str::FromStr;
        use tauri_plugin_fs::{FilePath, FsExt, OpenOptions};

        let mut source = fs::File::open(source_path)
            .map_err(|error| format!("读取处理结果失败 {source_path}: {error}"))?;
        let destination = FilePath::from_str(destination_path)
            .map_err(|_| "无效的 Android 导出文件 URI。".to_string())?;
        let mut destination_options = OpenOptions::new();
        destination_options.write(true).truncate(true).create(true);
        let mut output = self
            .app
            .fs()
            .open(destination, destination_options)
            .map_err(|error| format!("创建导出文件失败: {error}"))?;
        std::io::copy(&mut source, &mut output)
            .map_err(|error| format!("导出处理结果失败: {error}"))?;
        output
            .flush()
            .map_err(|error| format!("刷新导出文件失败: {error}"))
    }
}
