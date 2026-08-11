//! OCR building blocks for `decrypt_font`.
//!
//! The renderer and ONNX runtime remain independent from EPUB rewrite policy so
//! each stage can be tested directly.

use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::Cursor,
    path::{Path, PathBuf},
    sync::{LazyLock, OnceLock},
};

use super::{
    encrypt_font::FontEncryptionPlan,
    font_cmap::{sfnt_data, unicode_cmap},
    font_obfuscation::split_obfuscation_text,
};
use crate::rust_backend::{
    epub::{workspace::relative_member_path, EpubWorkspace},
    text_encoding::{decode_epub_text, encode_epub_text, text_kind_for_path, TextKind},
    EpubTask, TaskOutcome, TaskUpdate,
};
use crate::task_types::{OcrCharPolicy, TaskOptions, TaskType};
use icu_properties::{props::GeneralCategory, CodePointMapData};
use image::{imageops::FilterType, DynamicImage, ImageFormat, Rgb, RgbImage};
use ort::{session::Session, value::TensorRef};
use regex::Regex;
use sha1::{Digest, Sha1};

const OCR_PERIOD_ALIASES: [char; 3] = ['.', '．', '｡'];
const OCR_PASSTHROUGH_PUNCTUATION: &str = "。，、；：？！“”‘’（）《》〈〉【】〔〕…—·";
const OCR_HANGUL_OBFUSCATION_START: u32 = 0xAC00;
const OCR_HANGUL_OBFUSCATION_END: u32 = 0xD7AF;
pub const DEFAULT_OCR_MAX_IMAGE_WIDTH: usize = 3200;
const DEFAULT_MIN_OCR_CONFIDENCE: f32 = 0.8;
const OCR_TOP_K: usize = 5;
const OCR_PROGRESS_INTERVAL: usize = 100;
const OCR_TASK_PROGRESS_LIMIT: f64 = 99.0;
const OCR_FAILURE_IMAGE_DIR: &str = "Images/ocr-failures";
const OCR_FAILURE_STYLE_CLASS: &str = "epub-tool-ocr-failure-style";
const OCR_FAILURE_STYLE_CSS: &str = ".ocr-failure{font-size:1em;white-space:nowrap;line-height:1;}.ocr-failure img.ocr-failure-glyph{height:1.18em!important;width:auto!important;max-width:none!important;max-height:none!important;vertical-align:-0.22em!important;display:inline-block!important;}";

#[derive(Debug, Clone)]
struct OcrFailure {
    character: char,
    status_code: &'static str,
    reason: String,
    font_path: String,
    image_path: Option<String>,
    confidence: Option<f32>,
    candidates: Vec<OcrCandidate>,
}

#[derive(Debug, Default)]
struct OcrReplacementPlan {
    replacements: BTreeMap<String, BTreeMap<char, char>>,
    failures: BTreeMap<String, BTreeMap<char, OcrFailure>>,
    failure_images: BTreeMap<String, Vec<u8>>,
}

pub struct DecryptFontTask;

impl EpubTask for DecryptFontTask {
    fn task_type(&self) -> TaskType {
        TaskType::DecryptFont
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        options.font().is_some_and(|options| {
            options
                .min_ocr_confidence
                .is_none_or(|value| (0.0..=1.0).contains(&value))
        })
    }

    fn supports_input(&self, _input: &Path, _options: &TaskOptions) -> bool {
        configured_ocr_resources().is_some()
    }

