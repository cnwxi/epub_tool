use std::path::PathBuf;

#[cfg(mobile)]
use std::fs;

use tauri::{AppHandle, Manager};

#[cfg(not(mobile))]
use super::paths::workspace_root;

#[derive(Debug, Clone)]
pub struct RuntimeResources {
    pub opencc_dir: Option<PathBuf>,
    pub ocr_model_dir: Option<PathBuf>,
}

#[cfg(not(mobile))]
pub fn prepare(app: &AppHandle) -> Result<RuntimeResources, String> {
    let resources = RuntimeResources {
        opencc_dir: resolve_opencc_dir(app),
        ocr_model_dir: resolve_ocr_model_dir(app),
    };
    if let Some(directory) = &resources.opencc_dir {
        crate::rust_backend::text::configure_resource_dir(directory.clone())?;
    }
    if let Some(directory) = &resources.ocr_model_dir {
        crate::rust_backend::font::decrypt_font::configure_ocr_resources(
            crate::rust_backend::font::decrypt_font::OcrResourcePaths {
                model_dir: directory.clone(),
            },
        )?;
    }
    Ok(resources)
}

#[cfg(not(mobile))]
fn resolve_opencc_dir(app: &AppHandle) -> Option<PathBuf> {
    workspace_root()
        .map(|root| {
            root.join("src-tauri")
                .join("bundle-resources")
                .join("opencc")
        })
        .or_else(|| {
            app.path()
                .resource_dir()
                .ok()
                .map(|directory| directory.join("opencc"))
        })
        .filter(|directory| directory.is_dir())
}

#[cfg(not(mobile))]
fn resolve_ocr_model_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(path) = std::env::var("EPUB_TOOL_OCR_ONNX_MODEL_DIR") {
        if !path.is_empty() {
            return Some(PathBuf::from(path));
        }
    }
    let model_name = std::env::var("EPUB_TOOL_OCR_MODEL_NAME")
        .ok()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "PP-OCRv6_small_rec".to_string())
        + "_onnx";
    workspace_root()
        .map(|root| {
            root.join("src-tauri")
                .join("bundle-resources")
                .join("ocr-models")
                .join(&model_name)
        })
        .or_else(|| {
            app.path()
                .resource_dir()
                .ok()
                .map(|directory| directory.join("ocr-models").join(model_name))
        })
        .filter(|directory| directory.is_dir())
}

#[cfg(mobile)]
const OPENCC_FILES: [&str; 7] = [
    "NOTICE.txt",
    "STCharacters.txt",
    "STPhrases.txt",
    "TSCharacters.txt",
    "TSPhrases.txt",
    "s2t.json",
    "t2s.json",
];

#[cfg(mobile)]
const OCR_MODEL_NAME: &str = "PP-OCRv6_small_rec_onnx";

#[cfg(mobile)]
const OCR_FILES: [&str; 2] = ["inference.onnx", "inference.yml"];

#[cfg(mobile)]
pub fn prepare(app: &AppHandle) -> Result<RuntimeResources, String> {
    use tauri::path::BaseDirectory;

    let root = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("无法定位移动端应用数据目录: {error}"))?
        .join("runtime-resources");
    let opencc_dir = root.join("opencc");
    let ocr_model_dir = root.join("ocr-models").join(OCR_MODEL_NAME);
    let version_path = root.join(".epub-tool-resource-version");
    fs::create_dir_all(&root)
        .map_err(|error| format!("创建移动端资源目录失败 {}: {error}", root.display()))?;

    let resources_are_current = fs::read_to_string(&version_path)
        .map(|value| value.trim() == env!("CARGO_PKG_VERSION"))
        .unwrap_or(false)
        && OPENCC_FILES
            .iter()
            .all(|name| opencc_dir.join(name).is_file())
        && OCR_FILES
            .iter()
            .all(|name| ocr_model_dir.join(name).is_file());

    if !resources_are_current {
        for name in OPENCC_FILES {
            copy_resource(
                app,
                &format!("opencc/{name}"),
                &opencc_dir.join(name),
                BaseDirectory::Resource,
            )?;
        }
        for name in OCR_FILES {
            copy_resource(
                app,
                &format!("ocr-models/{OCR_MODEL_NAME}/{name}"),
                &ocr_model_dir.join(name),
                BaseDirectory::Resource,
            )?;
        }
        fs::write(&version_path, env!("CARGO_PKG_VERSION")).map_err(|error| {
            format!(
                "写入移动端资源版本标记失败 {}: {error}",
                version_path.display()
            )
        })?;
    }

    crate::rust_backend::text::configure_resource_dir(opencc_dir.clone())?;
    crate::rust_backend::font::decrypt_font::configure_ocr_resources(
        crate::rust_backend::font::decrypt_font::OcrResourcePaths {
            model_dir: ocr_model_dir.clone(),
        },
    )?;

    Ok(RuntimeResources {
        opencc_dir: Some(opencc_dir),
        ocr_model_dir: Some(ocr_model_dir),
    })
}

#[cfg(mobile)]
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
