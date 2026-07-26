//! OCR building blocks for `decrypt_font`.
//!
//! This module keeps the renderer and ONNX runtime independent from EPUB
//! rewrite policy so each stage can be compared against the Python sidecar.
//! Production dispatch remains Python-backed until the EPUB-level regression
//! suite covers a complete strict subset.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use super::{
    encrypt_font::FontEncryptionPlan,
    font_cmap::unicode_cmap,
    font_obfuscation::split_obfuscation_text,
};
use crate::rust_backend::{epub::EpubWorkspace, EpubTask, TaskOutcome};
use image::{imageops::FilterType, DynamicImage, Rgb, RgbImage};
use ort::{session::Session, value::TensorRef};
use regex::Regex;
use serde_json::Value;

const OCR_PERIOD_ALIASES: [char; 3] = ['.', '．', '｡'];
const OCR_HANGUL_OBFUSCATION_START: u32 = 0xAC00;
const OCR_HANGUL_OBFUSCATION_END: u32 = 0xD7AF;
pub const DEFAULT_OCR_MAX_IMAGE_WIDTH: usize = 3200;
const DEFAULT_MIN_OCR_CONFIDENCE: f32 = 0.8;

pub struct DecryptFontTask;

impl EpubTask for DecryptFontTask {
    fn task_type(&self) -> &'static str {
        "decrypt_font"
    }

    fn supports_options(&self, options: &Value) -> bool {
        let Some(values) = options.as_object() else {
            return false;
        };
        if !values.keys().all(|key| {
            matches!(
                key.as_str(),
                "target_font_families_by_file" | "min_ocr_confidence" | "ocr_char_policy"
            )
        }) {
            return false;
        }
        values
            .get("target_font_families_by_file")
            .is_none_or(Value::is_object)
            && values
                .get("min_ocr_confidence")
                .and_then(Value::as_f64)
                .is_none_or(|value| (0.0..=1.0).contains(&value))
            && values
                .get("ocr_char_policy")
                .and_then(Value::as_str)
                .is_none_or(|value| value == "strict")
    }

    fn supports_input(&self, input: &Path, options: &Value) -> bool {
        configured_ocr_resources().is_some()
            && EpubWorkspace::load(input, |_| {})
                .and_then(|workspace| {
                    FontEncryptionPlan::build_for_decryption(&workspace, input, options)
                })
                .is_ok()
    }

    fn process(
        &self,
        input: &Path,
        workspace: &mut EpubWorkspace,
        options: &Value,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        let plan = FontEncryptionPlan::build_for_decryption(workspace, input, options)?;
        if plan.target_fonts().is_empty() {
            return Ok(TaskOutcome::Skip);
        }
        let resources = configured_ocr_resources().ok_or_else(|| {
            "未找到 Rust OCR 所需的 ONNX Runtime 或 PP-OCR 模型资源".to_string()
        })?;
        let font_data_by_path = plan
            .target_fonts()
            .iter()
            .map(|path| {
                workspace
                    .members
                    .get(path)
                    .cloned()
                    .map(|data| (path.clone(), data))
                    .ok_or_else(|| format!("EPUB 缺少目标字体: {path}"))
            })
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        let text_by_font = plan.collect_target_text(workspace)?;
        let minimum_confidence = options
            .get("min_ocr_confidence")
            .and_then(Value::as_f64)
            .map(|value| value as f32)
            .unwrap_or(DEFAULT_MIN_OCR_CONFIDENCE);
        let replacements = build_strict_ocr_replacements(
            &font_data_by_path,
            &text_by_font,
            &resources,
            minimum_confidence,
        )?;
        let processed_fonts: BTreeSet<_> = replacements
            .iter()
            .filter(|(_, table)| !table.is_empty())
            .map(|(path, _)| path.clone())
            .collect();
        if processed_fonts.is_empty() {
            return Ok(TaskOutcome::Skip);
        }

        for member in plan.xhtml_members() {
            let source = std::str::from_utf8(
                workspace
                    .members
                    .get(member)
                    .ok_or_else(|| format!("EPUB 缺少 XHTML 文件: {member}"))?,
            )
            .map_err(|_| format!("XHTML 不是 UTF-8: {member}"))?;
            let rewritten = plan.rewrite_xhtml(source, &replacements)?;
            workspace.members.insert(member.clone(), rewritten.into_bytes());
        }
        for (member, data) in workspace.members.clone() {
            if member.to_ascii_lowercase().ends_with(".css") {
                let css = std::str::from_utf8(&data)
                    .map_err(|_| format!("CSS 不是 UTF-8: {member}"))?;
                let cleaned = clean_strict_css_font_references(css, plan.target_families())?;
                workspace.members.insert(member, cleaned.into_bytes());
            }
        }
        let opf_path = workspace.opf_path.clone();
        let opf = std::str::from_utf8(
            workspace
                .members
                .get(&opf_path)
                .ok_or_else(|| format!("EPUB 缺少 OPF 文件: {opf_path}"))?,
        )
        .map_err(|_| format!("OPF 不是 UTF-8: {opf_path}"))?;
        workspace.members.insert(
            opf_path.clone(),
            clean_strict_opf_font_manifest(opf, &opf_target_hrefs(&opf_path, &processed_fonts))
                .into_bytes(),
        );
        for font_path in &processed_fonts {
            workspace.members.remove(font_path);
        }
        log(format!(
            "Rust 字体 OCR 解密完成：处理 {} 个字体、{} 个 XHTML 文件。",
            processed_fonts.len(),
            plan.xhtml_members().len()
        ));
        Ok(TaskOutcome::Success)
    }
}