    fn process(
        &self,
        input: &Path,
        workspace: &mut EpubWorkspace,
        options: &TaskOptions,
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String> {
        let plan = FontEncryptionPlan::build_for_decryption(workspace, input, options)?;
        if plan.target_fonts().is_empty() {
            return Ok(TaskOutcome::Skip);
        }
        let resources = configured_ocr_resources()
            .ok_or_else(|| "未找到 Rust OCR 所需的 ONNX Runtime 或 PP-OCR 模型资源".to_string())?;
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
            .font()
            .and_then(|options| options.min_ocr_confidence)
            .map(|value| value as f32)
            .unwrap_or(DEFAULT_MIN_OCR_CONFIDENCE);
        let char_policy = options
            .font()
            .and_then(|options| options.ocr_char_policy)
            .unwrap_or(OcrCharPolicy::Strict);
        let ocr_plan = build_ocr_replacement_plan(
            &font_data_by_path,
            &text_by_font,
            &resources,
            minimum_confidence,
            char_policy,
            &workspace.opf_path,
            update,
        )?;
        let processed_fonts: BTreeSet<_> = font_data_by_path
            .iter()
            .filter(|(path, _)| {
                ocr_plan
                    .replacements
                    .get(*path)
                    .is_some_and(|table| !table.is_empty())
                    || ocr_plan
                        .failures
                        .get(*path)
                        .is_some_and(|table| !table.is_empty())
            })
            .map(|(path, _)| path.clone())
            .collect();
        if processed_fonts.is_empty() {
            return Ok(TaskOutcome::Skip);
        }

        for member in plan.xhtml_members() {
            let source = decode_epub_text(
                workspace
                    .members
                    .get(member)
                    .ok_or_else(|| format!("EPUB 缺少 XHTML 文件: {member}"))?,
                text_kind_for_path(member),
                member,
            )?;
            let failure_markup = ocr_failure_markup(&ocr_plan.failures, member);
            let rewritten = plan.rewrite_xhtml_with_ocr_failures(
                member,
                &source,
                &ocr_plan.replacements,
                &failure_markup,
            )?;
            workspace.members.insert(
                member.clone(),
                encode_epub_text(
                    &ensure_ocr_failure_style(rewritten),
                    text_kind_for_path(member),
                ),
            );
        }
        for (member, data) in workspace.members.clone() {
            if member.to_ascii_lowercase().ends_with(".css") {
                let css = decode_epub_text(&data, TextKind::Css, &member)?;
                let cleaned = clean_decrypted_css_font_references(&css, plan.target_families())?;
                workspace
                    .members
                    .insert(member, encode_epub_text(&cleaned, TextKind::Css));
            }
        }
        let opf_path = workspace.opf_path.clone();
        let opf = decode_epub_text(
            workspace
                .members
                .get(&opf_path)
                .ok_or_else(|| format!("EPUB 缺少 OPF 文件: {opf_path}"))?,
            TextKind::Xml,
            &opf_path,
        )?;
        let updated_opf = add_ocr_failure_images_to_manifest(
            &clean_decrypted_opf_font_manifest(
                &opf,
                &opf_target_hrefs(&opf_path, &processed_fonts),
            ),
            &ocr_plan.failure_images,
            &opf_path,
        );
        workspace.members.insert(
            opf_path.clone(),
            encode_epub_text(&updated_opf, TextKind::Xml),
        );
        for font_path in &processed_fonts {
            workspace.members.remove(font_path);
        }
        workspace.members.extend(ocr_plan.failure_images);
        update(TaskUpdate::message(format!(
            "Rust 字体 OCR 解密完成：处理 {} 个字体、{} 个 XHTML 文件。",
            processed_fonts.len(),
            plan.xhtml_members().len()
        )));
        Ok(TaskOutcome::Success)
    }
}

fn opf_target_hrefs(opf_path: &str, font_paths: &BTreeSet<String>) -> BTreeSet<String> {
    let directory = opf_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
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

fn ocr_failure_image_path(
    opf_path: &str,
    font_hash: &str,
    character: char,
    status_code: &str,
) -> String {
    let directory = opf_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
    let filename = format!(
        "{}_U-{:04X}_{status_code}.png",
        &font_hash[..8],
        character as u32
    );
    if directory.is_empty() {
        format!("{OCR_FAILURE_IMAGE_DIR}/{filename}")
    } else {
        format!("{directory}/{OCR_FAILURE_IMAGE_DIR}/{filename}")
    }
}

fn ocr_failure_markup(
    failures: &BTreeMap<String, BTreeMap<char, OcrFailure>>,
    html_path: &str,
) -> BTreeMap<String, BTreeMap<char, String>> {
    let html_directory = html_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
    failures
        .iter()
        .map(|(font_path, table)| {
            let markup = table
                .iter()
                .map(|(character, failure)| {
                    let image = failure.image_path.as_deref().map_or_else(String::new, |path| {
                        format!(
                            "<img class=\"ocr-failure-glyph\" src=\"{}\" alt=\"U+{:04X} {} {}\"/>",
                            escape_xml_attr(&relative_member_path(html_directory, path)),
                            failure.character as u32,
                            escape_xml_attr(&failure.character.to_string()),
                            failure.status_code,
                        )
                    });
                    let candidates = serde_json::to_string(&failure.candidates)
                        .unwrap_or_else(|_| "[]".to_string());
                    let confidence = failure
                        .confidence
                        .map(|value| format!("{value:.6}"))
                        .unwrap_or_default();
                    let text = format!(
                        "<span class=\"ocr-failure\" data-codepoint=\"U+{:04X}\" data-original-char=\"{}\" data-status=\"{}\" data-font-path=\"{}\" data-confidence=\"{}\" data-candidates=\"{}\" data-reason=\"{}\">{image}</span>",
                        failure.character as u32,
                        escape_xml_attr(&failure.character.to_string()),
                        failure.status_code,
                        escape_xml_attr(&failure.font_path),
                        confidence,
                        escape_xml_attr(&candidates),
                        escape_xml_attr(&failure.reason),
                    );
                    (*character, text)
                })
                .collect();
            (font_path.clone(), markup)
        })
        .collect()
}

fn ensure_ocr_failure_style(xhtml: String) -> String {
    if !xhtml.contains("class=\"ocr-failure\"")
        || xhtml.contains(&format!("class=\"{OCR_FAILURE_STYLE_CLASS}\""))
    {
        return xhtml;
    }
    let style = format!(
        "<style type=\"text/css\" class=\"{OCR_FAILURE_STYLE_CLASS}\">{OCR_FAILURE_STYLE_CSS}</style>"
    );
    if let Some(index) = xhtml.find("</head>") {
        return format!("{}{style}{}", &xhtml[..index], &xhtml[index..]);
    }
    if let Some(index) = xhtml.find('>') {
        if xhtml[..=index].to_ascii_lowercase().contains("<html") {
            return format!("{}{style}{}", &xhtml[..=index], &xhtml[index + 1..]);
        }
    }
    format!("{style}{xhtml}")
}

fn add_ocr_failure_images_to_manifest(
    opf: &str,
    failure_images: &BTreeMap<String, Vec<u8>>,
    opf_path: &str,
) -> String {
    if failure_images.is_empty() {
        return opf.to_string();
    }
    static ITEM_ID: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)\bid\s*=\s*[\"']([^\"']*)[\"']"#).expect("literal regex")
    });
    static ITEM_HREF: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)\bhref\s*=\s*[\"']([^\"']*)[\"']"#).expect("literal regex")
    });
    let mut ids = ITEM_ID
        .captures_iter(opf)
        .filter_map(|capture| capture.get(1).map(|value| value.as_str().to_string()))
        .collect::<BTreeSet<_>>();
    let mut hrefs = ITEM_HREF
        .captures_iter(opf)
        .filter_map(|capture| capture.get(1).map(|value| value.as_str().to_string()))
        .collect::<BTreeSet<_>>();
    let directory = opf_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
    let mut next_index = 1_usize;
    let mut entries = Vec::new();
    for image_path in failure_images.keys() {
        let image_href = relative_member_path(directory, image_path);
        if hrefs.contains(&image_href) {
            continue;
        }
        let item_id = loop {
            let candidate = format!("ocr_failure_{next_index}");
            next_index += 1;
            if ids.insert(candidate.clone()) {
                break candidate;
            }
        };
        hrefs.insert(image_href.clone());
        entries.push(format!(
            "    <item id=\"{item_id}\" href=\"{}\" media-type=\"image/png\"/>",
            escape_xml_attr(&image_href),
        ));
    }
    if entries.is_empty() {
        return opf.to_string();
    }
    let Some(index) = opf.to_ascii_lowercase().find("</manifest") else {
        return opf.to_string();
    };
    format!(
        "{}\n{}\n{}",
        &opf[..index],
        entries.join("\n"),
        &opf[index..]
    )
}

