use std::{fs, path::Path, sync::Arc};

#[cfg(mobile)]
use std::io::Write;

#[cfg(not(mobile))]
use std::process::Command;

use tauri::AppHandle;

#[cfg(mobile)]
use tauri::Manager;

#[cfg(not(mobile))]
use super::paths::resolve_path;

pub trait PlatformFiles: Send + Sync {
    fn collect_epub_files(&self, directory_path: &str) -> Result<Vec<String>, String>;
    fn validate_output_directory(&self, directory_path: &str) -> Result<(), String>;
    fn resolve_input_sources(&self, input_paths: &[String]) -> Result<Vec<String>, String>;
    fn stage_source(&self, source_path: &str, extension: &str) -> Result<String, String>;
    fn export_output(&self, source_path: &str, destination_path: &str) -> Result<(), String>;
    fn open_path(&self, path: &str) -> Result<(), String>;
}

#[cfg(not(mobile))]
pub fn create(app: AppHandle) -> Arc<dyn PlatformFiles> {
    Arc::new(DesktopFiles { app })
}

#[cfg(mobile)]
pub fn create(app: AppHandle) -> Arc<dyn PlatformFiles> {
    Arc::new(MobileFiles { app })
}

fn collect_epubs_recursive(directory: &Path, result: &mut Vec<String>) -> Result<(), String> {
    let entries = fs::read_dir(directory)
        .map_err(|error| format!("读取目录失败 {}: {error}", directory.display()))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("读取目录项失败: {error}"))?;
        let path = entry.path();
        if path.is_dir() {
            collect_epubs_recursive(&path, result)?;
        } else if path
            .extension()
            .and_then(|value| value.to_str())
            .is_some_and(|value| value.eq_ignore_ascii_case("epub"))
        {
            result.push(path.to_string_lossy().to_string());
        }
    }
    Ok(())
}

#[cfg(not(mobile))]
struct DesktopFiles {
    app: AppHandle,
}

#[cfg(not(mobile))]
impl PlatformFiles for DesktopFiles {
    fn collect_epub_files(&self, directory_path: &str) -> Result<Vec<String>, String> {
        let directory = resolve_path(&self.app, directory_path)?;
        if !directory.is_dir() {
            return Err(format!("不是有效目录: {}", directory.display()));
        }
        let mut files = Vec::new();
        collect_epubs_recursive(&directory, &mut files)?;
        files.sort();
        Ok(files)
    }

    fn validate_output_directory(&self, directory_path: &str) -> Result<(), String> {
        let directory = resolve_path(&self.app, directory_path)?;
        if directory.is_dir() {
            Ok(())
        } else {
            Err(format!("不是有效目录: {}", directory.display()))
        }
    }

    fn resolve_input_sources(&self, input_paths: &[String]) -> Result<Vec<String>, String> {
        let mut files = Vec::new();
        for input_path in input_paths {
            let path = resolve_path(&self.app, input_path)?;
            if path.is_dir() {
                collect_epubs_recursive(&path, &mut files)?;
            } else if path.is_file()
                && path
                    .extension()
                    .and_then(|value| value.to_str())
                    .is_some_and(|value| value.eq_ignore_ascii_case("epub"))
            {
                files.push(path.to_string_lossy().to_string());
            }
        }
        files.sort();
        files.dedup();
        Ok(files)
    }

    fn stage_source(&self, source_path: &str, _extension: &str) -> Result<String, String> {
        Ok(resolve_path(&self.app, source_path)?
            .to_string_lossy()
            .to_string())
    }

    fn export_output(&self, source_path: &str, destination_path: &str) -> Result<(), String> {
        fs::copy(
            resolve_path(&self.app, source_path)?,
            resolve_path(&self.app, destination_path)?,
        )
        .map(|_| ())
        .map_err(|error| format!("导出处理结果失败: {error}"))
    }

    fn open_path(&self, path: &str) -> Result<(), String> {
        let external = path.to_ascii_lowercase().starts_with("https://")
            || path.to_ascii_lowercase().starts_with("http://");
        let target = if external {
            path.to_string()
        } else {
            resolve_path(&self.app, path)?.to_string_lossy().to_string()
        };
        let mut command = if cfg!(target_os = "macos") {
            let mut command = Command::new("open");
            command.arg(&target);
            command
        } else if cfg!(target_os = "windows") {
            let mut command = Command::new("cmd");
            command.args(["/C", "start", "", &target]);
            command
        } else {
            let mut command = Command::new("xdg-open");
            command.arg(&target);
            command
        };
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        let status = command
            .status()
            .map_err(|error| format!("打开路径失败: {error}"))?;
        if status.success() {
            Ok(())
        } else {
            Err(format!("系统命令返回失败状态: {status}"))
        }
    }
}

#[cfg(mobile)]
struct MobileFiles {
    app: AppHandle,
}

#[cfg(mobile)]
impl MobileFiles {
    fn staging_directory(&self) -> Result<std::path::PathBuf, String> {
        let directory = self
            .app
            .path()
            .app_cache_dir()
            .map_err(|error| format!("无法定位移动端临时目录: {error}"))?
            .join("epub-tool-inputs");
        fs::create_dir_all(&directory)
            .map_err(|error| format!("创建移动端临时目录失败 {}: {error}", directory.display()))?;
        Ok(directory)
    }
}

#[cfg(mobile)]
impl PlatformFiles for MobileFiles {
    fn collect_epub_files(&self, _directory_path: &str) -> Result<Vec<String>, String> {
        Err("Android 和 iOS 不支持目录扫描，请直接选择 EPUB 文件。".to_string())
    }

    fn validate_output_directory(&self, _directory_path: &str) -> Result<(), String> {
        Err("移动端不支持自定义输出目录，请在任务完成后导出结果。".to_string())
    }

    fn resolve_input_sources(&self, input_paths: &[String]) -> Result<Vec<String>, String> {
        input_paths
            .iter()
            .map(|path| self.stage_source(path, "epub"))
            .collect()
    }

    fn stage_source(&self, source_path: &str, extension: &str) -> Result<String, String> {
        use std::{str::FromStr, time::SystemTime};
        use tauri_plugin_fs::{FilePath, FsExt, OpenOptions};

        let source = match FilePath::from_str(source_path) {
            Ok(source) => source,
            Err(never) => match never {},
        };
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
            return Err("移动端暂存文件缺少有效扩展名。".to_string());
        }
        let timestamp = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|value| value.as_nanos())
            .unwrap_or_default();
        let destination = self
            .staging_directory()?
            .join(format!("{timestamp}.{extension}"));
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
        let destination = match FilePath::from_str(destination_path) {
            Ok(destination) => destination,
            Err(never) => match never {},
        };
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

    fn open_path(&self, _path: &str) -> Result<(), String> {
        Err("移动端不支持在应用内直接打开本地路径，请使用导出功能保存处理结果。".to_string())
    }
}