fn opf_target_hrefs(opf_path: &str, font_paths: &BTreeSet<String>) -> BTreeSet<String> {
    let directory = opf_path.rsplit_once('/').map_or("", |(directory, _)| directory);
    font_paths
        .iter()
        .map(|path| {
            (!directory.is_empty())
                .then(|| format!("{directory}/"))
                .and_then(|prefix| path.strip_prefix(&prefix))
                .unwrap_or(path)
                .to_string()
        })
        .collect()
}

/// Application-owned locations for the bundled OCR model and the ONNX Runtime
/// carried by the Python sidecar. They are never accepted from frontend task
/// options, so a task cannot load an arbitrary dynamic library.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OcrResourcePaths {
    pub runtime_path: PathBuf,
    pub model_dir: PathBuf,
}

static OCR_RESOURCE_PATHS: OnceLock<OcrResourcePaths> = OnceLock::new();

pub fn configure_ocr_resources(resources: OcrResourcePaths) -> Result<(), String> {
    if !resources.runtime_path.is_file() {
        return Err(format!(
            "ONNX Runtime 不存在: {}",
            resources.runtime_path.display()
        ));
    }
    if !resources.model_dir.join("inference.onnx").is_file()
        || !resources.model_dir.join("inference.yml").is_file()
    {
        return Err(format!(
            "OCR 模型资源不完整: {}",
            resources.model_dir.display()
        ));
    }
    match OCR_RESOURCE_PATHS.set(resources.clone()) {
        Ok(()) => Ok(()),
        Err(existing) if existing == resources => Ok(()),
        Err(existing) => Err(format!(
            "ONNX Runtime 已使用不同资源初始化: {}",
            existing.runtime_path.display()
        )),
    }
}

pub fn configured_ocr_resources() -> Option<OcrResourcePaths> {
    OCR_RESOURCE_PATHS.get().cloned().or_else(dev_ocr_resources)
}