fn escape_xml_attr(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('\"', "&quot;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Application-owned location for the bundled OCR model. ONNX Runtime is linked
/// by the Rust application and is never selected from frontend task options.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OcrResourcePaths {
    pub model_dir: PathBuf,
}

static OCR_RESOURCE_PATHS: OnceLock<OcrResourcePaths> = OnceLock::new();

pub fn configure_ocr_resources(resources: OcrResourcePaths) -> Result<(), String> {
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
            "OCR 模型已使用不同资源初始化: {}",
            existing.model_dir.display()
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
    (model_dir.join("inference.onnx").is_file() && model_dir.join("inference.yml").is_file())
        .then_some(OcrResourcePaths { model_dir })
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct OcrTextResult {
    pub text: String,
    pub confidence: f32,
    pub candidates: Vec<OcrCandidate>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct OcrCandidate {
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

/// Model output reduced to the values consumed by the CTC decoder. Keeping this
/// representation independent from `ort` makes runtime tests deterministic.
#[derive(Debug, Clone, PartialEq)]
pub struct OcrCtcPrediction {
    pub shape: Vec<usize>,
    pub token_ids: Vec<usize>,
    pub scores: Vec<f32>,
    pub top_k: Vec<Vec<OcrTokenCandidate>>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct OcrTokenCandidate {
    pub token_id: usize,
    pub score: f32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OcrModelVerification {
    pub input_shape: [usize; 3],
    pub output_shape: Vec<usize>,
    pub character_count: usize,
}

/// The portion of PaddleOCR's bundled `inference.yml` that affects OCR
/// inference. It intentionally does not try to be a general YAML parser:
/// accepting arbitrary YAML here would make the model contract much harder to
/// audit. The parser supports the checked-in PaddleOCR model format and rejects
/// malformed or incomplete files.
#[derive(Debug, Clone, PartialEq)]
pub struct OcrModelConfig {
    pub image_shape: [usize; 3],
    pub image_mode: String,
    /// Includes the CTC blank token at index zero and the trailing space token.
    pub characters: Vec<String>,
}

/// Reusable ONNX OCR session for one fixed bundled model.
///
/// `ort` links its build-owned runtime once, then one instance of this backend
/// can recognize every glyph in a book without reloading the model.
pub struct OnnxGlyphOcrBackend {
    session: Session,
    pub config: OcrModelConfig,
    max_image_width: usize,
}

/// Rasterizes a single glyph before it is sent to PaddleOCR. The production
/// task supports embedded TrueType and CFF OpenType fonts, and returns a task
/// error for unsupported outlines rather than emitting a visually different
/// EPUB.
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
        let sfnt = sfnt_data(font_bytes)?;
        let font = fontdue::Font::from_bytes(sfnt, fontdue::FontSettings::default())
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

/// Builds the complete per-font OCR plan needed by EPUB rewriting.
///
/// Low-confidence characters are never guessed. The affected text is replaced
/// with a visible glyph image and its diagnostic metadata is retained, allowing
/// the rest of the EPUB to remain readable and the glyph to be reviewed.
fn build_ocr_replacement_plan(
    font_data_by_path: &BTreeMap<String, Vec<u8>>,
    text_by_font: &BTreeMap<String, String>,
    resources: &OcrResourcePaths,
    minimum_confidence: f32,
    char_policy: OcrCharPolicy,
    opf_path: &str,
    update: &mut dyn FnMut(TaskUpdate),
) -> Result<OcrReplacementPlan, String> {
    if !(0.0..=1.0).contains(&minimum_confidence) {
        return Err("OCR 最低置信度必须在 0 到 1 之间".to_string());
    }
    let mut backend =
        OnnxGlyphOcrBackend::from_model_dir(&resources.model_dir, DEFAULT_OCR_MAX_IMAGE_WIDTH)?;
    let mut plan = OcrReplacementPlan::default();
    let prepared_fonts = font_data_by_path
        .iter()
        .map(|(font_path, font_data)| {
            let cmap = unicode_cmap(font_data)
                .map_err(|error| format!("读取待解密字体 cmap 失败 {font_path}: {error}"))?;
            let text = text_by_font.get(font_path).map_or("", String::as_str);
            let candidates = ocr_candidate_text(text, char_policy);
            Ok((font_path, font_data, cmap, candidates))
        })
        .collect::<Result<Vec<_>, String>>()?;
    let total_characters = prepared_fonts
        .iter()
        .map(|(_, _, cmap, candidates)| {
            candidates
                .chars()
                .filter(|character| cmap.contains_key(&(*character as u32)))
                .count()
        })
        .sum::<usize>();
    update(TaskUpdate::progress(
        format!("Rust 字体 OCR 解密：待识别 {total_characters} 个字符。"),
        0.0,
    ));
    let mut processed_characters = 0_usize;
    for (font_path, font_data, cmap, candidates) in prepared_fonts {
        let renderer = FontGlyphRenderer::new(font_data)?;
        let mut replacements = BTreeMap::new();
        let mut failures = BTreeMap::new();
        let font_hash = format!("{:x}", Sha1::digest(font_data));
        for character in candidates.chars() {
            if !cmap.contains_key(&(character as u32)) {
                continue;
            }
            processed_characters += 1;
            if should_report_ocr_progress(processed_characters, total_characters) {
                update(TaskUpdate::progress(
                    format!(
                        "正在识别 U+{:04X}（字体 {font_path}）{}",
                        character as u32,
                        format_ocr_progress(processed_characters, total_characters)
                    ),
                    ocr_task_progress(processed_characters, total_characters),
                ));
            }
            let image = match renderer.render(character) {
                Ok(image) => image,
                Err(error) => {
                    failures.insert(
                        character,
                        OcrFailure {
                            character,
                            status_code: "OCR_EXCEPTION",
                            reason: format!("OCR 异常: {error}，字体 {font_path}"),
                            font_path: font_path.clone(),
                            image_path: None,
                            confidence: None,
                            candidates: Vec::new(),
                        },
                    );
                    continue;
                }
            };
            let result = match backend.recognize_image(&image) {
                Ok(result) => result,
                Err(error) => {
                    failures.insert(
                        character,
                        ocr_failure(
                            character,
                            "OCR_EXCEPTION",
                            format!("OCR 异常: {error}，字体 {font_path}"),
                            font_path,
                            opf_path,
                            &font_hash,
                            &image,
                            None,
                            &mut plan.failure_images,
                        )?,
                    );
                    continue;
                }
            };
            let normalized =
                normalize_ocr_text(&result.text, Some(character), is_period_like_image(&image));
            let mut decoded = normalized.chars();
            let Some(replacement) = decoded.next().filter(|_| decoded.next().is_none()) else {
                let status_code = if normalized.is_empty() {
                    "OCR_EMPTY"
                } else {
                    "OCR_MULTI_CHAR"
                };
                let reason = if normalized.is_empty() {
                    format!("OCR 为空，字体 {font_path}")
                } else {
                    format!("OCR 结果不是单字: {normalized}，字体 {font_path}")
                };
                failures.insert(
                    character,
                    ocr_failure(
                        character,
                        status_code,
                        reason,
                        font_path,
                        opf_path,
                        &font_hash,
                        &image,
                        Some(&result),
                        &mut plan.failure_images,
                    )?,
                );
                continue;
            };
            if result.confidence < minimum_confidence {
                failures.insert(
                    character,
                    ocr_failure(
                        character,
                        "OCR_LOW_CONF",
                        format!(
                            "OCR 置信度过低: {:.4} < {:.4}，字体 {font_path}",
                            result.confidence, minimum_confidence
                        ),
                        font_path,
                        opf_path,
                        &font_hash,
                        &image,
                        Some(&result),
                        &mut plan.failure_images,
                    )?,
                );
                continue;
            }
            replacements.insert(character, replacement);
        }
        plan.replacements.insert(font_path.clone(), replacements);
        plan.failures.insert(font_path.clone(), failures);
    }
    Ok(plan)
}

fn ocr_task_progress(processed_count: usize, total_count: usize) -> f64 {
    if total_count == 0 {
        0.0
    } else {
        processed_count.min(total_count) as f64 * OCR_TASK_PROGRESS_LIMIT / total_count as f64
    }
}

fn should_report_ocr_progress(processed_count: usize, total_count: usize) -> bool {
    processed_count == 1
        || processed_count.is_multiple_of(OCR_PROGRESS_INTERVAL)
        || processed_count == total_count
}

#[allow(clippy::too_many_arguments)]
fn ocr_failure(
    character: char,
    status_code: &'static str,
    reason: String,
    font_path: &str,
    opf_path: &str,
    font_hash: &str,
    image: &DynamicImage,
    evidence: Option<&OcrTextResult>,
    failure_images: &mut BTreeMap<String, Vec<u8>>,
) -> Result<OcrFailure, String> {
    let image_path = ocr_failure_image_path(opf_path, font_hash, character, status_code);
    let mut encoded = Cursor::new(Vec::new());
    image
        .write_to(&mut encoded, ImageFormat::Png)
        .map_err(|error| format!("编码 OCR 失败字形图像失败: {error}"))?;
    failure_images.insert(image_path.clone(), encoded.into_inner());
    Ok(OcrFailure {
        character,
        status_code,
        reason,
        font_path: font_path.to_string(),
        image_path: Some(image_path),
        confidence: evidence.map(|result| result.confidence),
        candidates: evidence
            .map(|result| result.candidates.clone())
            .unwrap_or_default(),
    })
}

/// Removes only the simple font references accepted by the native font plan.
/// The caller supplies normalized family names and applies the returned text
/// only after every OCR replacement has succeeded.
pub fn clean_decrypted_css_font_references(
    css: &str,
    target_families: &std::collections::BTreeSet<String>,
) -> Result<String, String> {
    if css.to_ascii_lowercase().contains("font:") || css.to_ascii_lowercase().contains("all:") {
        return Err("font/all 简写清理当前 Rust 实现暂不支持".to_string());
    }
    static FONT_FACE: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?is)\s*@font-face\s*\{[^{}]*\}").expect("literal regex"));
    static FONT_FAMILY: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?is)font-family\s*:\s*([^;{}]+);?").expect("literal regex"));
    let without_faces = FONT_FACE.replace_all(css, |captures: &regex::Captures<'_>| {
        FONT_FAMILY
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
    Ok(FONT_FAMILY
        .replace_all(&without_faces, |captures: &regex::Captures<'_>| {
            let kept: Vec<_> = captures[1]
                .split(',')
                .map(str::trim)
                .filter(|item| {
                    let normalized = item.trim_matches(['\'', '"']).to_ascii_lowercase();
                    !target_families.contains(&normalized)
                })
                .collect();
            if kept.len() == captures[1].split(',').count() {
                return captures[0].to_string();
            }
            if kept.is_empty() {
                String::new()
            } else {
                format!("font-family: {};", kept.join(", "))
            }
        })
        .into_owned())
}

pub fn clean_decrypted_opf_font_manifest(
    opf: &str,
    target_hrefs: &std::collections::BTreeSet<String>,
) -> String {
    static MANIFEST_ITEM: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"(?is)\s*<item\b([^>]*)/?>").expect("literal regex"));
    static MANIFEST_HREF: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)\bhref\s*=\s*['\"]([^'\"]*)['\"]"#).expect("literal regex")
    });
    MANIFEST_ITEM
        .replace_all(opf, |captures: &regex::Captures<'_>| {
            MANIFEST_HREF
                .captures(&captures[1])
                .and_then(|href| href.get(1))
                .is_some_and(|href| target_hrefs.contains(href.as_str()))
                .then(String::new)
                .unwrap_or_else(|| captures[0].to_string())
        })
        .into_owned()
}

