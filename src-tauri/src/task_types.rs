//! Platform-independent task contracts used by the Rust engine.
//!
//! Protobuf and Tauri are transport concerns. The task engine receives these
//! strongly typed values so malformed options cannot reach task code.

use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskType {
    ReformatEpub,
    DecryptEpub,
    EncryptEpub,
    EncryptFont,
    DecryptFont,
    WebpToImg,
    ImageCompress,
    ImageToWebp,
    ChineseConvert,
    ReplaceCover,
}

impl TaskType {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ReformatEpub => "reformat_epub",
            Self::DecryptEpub => "decrypt_epub",
            Self::EncryptEpub => "encrypt_epub",
            Self::EncryptFont => "encrypt_font",
            Self::DecryptFont => "decrypt_font",
            Self::WebpToImg => "webp_to_img",
            Self::ImageCompress => "image_compress",
            Self::ImageToWebp => "image_to_webp",
            Self::ChineseConvert => "chinese_convert",
            Self::ReplaceCover => "replace_cover",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct FontTaskOptions {
    #[serde(default)]
    pub target_font_families_by_file: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub target_font_families: Vec<String>,
    #[serde(default)]
    pub ocr_char_policy: Option<OcrCharPolicy>,
    #[serde(default)]
    pub min_ocr_confidence: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OcrCharPolicy {
    Strict,
    Compatible,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ChineseConversionDirection {
    #[serde(rename = "s2t")]
    SimplifiedToTraditional,
    #[serde(rename = "t2s")]
    TraditionalToSimplified,
}

impl ChineseConversionDirection {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::SimplifiedToTraditional => "s2t",
            Self::TraditionalToSimplified => "t2s",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ImageTaskOptions {
    pub quality: Option<u8>,
    pub jpeg_quality: Option<u8>,
    pub webp_quality: Option<u8>,
    pub png_to_jpg: Option<bool>,
    pub png_quantize: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ReplaceCoverOptions {
    #[serde(default)]
    pub cover_path_by_file: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum TaskOptions {
    #[default]
    Empty,
    Font(FontTaskOptions),
    Image(ImageTaskOptions),
    ChineseConvert {
        direction: Option<ChineseConversionDirection>,
    },
    ReplaceCover(ReplaceCoverOptions),
}

impl TaskOptions {
    pub fn font(&self) -> Option<&FontTaskOptions> {
        match self {
            Self::Font(options) => Some(options),
            _ => None,
        }
    }

    pub fn image(&self) -> Option<&ImageTaskOptions> {
        match self {
            Self::Image(options) => Some(options),
            _ => None,
        }
    }

    pub fn chinese_direction(&self) -> Option<ChineseConversionDirection> {
        match self {
            Self::ChineseConvert { direction } => *direction,
            _ => None,
        }
    }

    pub fn replace_cover(&self) -> Option<&ReplaceCoverOptions> {
        match self {
            Self::ReplaceCover(options) => Some(options),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskSpec {
    pub task_id: String,
    pub task_type: TaskType,
    pub input_files: Vec<PathBuf>,
    pub output_dir: Option<PathBuf>,
    pub options: TaskOptions,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FileIssue {
    pub input_file: String,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskSummary {
    pub total: usize,
    pub success: usize,
    pub failed: usize,
    pub skipped: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskResult {
    pub ok: bool,
    pub status: String,
    pub outputs: Vec<String>,
    pub errors: Vec<FileIssue>,
    pub skipped: Vec<FileIssue>,
    pub summary: TaskSummary,
    pub log_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskEvent {
    pub event: String,
    pub task_id: String,
    pub status: String,
    pub progress: f64,
    pub message: String,
    pub current_file: Option<String>,
    pub current_index: Option<usize>,
    pub total_files: Option<usize>,
    pub output_path: Option<String>,
    pub level: Option<String>,
    pub result: Option<TaskResult>,
}