fn dev_ocr_resources() -> Option<OcrResourcePaths> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent()?;
    let model_dir = root
        .join("src-tauri")
        .join("bundle-resources")
        .join("ocr-models")
        .join("PP-OCRv6_small_rec_onnx");
    let runtime_dir = root
        .join("src-tauri")
        .join("binaries")
        .join("epub-tool-python")
        .join("_internal")
        .join("onnxruntime")
        .join("capi");
    let runtime_path = fs::read_dir(runtime_dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .find(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| {
                    name.starts_with("libonnxruntime")
                        || name.eq_ignore_ascii_case("onnxruntime.dll")
                })
        })?;
    (runtime_path.is_file()
        && model_dir.join("inference.onnx").is_file()
        && model_dir.join("inference.yml").is_file())
    .then_some(OcrResourcePaths {
        runtime_path,
        model_dir,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct OcrTextResult {
    pub text: String,
    pub confidence: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OcrImageTensor {
    pub data: Vec<f32>,
    pub channels: usize,
    pub height: usize,
    pub width: usize,
}

/// Model output reduced to the values consumed by the Python CTC decoder.
/// Keeping this representation independent from `ort` makes parity tests
/// compare the runtime output directly without changing the task protocol.
#[derive(Debug, Clone, PartialEq)]
pub struct OcrCtcPrediction {
    pub shape: Vec<usize>,
    pub token_ids: Vec<usize>,
    pub scores: Vec<f32>,
}

/// The portion of PaddleOCR's bundled `inference.yml` that affects OCR
/// inference.  It intentionally does not try to be a general YAML parser:
/// accepting arbitrary YAML here would make the Rust/Python parity contract
/// much harder to audit.  The parser supports the checked-in PaddleOCR model
/// format and rejects malformed or incomplete files.
#[derive(Debug, Clone, PartialEq)]
pub struct OcrModelConfig {
    pub image_shape: [usize; 3],
    pub image_mode: String,
    /// Includes the CTC blank token at index zero and the trailing space
    /// token, exactly as Python's `OnnxGlyphOcrBackend` builds it.
    pub characters: Vec<String>,
}

/// Reusable ONNX OCR session for one fixed bundled model.
///
/// `ort` keeps its dynamic runtime process-global.  The application resolves
/// the runtime once from its bundled Python sidecar, then one instance of this
/// backend can recognize every glyph in a book without reloading the model.
pub struct OnnxGlyphOcrBackend {
    session: Session,
    pub config: OcrModelConfig,
    max_image_width: usize,
}

/// Rasterizes a single glyph before it is sent to PaddleOCR.  The production
/// task will only use this for embedded TrueType fonts; unsupported outlines
/// deliberately cause a Python-sidecar fallback rather than emitting a
/// visually different EPUB.
pub struct FontGlyphRenderer {
    font: fontdue::Font,
    font_size: f32,
    padding: u32,
    small_glyph_font_size: f32,
    small_glyph_padding: u32,
    small_glyph_threshold: f32,
}

impl FontGlyphRenderer {
    pub fn new(font_bytes: &[u8]) -> Result<Self, String> {
        let font = fontdue::Font::from_bytes(font_bytes, fontdue::FontSettings::default())
            .map_err(|error| format!("Rust OCR 暂不支持该字体: {error}"))?;
        Ok(Self {
            font,
            font_size: 128.0,
            padding: 32,
            small_glyph_font_size: 256.0,
            small_glyph_padding: 16,
            small_glyph_threshold: 0.42,
        })
    }

    pub fn render(&self, character: char) -> Result<DynamicImage, String> {
        let metrics = self.font.metrics(character, self.font_size);
        let threshold = self.font_size * self.small_glyph_threshold;
        let use_large_glyph =
            metrics.width as f32 <= threshold || metrics.height as f32 <= threshold;
        let (font_size, padding) = if use_large_glyph {
            (self.small_glyph_font_size, self.small_glyph_padding)
        } else {
            (self.font_size, self.padding)
        };
        let (metrics, coverage) = self.font.rasterize(character, font_size);
        if metrics.width == 0 || metrics.height == 0 {
            return Err(format!("字体没有可渲染的字形: U+{:04X}", character as u32));
        }
        let width = u32::try_from(metrics.width)
            .map_err(|_| "字体字形宽度超出范围".to_string())?
            .checked_add(padding.saturating_mul(2))
            .ok_or_else(|| "字体字形宽度超出范围".to_string())?;
        let height = u32::try_from(metrics.height)
            .map_err(|_| "字体字形高度超出范围".to_string())?
            .checked_add(padding.saturating_mul(2))
            .ok_or_else(|| "字体字形高度超出范围".to_string())?;
        let mut image = RgbImage::from_pixel(width, height, Rgb([255, 255, 255]));
        for (index, alpha) in coverage.iter().copied().enumerate() {
            let x = u32::try_from(index % metrics.width)
                .map_err(|_| "字体字形像素坐标超出范围".to_string())?;
            let y = u32::try_from(index / metrics.width)
                .map_err(|_| "字体字形像素坐标超出范围".to_string())?;
            let value = 255_u8.saturating_sub(alpha);
            image.put_pixel(x + padding, y + padding, Rgb([value, value, value]));
        }
        Ok(DynamicImage::ImageRgb8(image))
    }
}

pub fn is_period_like_image(image: &DynamicImage) -> bool {
    let image = image.to_luma8();
    let mut min_x = image.width();
    let mut min_y = image.height();
    let mut max_x = 0;
    let mut max_y = 0;
    let mut has_ink = false;
    for (x, y, pixel) in image.enumerate_pixels() {
        if pixel.0[0] >= 250 {
            continue;
        }
        has_ink = true;
        min_x = min_x.min(x);
        min_y = min_y.min(y);
        max_x = max_x.max(x);
        max_y = max_y.max(y);
    }
    if !has_ink {
        return false;
    }
    let ink_width = max_x - min_x + 1;
    let ink_height = max_y - min_y + 1;
    let aspect_ratio = ink_width as f32 / ink_height as f32;
    ink_width as f32 <= image.width() as f32 * 0.38
        && ink_height as f32 <= image.height() as f32 * 0.46
        && (0.55..=1.6).contains(&aspect_ratio)
}

/// Builds the complete per-font replacement tables needed by EPUB rewriting.
/// The caller must not use a partial result: any unrenderable glyph, missing
/// cmap entry, low-confidence prediction or multi-character OCR result means
/// the entire EPUB must remain on the Python compatibility backend.
pub fn build_strict_ocr_replacements(
    font_data_by_path: &BTreeMap<String, Vec<u8>>,
    text_by_font: &BTreeMap<String, String>,
    resources: &OcrResourcePaths,
    minimum_confidence: f32,
) -> Result<BTreeMap<String, BTreeMap<char, char>>, String> {
    if !(0.0..=1.0).contains(&minimum_confidence) {
        return Err("OCR 最低置信度必须在 0 到 1 之间".to_string());
    }
    let mut backend = OnnxGlyphOcrBackend::from_model_dir(
        &resources.runtime_path,
        &resources.model_dir,
        DEFAULT_OCR_MAX_IMAGE_WIDTH,
    )?;
    let mut tables = BTreeMap::new();
    for (font_path, font_data) in font_data_by_path {
        let cmap = unicode_cmap(font_data)
            .map_err(|error| format!("读取待解密字体 cmap 失败 {font_path}: {error}"))?;
        let text = text_by_font.get(font_path).map_or("", String::as_str);
        let candidates = split_obfuscation_text(text).obfuscate;
        let renderer = FontGlyphRenderer::new(font_data)?;
        let mut replacements = BTreeMap::new();
        for character in candidates.chars() {
            if !cmap.contains_key(&(character as u32)) {
                continue;
            }
            let image = renderer.render(character)?;
            let result = backend.recognize_image(&image)?;
            let normalized =
                normalize_ocr_text(&result.text, Some(character), is_period_like_image(&image));
            let mut decoded = normalized.chars();
            let replacement = decoded
                .next()
                .filter(|_| decoded.next().is_none())
                .ok_or_else(|| {
                    format!(
                        "字体 OCR 未得到单字结果 {font_path} U+{:04X}: {}",
                        character as u32, result.text
                    )
                })?;
            if result.confidence < minimum_confidence {
                return Err(format!(
                    "字体 OCR 置信度不足 {font_path} U+{:04X}: {:.4} < {:.4}",
                    character as u32, result.confidence, minimum_confidence
                ));
            }
            replacements.insert(character, replacement);
        }
        tables.insert(font_path.clone(), replacements);
    }
    Ok(tables)
}

/// Removes only the simple font references accepted by the native font plan.
/// The caller supplies normalized family names and applies the returned text
/// only after every OCR replacement has succeeded.
pub fn clean_strict_css_font_references(
    css: &str,
    target_families: &std::collections::BTreeSet<String>,
) -> Result<String, String> {
    if css.to_ascii_lowercase().contains("font:") || css.to_ascii_lowercase().contains("all:") {
        return Err("font/all 简写清理需使用 Python 兼容实现".to_string());
    }
    let face = Regex::new(r"(?is)\s*@font-face\s*\{[^{}]*\}").expect("literal regex");
    let family = Regex::new(r"(?is)font-family\s*:\s*([^;{}]+);?").expect("literal regex");
    let without_faces = face.replace_all(css, |captures: &regex::Captures<'_>| {
        family
            .captures(&captures[0])
            .and_then(|family_match| family_match.get(1))
            .map(|family_match| {
                family_match
                    .as_str()
                    .trim_matches(['\'', '"'])
                    .to_ascii_lowercase()
            })
            .is_some_and(|name| target_families.contains(&name))
            .then(String::new)
            .unwrap_or_else(|| captures[0].to_string())
    });
    Ok(family
        .replace_all(&without_faces, |captures: &regex::Captures<'_>| {
            let kept: Vec<_> = captures[1]
                .split(',')
                .map(str::trim)
                .filter(|item| {
                    let normalized = item.trim_matches(['\'', '"']).to_ascii_lowercase();
                    !target_families.contains(&normalized)
                })
                .collect();
            if kept.is_empty() {
                String::new()
            } else {
                format!("font-family: {};", kept.join(", "))
            }
        })
        .into_owned())
}

pub fn clean_strict_opf_font_manifest(
    opf: &str,
    target_hrefs: &std::collections::BTreeSet<String>,
) -> String {
    let item = Regex::new(r"(?is)\s*<item\b([^>]*)/?>").expect("literal regex");
    let href = Regex::new(r#"(?is)\bhref\s*=\s*['\"]([^'\"]*)['\"]"#).expect("literal regex");
    item.replace_all(opf, |captures: &regex::Captures<'_>| {
        href.captures(&captures[1])
            .and_then(|href| href.get(1))
            .is_some_and(|href| target_hrefs.contains(href.as_str()))
            .then(String::new)
            .unwrap_or_else(|| captures[0].to_string())
    })
    .into_owned()
}

impl OnnxGlyphOcrBackend {
    pub fn from_model_dir(
        runtime_path: &Path,
        model_dir: &Path,
        max_image_width: usize,
    ) -> Result<Self, String> {
        let model_path = model_dir.join("inference.onnx");
        let config_path = model_dir.join("inference.yml");
        let config = load_ocr_model_config(&config_path)?;
        Self::new(runtime_path, &model_path, config, max_image_width)
    }

    pub fn new(
        runtime_path: &Path,
        model_path: &Path,
        config: OcrModelConfig,
        max_image_width: usize,
    ) -> Result<Self, String> {
        if max_image_width == 0 {
            return Err("OCR 最大图像宽度必须大于零".to_string());
        }
        initialize_onnx_runtime(runtime_path)?;
        let session = Session::builder()
            .map_err(|error| format!("创建 ONNX OCR Session 失败: {error}"))?
            .commit_from_file(model_path)
            .map_err(|error| format!("加载 ONNX OCR 模型失败 {}: {error}", model_path.display()))?;
        Ok(Self {
            session,
            config,
            max_image_width,
        })
    }

    pub fn recognize_image(&mut self, image: &DynamicImage) -> Result<OcrTextResult, String> {
        let tensor = preprocess_ocr_image(
            image,
            self.config.image_shape,
            &self.config.image_mode,
            self.max_image_width,
        )?;
        self.recognize_tensor(&tensor)
    }

    pub fn recognize_tensor(&mut self, tensor: &OcrImageTensor) -> Result<OcrTextResult, String> {
        let prediction = run_onnx_ctc_session(&mut self.session, tensor)?;
        Ok(decode_ctc_argmax(
            &prediction.token_ids,
            &prediction.scores,
            &self.config.characters,
        ))
    }
}

pub fn load_ocr_model_config(path: &Path) -> Result<OcrModelConfig, String> {
    let source = fs::read_to_string(path)
        .map_err(|error| format!("读取 OCR 模型配置失败 {}: {error}", path.display()))?;
    parse_ocr_model_config(&source)
        .map_err(|error| format!("OCR 模型配置无效 {}: {error}", path.display()))
}

pub fn parse_ocr_model_config(source: &str) -> Result<OcrModelConfig, String> {
    let lines: Vec<_> = source.lines().collect();
    let image_mode = scalar_after_key(&lines, "img_mode")
        .ok_or_else(|| "缺少 DecodeImage.img_mode".to_string())?;
    let image_shape = integer_list_after_key(&lines, "image_shape")
        .ok_or_else(|| "缺少 RecResizeImg.image_shape".to_string())?;
    let image_shape: [usize; 3] = image_shape
        .try_into()
        .map_err(|_| "RecResizeImg.image_shape 必须有 3 个整数".to_string())?;
    let dictionary = yaml_list_after_key(&lines, "character_dict")
        .ok_or_else(|| "缺少 PostProcess.character_dict".to_string())?;
    if dictionary.is_empty() {
        return Err("PostProcess.character_dict 不能为空".to_string());
    }
    let mut characters = Vec::with_capacity(dictionary.len() + 2);
    characters.push("blank".to_string());
    characters.extend(dictionary);
    characters.push(" ".to_string());
    Ok(OcrModelConfig {
        image_shape,
        image_mode,
        characters,
    })
}

fn scalar_after_key(lines: &[&str], key: &str) -> Option<String> {
    let prefix = format!("{key}:");
    lines.iter().find_map(|line| {
        let trimmed = line.trim_start();
        trimmed
            .strip_prefix(&prefix)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(parse_yaml_scalar)
    })
}

fn integer_list_after_key(lines: &[&str], key: &str) -> Option<Vec<usize>> {
    yaml_list_after_key(lines, key).and_then(|values| {
        values
            .into_iter()
            .map(|value| value.parse::<usize>().ok())
            .collect()
    })
}

fn yaml_list_after_key(lines: &[&str], key: &str) -> Option<Vec<String>> {
    let prefix = format!("{key}:");
    let start = lines.iter().position(|line| line.trim_start() == prefix)?;
    let indentation = lines[start].len() - lines[start].trim_start().len();
    let mut values = Vec::new();
    for line in &lines[start + 1..] {
        if line.trim().is_empty() {
            continue;
        }
        let current_indentation = line.len() - line.trim_start().len();
        let trimmed = line.trim_start();
        if current_indentation < indentation {
            break;
        }
        let Some(value) = trimmed.strip_prefix("- ") else {
            if current_indentation == indentation {
                break;
            }
            continue;
        };
        values.push(parse_yaml_scalar(value.trim()));
    }
    Some(values)
}

fn parse_yaml_scalar(value: &str) -> String {
    if let Some(value) = value
        .strip_prefix('\'')
        .and_then(|value| value.strip_suffix('\''))
    {
        return value.replace("''", "'");
    }
    if let Some(value) = value
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
    {
        return value.replace("\\\\", "\\").replace("\\\"", "\"");
    }
    value.to_string()
}

fn initialize_onnx_runtime(runtime_path: &Path) -> Result<(), String> {
    ort::init_from(runtime_path)
        .map_err(|error| format!("加载 ONNX Runtime 失败 {}: {error}", runtime_path.display()))?
        .commit();
    Ok(())
}

/// Runs one OCR tensor through ONNX Runtime loaded from an explicit bundled
/// dynamic library. The caller owns runtime-path resolution so this library
/// code remains usable both by Tauri and the standalone regression runner.
pub fn infer_onnx_ctc(
    runtime_path: &Path,
    model_path: &Path,
    tensor: &OcrImageTensor,
) -> Result<OcrCtcPrediction, String> {
    initialize_onnx_runtime(runtime_path)?;
    let mut session = Session::builder()
        .map_err(|error| format!("创建 ONNX OCR Session 失败: {error}"))?
        .commit_from_file(model_path)
        .map_err(|error| format!("加载 ONNX OCR 模型失败 {}: {error}", model_path.display()))?;
    run_onnx_ctc_session(&mut session, tensor)
}

fn run_onnx_ctc_session(
    session: &mut Session,
    tensor: &OcrImageTensor,
) -> Result<OcrCtcPrediction, String> {
    let input = ndarray::Array4::from_shape_vec(
        (1, tensor.channels, tensor.height, tensor.width),
        tensor.data.clone(),
    )
    .map_err(|error| format!("构建 OCR 输入张量失败: {error}"))?;
    let output = session
        .run(ort::inputs![TensorRef::from_array_view(&input).map_err(
            |error| format!("创建 ONNX OCR 输入失败: {error}")
        )?])
        .map_err(|error| format!("运行 ONNX OCR 推理失败: {error}"))?;
    let output = &output[0];
    let (shape, values) = output
        .try_extract_tensor::<f32>()
        .map_err(|error| format!("读取 ONNX OCR 输出失败: {error}"))?;
    let shape = shape
        .iter()
        .map(|value| *value as usize)
        .collect::<Vec<_>>();
    if shape.len() != 3 || shape[0] != 1 || shape[2] == 0 {
        return Err(format!("ONNX OCR 输出形状无效: {shape:?}"));
    }
    let vocab = shape[2];
    let steps = shape[1];
    if values.len() != steps * vocab {
        return Err(format!("ONNX OCR 输出长度与形状不一致: {}", values.len()));
    }
    let mut token_ids = Vec::with_capacity(steps);
    let mut scores = Vec::with_capacity(steps);
    for timestep in values.chunks_exact(vocab) {
        let (token_id, score) = timestep
            .iter()
            .enumerate()
            .max_by(|(_, left), (_, right)| left.total_cmp(right))
            .ok_or_else(|| "ONNX OCR 输出 timestep 为空".to_string())?;
        token_ids.push(token_id);
        scores.push(*score);
    }
    Ok(OcrCtcPrediction {
        shape,
        token_ids,
        scores,
    })
}

fn decode_ctc_argmax(token_ids: &[usize], scores: &[f32], characters: &[String]) -> OcrTextResult {
    let mut text = String::new();
    let mut selected_scores = Vec::new();
    let mut previous = None;
    for (&token_id, &score) in token_ids.iter().zip(scores) {
        if token_id == 0 || previous == Some(token_id) {
            previous = Some(token_id);
            continue;
        }
        previous = Some(token_id);
        if let Some(character) = characters.get(token_id) {
            text.push_str(character);
            selected_scores.push(score);
        }
    }
    let confidence = (!selected_scores.is_empty())
        .then(|| selected_scores.iter().sum::<f32>() / selected_scores.len() as f32)
        .unwrap_or(0.0);
    OcrTextResult { text, confidence }
}

/// Decodes the first batch item of a CTC recognition output.
///
/// `prediction` is indexed as `[time_step][token_id]`, where token zero is
/// the CTC blank. It mirrors Python's `OnnxGlyphOcrBackend.decode_prediction`:
/// repeated non-blank tokens are collapsed, blank tokens reset repetition, and
/// confidence is the mean selected-token score.
pub fn decode_ctc_prediction(prediction: &[Vec<f32>], characters: &[String]) -> OcrTextResult {
    let mut text = String::new();
    let mut scores = Vec::new();
    let mut previous = None;
    for timestep in prediction {
        let Some((token_id, score)) = timestep
            .iter()
            .enumerate()
            .max_by(|(_, left), (_, right)| left.total_cmp(right))
        else {
            continue;
        };
        if token_id == 0 || previous == Some(token_id) {
            previous = Some(token_id);
            continue;
        }
        previous = Some(token_id);
        if let Some(character) = characters.get(token_id) {
            text.push_str(character);
            scores.push(*score);
        }
    }
    let confidence = (!scores.is_empty())
        .then(|| scores.iter().sum::<f32>() / scores.len() as f32)
        .unwrap_or(0.0);
    OcrTextResult { text, confidence }
}

/// Mirrors Python `OnnxGlyphOcrBackend.preprocess_image` without requiring an
/// ONNX Runtime. The result is NCHW data for a batch of one, normalized from
/// RGB pixels by `(pixel / 255 - 0.5) / 0.5` and padded with zeros on the
/// right.
pub fn preprocess_ocr_image(
    image: &DynamicImage,
    image_shape: [usize; 3],
    image_mode: &str,
    max_image_width: usize,
) -> Result<OcrImageTensor, String> {
    let [channels, target_height, configured_width] = image_shape;
    if channels != 3 {
        return Err(format!("暂不支持非 3 通道 OCR 输入: {image_shape:?}"));
    }
    if target_height == 0 || configured_width == 0 || max_image_width == 0 {
        return Err(format!("OCR 输入形状无效: {image_shape:?}"));
    }
    let rgb = image.to_rgb8();
    let (width, height) = rgb.dimensions();
    if width == 0 || height == 0 {
        return Err(format!("OCR 输入图像尺寸无效: {width}x{height}"));
    }
    let ratio = width as f64 / height as f64;
    let max_wh_ratio = (configured_width as f64 / target_height as f64).max(ratio);
    let target_width = ((target_height as f64 * max_wh_ratio) as usize).min(max_image_width);
    let resized_width = round_half_even(target_height as f64 * ratio)
        .max(1)
        .min(target_width);
    let resized = image::imageops::resize(
        &rgb,
        resized_width as u32,
        target_height as u32,
        FilterType::Triangle,
    );
    let mut data = vec![0.0_f32; channels * target_height * target_width];
    let bgr = image_mode.eq_ignore_ascii_case("BGR");
    for y in 0..target_height {
        for x in 0..resized_width {
            let pixel = resized.get_pixel(x as u32, y as u32).0;
            for channel in 0..channels {
                let source_channel = if bgr { channels - 1 - channel } else { channel };
                let offset = channel * target_height * target_width + y * target_width + x;
                data[offset] = pixel[source_channel] as f32 / 127.5 - 1.0;
            }
        }
    }
    Ok(OcrImageTensor {
        data,
        channels,
        height: target_height,
        width: target_width,
    })
}

fn round_half_even(value: f64) -> usize {
    let floor = value.floor();
    let fraction = value - floor;
    if (fraction - 0.5).abs() < f64::EPSILON {
        let integer = floor as usize;
        if integer.is_multiple_of(2) {
            integer
        } else {
            integer + 1
        }
    } else {
        value.round() as usize
    }
}

pub fn format_ocr_progress(processed_count: usize, total_count: usize) -> String {
    if total_count == 0 {
        return String::new();
    }
    format!(
        "，进度 {processed_count}/{total_count} ({:.1}%)",
        processed_count as f64 / total_count as f64 * 100.0
    )
}

pub fn is_ocr_obfuscation_hint_char(character: char) -> bool {
    let codepoint = character as u32;
    matches!(character, '\u{E000}'..='\u{F8FF}')
        || (OCR_HANGUL_OBFUSCATION_START..=OCR_HANGUL_OBFUSCATION_END).contains(&codepoint)
}

pub fn normalize_ocr_text(text: &str, hint_char: Option<char>, period_like_glyph: bool) -> String {
    let normalized: String = text
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    if normalized.chars().count() != 1 {
        return normalized;
    }
    let Some(hint_char) = hint_char else {
        return normalized;
    };
    if !is_ocr_obfuscation_hint_char(hint_char) {
        return normalized;
    }
    let character = normalized
        .chars()
        .next()
        .expect("one character checked above");
    if OCR_PERIOD_ALIASES.contains(&character) || (character == '0' && period_like_glyph) {
        "。".to_string()
    } else {
        normalized
    }
}

pub fn filter_text_by_cmap(text: &str, cmap: Option<&BTreeMap<u32, u16>>) -> String {
    let Some(cmap) = cmap else {
        return text.to_string();
    };
    text.chars()
        .filter(|character| cmap.contains_key(&(*character as u32)))
        .collect()
}

pub fn format_ocr_failure_placeholder(character: char, status_code: &str) -> String {
    format!("[U+{:04X} {status_code}]", character as u32)
}

#[cfg(test)]
mod tests {
    use super::{
        clean_strict_css_font_references, clean_strict_opf_font_manifest, decode_ctc_prediction,
        filter_text_by_cmap, format_ocr_failure_placeholder, format_ocr_progress,
        is_ocr_obfuscation_hint_char, is_period_like_image, normalize_ocr_text,
        parse_ocr_model_config, preprocess_ocr_image,
    };
    use image::{DynamicImage, Rgb, RgbImage};
    use std::collections::BTreeMap;
    use std::collections::BTreeSet;

    #[test]
    fn normalizes_period_aliases_only_for_obfuscated_hints() {
        assert_eq!(normalize_ocr_text("．", Some('\u{E000}'), false), "。");
        assert_eq!(normalize_ocr_text("0", Some('\u{D73C}'), true), "。");
        assert_eq!(normalize_ocr_text("0", Some('０'), true), "0");
        assert_eq!(normalize_ocr_text(" 你 ", Some('\u{E000}'), false), "你");
        assert!(is_ocr_obfuscation_hint_char('\u{D73C}'));
    }

    #[test]
    fn filters_only_characters_present_in_font_cmap() {
        let cmap = BTreeMap::from([(u32::from('你'), 1_u16), (u32::from('A'), 2_u16)]);
        assert_eq!(filter_text_by_cmap("你缺A", Some(&cmap)), "你A");
        assert_eq!(filter_text_by_cmap("你缺A", None), "你缺A");
    }

    #[test]
    fn formats_failure_placeholders_like_python() {
        assert_eq!(
            format_ocr_failure_placeholder('\u{E000}', "OCR_LOW_CONF"),
            "[U+E000 OCR_LOW_CONF]"
        );
    }

    #[test]
    fn decodes_ctc_output_like_python_onnx_backend() {
        let characters = vec![
            "blank".to_string(),
            "你".to_string(),
            "好".to_string(),
            " ".to_string(),
        ];
        let prediction = vec![
            vec![0.0, 0.9, 0.0, 0.0],
            vec![0.0, 0.8, 0.0, 0.0],
            vec![0.7, 0.0, 0.0, 0.0],
            vec![0.0, 0.0, 0.95, 0.0],
            vec![0.6, 0.0, 0.0, 0.0],
        ];
        let result = decode_ctc_prediction(&prediction, &characters);
        assert_eq!(result.text, "你好");
        assert!((result.confidence - 0.925).abs() < f32::EPSILON);
    }

    #[test]
    fn parses_bundled_paddleocr_config_like_python_backend() {
        let config = parse_ocr_model_config(include_str!(
            "../../../bundle-resources/ocr-models/PP-OCRv6_small_rec_onnx/inference.yml"
        ))
        .expect("bundled config");
        assert_eq!(config.image_shape, [3, 48, 320]);
        assert_eq!(config.image_mode, "BGR");
        assert_eq!(config.characters.first().map(String::as_str), Some("blank"));
        assert_eq!(config.characters.get(1).map(String::as_str), Some("!"));
        assert_eq!(config.characters.last().map(String::as_str), Some(" "));
        // The ONNX output's final dimension is 18,710 for this model.
        assert_eq!(config.characters.len(), 18_710);
    }

    #[test]
    fn detects_period_like_glyphs_with_python_thresholds() {
        let mut period = RgbImage::from_pixel(100, 100, Rgb([255, 255, 255]));
        for y in 75..85 {
            for x in 45..55 {
                period.put_pixel(x, y, Rgb([0, 0, 0]));
            }
        }
        assert!(is_period_like_image(&DynamicImage::ImageRgb8(period)));

        let mut zero = RgbImage::from_pixel(100, 100, Rgb([255, 255, 255]));
        for y in 20..80 {
            for x in 30..70 {
                if x < 36 || x >= 64 || y < 26 || y >= 74 {
                    zero.put_pixel(x, y, Rgb([0, 0, 0]));
                }
            }
        }
        assert!(!is_period_like_image(&DynamicImage::ImageRgb8(zero)));
    }

    #[test]
    fn cleans_only_strict_font_references_and_preserves_fallback_family() {
        let targets = BTreeSet::from(["obf".to_string()]);
        let css = "@font-face { font-family: Obf; src: url(font.ttf); }\n.body { font-family: Obf, serif; }";
        assert_eq!(
            clean_strict_css_font_references(css, &targets).expect("clean CSS"),
            "\n.body { font-family: serif; }"
        );
        assert!(clean_strict_css_font_references("p { font: 1em Obf; }", &targets).is_err());
    }

    #[test]
    fn removes_only_target_font_manifest_item() {
        let targets = BTreeSet::from(["Fonts/obf.ttf".to_string()]);
        let opf = "<manifest><item id=\"font\" href=\"Fonts/obf.ttf\" media-type=\"font/ttf\"/><item id=\"chapter\" href=\"chapter.xhtml\"/></manifest>";
        assert_eq!(
            clean_strict_opf_font_manifest(opf, &targets),
            "<manifest><item id=\"chapter\" href=\"chapter.xhtml\"/></manifest>"
        );
    }

    #[test]
    fn formats_ocr_progress_like_python() {
        assert_eq!(format_ocr_progress(3, 12), "，进度 3/12 (25.0%)");
        assert_eq!(format_ocr_progress(0, 0), "");
    }

    #[test]
    fn preprocesses_rgb_images_like_python_onnx_backend() {
        let image = DynamicImage::ImageRgb8(RgbImage::from_pixel(1, 1, Rgb([0, 127, 255])));
        let tensor = preprocess_ocr_image(&image, [3, 2, 4], "RGB", 4).expect("tensor");
        assert_eq!((tensor.channels, tensor.height, tensor.width), (3, 2, 4));
        assert_eq!(tensor.data.len(), 24);
        assert!((tensor.data[0] + 1.0).abs() < f32::EPSILON);
        assert!((tensor.data[8] + 0.003_921_568_4).abs() < 1e-6);
        assert!((tensor.data[16] - 1.0).abs() < f32::EPSILON);
        assert_eq!(tensor.data[2], 0.0, "right padding must remain zero");
    }

    #[test]
    fn swaps_bgr_channels_before_normalization() {
        let image = DynamicImage::ImageRgb8(RgbImage::from_pixel(1, 1, Rgb([0, 0, 255])));
        let tensor = preprocess_ocr_image(&image, [3, 1, 1], "BGR", 1).expect("tensor");
        assert!((tensor.data[0] - 1.0).abs() < f32::EPSILON);
        assert!((tensor.data[2] + 1.0).abs() < f32::EPSILON);
    }
}