impl OnnxGlyphOcrBackend {
    pub fn from_model_dir(model_dir: &Path, max_image_width: usize) -> Result<Self, String> {
        let model_path = model_dir.join("inference.onnx");
        let config_path = model_dir.join("inference.yml");
        let config = load_ocr_model_config(&config_path)?;
        Self::new(&model_path, config, max_image_width)
    }

    pub fn new(
        model_path: &Path,
        config: OcrModelConfig,
        max_image_width: usize,
    ) -> Result<Self, String> {
        if max_image_width == 0 {
            return Err("OCR 最大图像宽度必须大于零".to_string());
        }
        initialize_onnx_runtime()?;
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
            &prediction.top_k,
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

pub fn verify_ocr_model_dir(model_dir: &Path) -> Result<OcrModelVerification, String> {
    let model_path = model_dir.join("inference.onnx");
    let config = load_ocr_model_config(&model_dir.join("inference.yml"))?;
    let [channels, height, width] = config.image_shape;
    let tensor = OcrImageTensor {
        data: vec![0.0; channels * height * width],
        channels,
        height,
        width,
    };
    let prediction = infer_onnx_ctc(&model_path, &tensor)?;
    let output_classes = prediction
        .shape
        .last()
        .copied()
        .ok_or_else(|| "ONNX OCR 输出形状为空".to_string())?;
    if output_classes != config.characters.len() {
        return Err(format!(
            "ONNX OCR 输出类别数与 CTC 字典不匹配: {output_classes} != {}",
            config.characters.len()
        ));
    }
    Ok(OcrModelVerification {
        input_shape: config.image_shape,
        output_shape: prediction.shape,
        character_count: config.characters.len(),
    })
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

fn initialize_onnx_runtime() -> Result<(), String> {
    ort::init().commit();
    Ok(())
}

/// Runs one OCR tensor through the ONNX Runtime linked with the Rust binary.
pub fn infer_onnx_ctc(
    model_path: &Path,
    tensor: &OcrImageTensor,
) -> Result<OcrCtcPrediction, String> {
    initialize_onnx_runtime()?;
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
    let mut top_k = Vec::with_capacity(steps);
    for timestep in values.chunks_exact(vocab) {
        let ranked = top_k_token_candidates(timestep, OCR_TOP_K);
        let selected = ranked
            .first()
            .ok_or_else(|| "ONNX OCR 输出 timestep 为空".to_string())?;
        token_ids.push(selected.token_id);
        scores.push(selected.score);
        top_k.push(ranked);
    }
    Ok(OcrCtcPrediction {
        shape,
        token_ids,
        scores,
        top_k,
    })
}

fn top_k_token_candidates(scores: &[f32], limit: usize) -> Vec<OcrTokenCandidate> {
    let limit = limit.min(scores.len());
    let mut ranked = Vec::with_capacity(limit);
    for (token_id, score) in scores.iter().copied().enumerate() {
        let candidate = OcrTokenCandidate { token_id, score };
        let insertion_index =
            ranked.partition_point(|existing| token_candidate_order(existing, &candidate).is_lt());
        if insertion_index < limit {
            ranked.insert(insertion_index, candidate);
            ranked.truncate(limit);
        }
    }
    ranked
}

fn token_candidate_order(
    left: &OcrTokenCandidate,
    right: &OcrTokenCandidate,
) -> std::cmp::Ordering {
    right
        .score
        .total_cmp(&left.score)
        .then_with(|| left.token_id.cmp(&right.token_id))
}

fn decode_ctc_argmax(
    token_ids: &[usize],
    scores: &[f32],
    top_k: &[Vec<OcrTokenCandidate>],
    characters: &[String],
) -> OcrTextResult {
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
    let confidence = if selected_scores.is_empty() {
        0.0
    } else {
        selected_scores.iter().sum::<f32>() / selected_scores.len() as f32
    };
    let candidates = collect_ocr_candidates(&text, confidence, top_k, characters);
    OcrTextResult {
        text,
        confidence,
        candidates,
    }
}

/// Decodes the first batch item of a CTC recognition output.
///
/// `prediction` is indexed as `[time_step][token_id]`, where token zero is
/// the CTC blank. Repeated non-blank tokens are collapsed, blank tokens reset
/// repetition, and confidence is the mean selected-token score.
pub fn decode_ctc_prediction(prediction: &[Vec<f32>], characters: &[String]) -> OcrTextResult {
    let mut text = String::new();
    let mut scores = Vec::new();
    let mut previous = None;
    let mut top_k = Vec::with_capacity(prediction.len());
    for timestep in prediction {
        let ranked = top_k_token_candidates(timestep, OCR_TOP_K);
        let Some(selected) = ranked.first() else {
            continue;
        };
        let token_id = selected.token_id;
        let score = selected.score;
        top_k.push(ranked);
        if token_id == 0 || previous == Some(token_id) {
            previous = Some(token_id);
            continue;
        }
        previous = Some(token_id);
        if let Some(character) = characters.get(token_id) {
            text.push_str(character);
            scores.push(score);
        }
    }
    let confidence = if scores.is_empty() {
        0.0
    } else {
        scores.iter().sum::<f32>() / scores.len() as f32
    };
    let candidates = collect_ocr_candidates(&text, confidence, &top_k, characters);
    OcrTextResult {
        text,
        confidence,
        candidates,
    }
}

fn collect_ocr_candidates(
    selected_text: &str,
    selected_confidence: f32,
    top_k: &[Vec<OcrTokenCandidate>],
    characters: &[String],
) -> Vec<OcrCandidate> {
    let mut evidence = BTreeMap::<String, f32>::new();
    if !selected_text.is_empty() {
        evidence.insert(selected_text.to_string(), selected_confidence);
    }
    for timestep in top_k {
        for candidate in timestep {
            if candidate.token_id == 0 {
                continue;
            }
            let Some(text) = characters
                .get(candidate.token_id)
                .filter(|text| !text.is_empty())
            else {
                continue;
            };
            evidence
                .entry(text.clone())
                .and_modify(|score| *score = score.max(candidate.score))
                .or_insert(candidate.score);
        }
    }
    let mut candidates: Vec<_> = evidence
        .into_iter()
        .map(|(text, confidence)| OcrCandidate { text, confidence })
        .collect();
    candidates.sort_by(|left, right| {
        right
            .confidence
            .total_cmp(&left.confidence)
            .then_with(|| left.text.cmp(&right.text))
    });
    candidates.truncate(OCR_TOP_K);
    candidates
}

/// Produces NCHW data for a batch of one without requiring ONNX Runtime. RGB
/// pixels are normalized by `(pixel / 255 - 0.5) / 0.5` and right-padded with
/// zeros.
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

fn ocr_candidate_text(text: &str, policy: OcrCharPolicy) -> String {
    let strict = split_obfuscation_text(text)
        .obfuscate
        .chars()
        .collect::<BTreeSet<_>>();
    let categories = CodePointMapData::<GeneralCategory>::new();
    let mut seen = BTreeSet::new();
    text.chars()
        .filter(|character| {
            if is_ocr_common_exclusion(*character, categories.get(*character)) {
                return false;
            }
            strict.contains(character)
                || is_ocr_obfuscation_hint_char(*character)
                || (policy == OcrCharPolicy::Compatible && !character.is_ascii())
        })
        .filter(|character| seen.insert(*character))
        .collect()
}

fn is_ocr_common_exclusion(character: char, category: GeneralCategory) -> bool {
    character.is_whitespace()
        || matches!(
            category,
            GeneralCategory::Control
                | GeneralCategory::Format
                | GeneralCategory::Surrogate
                | GeneralCategory::Unassigned
        )
        || OCR_PASSTHROUGH_PUNCTUATION.contains(character)
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
        clean_decrypted_css_font_references, clean_decrypted_opf_font_manifest,
        decode_ctc_prediction, filter_text_by_cmap, format_ocr_failure_placeholder,
        format_ocr_progress, is_ocr_obfuscation_hint_char, is_period_like_image,
        normalize_ocr_text, ocr_candidate_text, ocr_task_progress, parse_ocr_model_config,
        preprocess_ocr_image, should_report_ocr_progress, token_candidate_order,
        top_k_token_candidates, OcrTokenCandidate,
    };
    use crate::task_types::OcrCharPolicy;
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
    fn preserves_strict_and_compatible_ocr_character_policies() {
        let text = "你０Ａ❶0A。？，！、；：《》（）\u{E000}<& \u{0000}";
        assert_eq!(
            ocr_candidate_text(text, OcrCharPolicy::Strict),
            "你０Ａ0A\u{E000}"
        );
        assert_eq!(
            ocr_candidate_text(text, OcrCharPolicy::Compatible),
            "你０Ａ❶0A\u{E000}"
        );
    }

    #[test]
    fn formats_stable_failure_placeholders() {
        assert_eq!(
            format_ocr_failure_placeholder('\u{E000}', "OCR_LOW_CONF"),
            "[U+E000 OCR_LOW_CONF]"
        );
    }

    #[test]
    fn decodes_ctc_output_with_blank_and_duplicate_collapse() {
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
        assert!(result
            .candidates
            .iter()
            .any(|candidate| candidate.text == "你好"));
        assert!(result
            .candidates
            .iter()
            .any(|candidate| candidate.text == "你"));
        assert!(result
            .candidates
            .iter()
            .any(|candidate| candidate.text == "好"));
    }

    #[test]
    fn selects_stable_top_k_without_sorting_the_full_vocabulary() {
        let scores = [0.5, f32::NAN, 0.5, f32::INFINITY, -0.0, 0.0, -1.0];
        let actual = top_k_token_candidates(&scores, 5);
        let mut expected = scores
            .iter()
            .copied()
            .enumerate()
            .map(|(token_id, score)| OcrTokenCandidate { token_id, score })
            .collect::<Vec<_>>();
        expected.sort_by(token_candidate_order);
        expected.truncate(5);
        assert_eq!(
            actual.iter().map(|item| item.token_id).collect::<Vec<_>>(),
            expected
                .iter()
                .map(|item| item.token_id)
                .collect::<Vec<_>>()
        );
        assert_eq!(
            top_k_token_candidates(&[0.8, 0.8, 0.7], 5)
                .iter()
                .map(|item| item.token_id)
                .collect::<Vec<_>>(),
            [0, 1, 2]
        );
        assert!(top_k_token_candidates(&scores, 0).is_empty());
    }

    #[test]
    fn parses_bundled_paddleocr_config() {
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
    fn detects_period_like_glyphs_with_stable_thresholds() {
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
                if !(36..64).contains(&x) || !(26..74).contains(&y) {
                    zero.put_pixel(x, y, Rgb([0, 0, 0]));
                }
            }
        }
        assert!(!is_period_like_image(&DynamicImage::ImageRgb8(zero)));
    }

    #[test]
    fn cleans_selected_font_references_and_preserves_fallback_family() {
        let targets = BTreeSet::from(["obf".to_string()]);
        let css = "@font-face { font-family: Obf; src: url(font.ttf); }\n.body { font-family: Obf, serif; }";
        assert_eq!(
            clean_decrypted_css_font_references(css, &targets).expect("clean CSS"),
            "\n.body { font-family: serif; }"
        );
        assert!(clean_decrypted_css_font_references("p { font: 1em Obf; }", &targets).is_err());
    }

    #[test]
    fn removes_only_target_font_manifest_item() {
        let targets = BTreeSet::from(["Fonts/obf.ttf".to_string()]);
        let opf = "<manifest><item id=\"font\" href=\"Fonts/obf.ttf\" media-type=\"font/ttf\"/><item id=\"chapter\" href=\"chapter.xhtml\"/></manifest>";
        assert_eq!(
            clean_decrypted_opf_font_manifest(opf, &targets),
            "<manifest><item id=\"chapter\" href=\"chapter.xhtml\"/></manifest>"
        );
    }

    #[test]
    fn formats_stable_ocr_progress() {
        assert_eq!(format_ocr_progress(3, 12), "，进度 3/12 (25.0%)");
        assert_eq!(format_ocr_progress(0, 0), "");
        assert_eq!(ocr_task_progress(0, 0), 0.0);
        assert_eq!(ocr_task_progress(3, 12), 24.75);
        assert_eq!(ocr_task_progress(12, 12), 99.0);
        assert_eq!(ocr_task_progress(13, 12), 99.0);
        assert!(should_report_ocr_progress(1, 9493));
        assert!(!should_report_ocr_progress(99, 9493));
        assert!(should_report_ocr_progress(100, 9493));
        assert!(should_report_ocr_progress(9493, 9493));
    }

    #[test]
    fn preprocesses_rgb_images_for_onnx_ctc() {
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
