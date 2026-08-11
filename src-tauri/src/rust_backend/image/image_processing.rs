use crate::rust_backend::epub::workspace::{
    media_type_for, resolve_reference, rewrite_reference, EpubWorkspace,
};
use crate::rust_backend::text_encoding::{
    decode_epub_text, encode_epub_text, text_kind_for_path, TextKind,
};
use crate::task_types::{TaskOptions, TaskType};
use image::{codecs::jpeg::JpegEncoder, DynamicImage, ImageFormat, ImageReader};
use regex::{Captures, Regex};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Cursor,
    path::Path,
    sync::LazyLock,
};

const IMAGE_EXTENSIONS: [&str; 5] = ["jpg", "jpeg", "png", "webp", "bmp"];
const MAX_IMAGE_PIXELS: u64 = 40_000_000;

#[derive(Debug, Clone, Copy)]
pub enum ImageMode {
    Compress,
    ToWebp,
    WebpToImage,
}

pub struct ImageTask {
    mode: ImageMode,
}

impl ImageTask {
    pub const fn new(mode: ImageMode) -> Self {
        Self { mode }
    }

    pub const fn task_type(&self) -> TaskType {
        match self.mode {
            ImageMode::Compress => TaskType::ImageCompress,
            ImageMode::ToWebp => TaskType::ImageToWebp,
            ImageMode::WebpToImage => TaskType::WebpToImg,
        }
    }

    pub fn is_supported_options(&self, options: &TaskOptions) -> bool {
        options.image().is_some_and(|options| {
            [options.quality, options.jpeg_quality, options.webp_quality]
                .into_iter()
                .flatten()
                .all(|quality| (1..=100).contains(&quality))
        })
    }

    pub fn is_supported_input(&self, _input: &Path, _options: &TaskOptions) -> bool {
        true
    }

    pub fn process(
        &self,
        workspace: &mut EpubWorkspace,
        options: &TaskOptions,
        mut log: impl FnMut(String),
    ) -> Result<ImageProcessOutcome, String> {
        let options = options
            .image()
            .ok_or_else(|| "图片任务需要 image options".to_string())?;
        let quality = if matches!(self.mode, ImageMode::Compress) {
            options.jpeg_quality.unwrap_or(82)
        } else {
            options.quality.unwrap_or(82)
        };
        let webp_quality = options.webp_quality.unwrap_or(82);
        let png_to_jpg = options.png_to_jpg.unwrap_or(false);
        let png_quantize = options.png_quantize.unwrap_or(false);

        let mut replacements = Vec::new();
        let mut existing: BTreeSet<String> = workspace.members.keys().cloned().collect();
        let member_names: Vec<String> = workspace.members.keys().cloned().collect();
        let mut candidates = 0_u32;
        let mut processed = 0_u32;
        let mut kept = 0_u32;
        let mut failed = 0_u32;
        let mut saved: i64 = 0;

        for source in member_names {
            let extension = extension_of(&source);
            if !IMAGE_EXTENSIONS.contains(&extension.as_str()) {
                continue;
            }
            if matches!(self.mode, ImageMode::WebpToImage) && extension != "webp" {
                continue;
            }
            candidates += 1;
            if matches!(self.mode, ImageMode::ToWebp) && extension == "webp" {
                kept += 1;
                continue;
            }
            let original = workspace
                .members
                .get(&source)
                .cloned()
                .ok_or_else(|| format!("EPUB 图片成员丢失: {source}"))?;
            let converted = match convert_image(
                &original,
                &extension,
                self.mode,
                quality,
                webp_quality,
                png_to_jpg,
                png_quantize,
            ) {
                Ok(converted) => converted,
                Err(error) => {
                    failed += 1;
                    log(format!("跳过无法处理的图片 {source}: {error}"));
                    continue;
                }
            };
            if matches!(self.mode, ImageMode::Compress) && converted.bytes.len() >= original.len() {
                kept += 1;
                continue;
            }
            let target = if converted.extension == extension {
                source.clone()
            } else {
                let target = converted_path(&source, &converted.extension, &existing);
                existing.remove(&source);
                existing.insert(target.clone());
                replacements.push((source.clone(), target.clone()));
                workspace.members.remove(&source);
                target
            };
            saved += original.len() as i64 - converted.bytes.len() as i64;
            workspace.members.insert(target, converted.bytes);
            processed += 1;
        }

        if matches!(self.mode, ImageMode::WebpToImage) {
            if candidates == 0 {
                log("没有找到需要转换的 WebP 图片".to_string());
                return Ok(ImageProcessOutcome::Skip);
            }
            if failed > 0 {
                return Err(format!("WebP 图片转换失败：{failed} 个文件无法处理"));
            }
        }
        if !replacements.is_empty() {
            rewrite_references(workspace, &replacements)?;
        }
        log(format!(
            "图片处理完成：处理 {processed}，保留 {kept}，失败 {failed}，节省 {:.2} MB",
            saved as f64 / (1024.0 * 1024.0)
        ));
        Ok(ImageProcessOutcome::Success)
    }
}

pub enum ImageProcessOutcome {
    Success,
    Skip,
}

struct ConvertedImage {
    bytes: Vec<u8>,
    extension: String,
}

fn convert_image(
    original: &[u8],
    extension: &str,
    mode: ImageMode,
    quality: u8,
    webp_quality: u8,
    png_to_jpg: bool,
    png_quantize: bool,
) -> Result<ConvertedImage, String> {
    let reader = ImageReader::new(Cursor::new(original))
        .with_guessed_format()
        .map_err(|error| format!("识别图片格式失败: {error}"))?;
    let detected_format = reader
        .format()
        .ok_or_else(|| "无法识别图片格式".to_string())?;
    let image = reader
        .decode()
        .map_err(|error| format!("解码图片失败: {error}"))?;
    if image.width() as u64 * image.height() as u64 > MAX_IMAGE_PIXELS {
        return Err("图片像素数超过安全限制".to_string());
    }
    match mode {
        ImageMode::ToWebp => Ok(ConvertedImage {
            bytes: encode_webp(&image, quality),
            extension: "webp".to_string(),
        }),
        ImageMode::WebpToImage => {
            if has_transparency(&image) {
                Ok(ConvertedImage {
                    bytes: if png_quantize {
                        encode_quantized_png(&image)?
                    } else {
                        encode_image(&image, ImageFormat::Png, quality)?
                    },
                    extension: "png".to_string(),
                })
            } else {
                Ok(ConvertedImage {
                    bytes: encode_image(&image, ImageFormat::Jpeg, quality)?,
                    extension: "jpg".to_string(),
                })
            }
        }
        ImageMode::Compress => {
            if extension == "bmp" {
                return Ok(ConvertedImage {
                    bytes: original.to_vec(),
                    extension: extension.to_string(),
                });
            }
            if extension == "png" && png_to_jpg && !image.color().has_alpha() {
                return Ok(ConvertedImage {
                    bytes: encode_image(&image, ImageFormat::Jpeg, quality)?,
                    extension: "jpg".to_string(),
                });
            }
            if extension == "png" && png_quantize {
                return Ok(ConvertedImage {
                    bytes: encode_quantized_png(&image)?,
                    extension: extension.to_string(),
                });
            }
            let target_format = detected_format;
            Ok(ConvertedImage {
                bytes: if target_format == ImageFormat::WebP {
                    encode_webp(&image, webp_quality)
                } else {
                    encode_image(&image, target_format, quality)?
                },
                extension: extension.to_string(),
            })
        }
    }
}

fn encode_image(image: &DynamicImage, format: ImageFormat, quality: u8) -> Result<Vec<u8>, String> {
    let mut output = Cursor::new(Vec::new());
    if format == ImageFormat::Jpeg {
        let rgb = image.to_rgb8();
        JpegEncoder::new_with_quality(&mut output, quality)
            .encode_image(&DynamicImage::ImageRgb8(rgb))
            .map_err(|error| format!("编码 JPEG 失败: {error}"))?;
    } else {
        image
            .write_to(&mut output, format)
            .map_err(|error| format!("编码图片失败: {error}"))?;
    }
    Ok(output.into_inner())
}

fn encode_webp(image: &DynamicImage, quality: u8) -> Vec<u8> {
    let rgba = image.to_rgba8();
    webp::Encoder::from_rgba(rgba.as_raw(), image.width(), image.height())
        .encode(quality as f32)
        .to_vec()
}

fn has_transparency(image: &DynamicImage) -> bool {
    image.to_rgba8().pixels().any(|pixel| pixel.0[3] < u8::MAX)
}

fn encode_quantized_png(image: &DynamicImage) -> Result<Vec<u8>, String> {
    let rgba = image.to_rgba8();
    let alpha_values: BTreeSet<u8> = rgba.pixels().map(|pixel| pixel.0[3]).collect();
    let alpha_levels: Vec<u8> = alpha_values.iter().copied().collect();
    let use_alpha = alpha_values.iter().any(|alpha| *alpha < u8::MAX);
    let mut colors = BTreeSet::new();
    for pixel in rgba.pixels() {
        colors.insert(quantized_color(pixel.0, &alpha_levels, use_alpha));
    }
    let palette: Vec<[u8; 4]> = colors.into_iter().collect();
    if palette.is_empty() || palette.len() > 256 {
        return Err("PNG 调色板量化失败：颜色数量超出限制".to_string());
    }
    let palette_indexes: BTreeMap<[u8; 4], u8> = palette
        .iter()
        .copied()
        .enumerate()
        .map(|(index, color)| (color, index as u8))
        .collect();
    let indexes: Vec<u8> = rgba
        .pixels()
        .map(|pixel| palette_indexes[&quantized_color(pixel.0, &alpha_levels, use_alpha)])
        .collect();
    let mut output = Vec::new();
    let mut encoder = png::Encoder::new(&mut output, image.width(), image.height());
    encoder.set_color(png::ColorType::Indexed);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_palette(
        palette
            .iter()
            .flat_map(|color| [color[0], color[1], color[2]])
            .collect::<Vec<_>>(),
    );
    if use_alpha {
        encoder.set_trns(palette.iter().map(|color| color[3]).collect::<Vec<_>>());
    }
    let mut writer = encoder
        .write_header()
        .map_err(|error| format!("创建 PNG 编码器失败: {error}"))?;
    writer
        .write_image_data(&indexes)
        .map_err(|error| format!("写入量化 PNG 失败: {error}"))?;
    drop(writer);
    Ok(output)
}

fn quantized_color(pixel: [u8; 4], alpha_levels: &[u8], use_alpha: bool) -> [u8; 4] {
    if !use_alpha {
        return [
            quantize_channel(pixel[0], 3),
            quantize_channel(pixel[1], 3),
            quantize_channel(pixel[2], 2),
            u8::MAX,
        ];
    }
    if alpha_levels.len() <= 4 {
        return [
            quantize_channel(pixel[0], 2),
            quantize_channel(pixel[1], 2),
            quantize_channel(pixel[2], 2),
            nearest_alpha(pixel[3], alpha_levels),
        ];
    }
    [
        quantize_channel(pixel[0], 2),
        quantize_channel(pixel[1], 2),
        quantize_channel(pixel[2], 2),
        quantize_channel(pixel[3], 2),
    ]
}

fn quantize_channel(value: u8, bits: u8) -> u8 {
    let levels = (1_u16 << bits) - 1;
    let level = (value as u16 * levels + 127) / 255;
    ((level * 255) / levels) as u8
}

fn nearest_alpha(value: u8, alpha_levels: &[u8]) -> u8 {
    alpha_levels
        .iter()
        .copied()
        .min_by_key(|candidate| value.abs_diff(*candidate))
        .unwrap_or(u8::MAX)
}

fn extension_of(path: &str) -> String {
    path.rsplit_once('.')
        .map_or_else(String::new, |(_, extension)| extension.to_ascii_lowercase())
}

fn converted_path(source: &str, extension: &str, existing: &BTreeSet<String>) -> String {
    let (base, _) = source.rsplit_once('.').unwrap_or((source, ""));
    let candidate = format!("{base}.{extension}");
    if candidate == source || !existing.contains(&candidate) {
        return candidate;
    }
    let mut index = 2;
    loop {
        let candidate = format!("{base}-{index}.{extension}");
        if !existing.contains(&candidate) {
            return candidate;
        }
        index += 1;
    }
}

pub(super) fn rewrite_references(
    workspace: &mut EpubWorkspace,
    replacements: &[(String, String)],
) -> Result<(), String> {
    let document_names: Vec<String> = workspace
        .members
        .keys()
        .filter(|name| {
            matches!(
                extension_of(name).as_str(),
                "xhtml" | "html" | "htm" | "css" | "svg" | "ncx"
            )
        })
        .cloned()
        .collect();
    for name in document_names {
        let data = workspace
            .members
            .get(&name)
            .ok_or_else(|| format!("EPUB 文档成员丢失: {name}"))?;
        let kind = text_kind_for_path(&name);
        let text = decode_epub_text(data, kind, &name)?;
        workspace.members.insert(
            name.clone(),
            encode_epub_text(&rewrite_document(&text, &name, replacements), kind),
        );
    }
    let opf = workspace
        .members
        .get(&workspace.opf_path)
        .ok_or_else(|| format!("EPUB 缺少 OPF 文件: {}", workspace.opf_path))?;
    let opf_text = decode_epub_text(opf, TextKind::Xml, &workspace.opf_path)?;
    let opf_with_manifest = rewrite_opf_manifest(&opf_text, &workspace.opf_path, replacements);
    let opf_path = workspace.opf_path.clone();
    workspace.members.insert(
        opf_path.clone(),
        encode_epub_text(
            &rewrite_document(&opf_with_manifest, &opf_path, replacements),
            TextKind::Xml,
        ),
    );
    Ok(())
}

fn rewrite_document(
    document: &str,
    document_path: &str,
    replacements: &[(String, String)],
) -> String {
    static REFERENCE_ATTRIBUTE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(\b(?:src|href|xlink:href|poster)\s*=\s*[\"'])([^\"']+)([\"'])"#)
            .expect("valid reference attribute regex")
    });
    static SRCSET_ATTRIBUTE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(\bsrcset\s*=\s*[\"'])([^\"']+)([\"'])"#).expect("valid srcset regex")
    });
    static CSS_URL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(url\(\s*[\"']?)([^\"')]+)([\"']?\s*\))"#).expect("valid CSS url regex")
    });
    let rewritten = REFERENCE_ATTRIBUTE.replace_all(document, |captures: &Captures<'_>| {
        format!(
            "{}{}{}",
            &captures[1],
            rewrite_one(&captures[2], document_path, replacements),
            &captures[3]
        )
    });
    let rewritten = SRCSET_ATTRIBUTE.replace_all(&rewritten, |captures: &Captures<'_>| {
        format!(
            "{}{}{}",
            &captures[1],
            rewrite_srcset(&captures[2], document_path, replacements),
            &captures[3]
        )
    });
    CSS_URL
        .replace_all(&rewritten, |captures: &Captures<'_>| {
            format!(
                "{}{}{}",
                &captures[1],
                rewrite_one(&captures[2], document_path, replacements),
                &captures[3]
            )
        })
        .into_owned()
}

fn rewrite_srcset(value: &str, document_path: &str, replacements: &[(String, String)]) -> String {
    let mut output = String::new();
    let mut position = 0;
    while position < value.len() {
        let remaining = &value[position..];
        let Some(character) = remaining.chars().next() else {
            break;
        };
        if character.is_whitespace() || character == ',' {
            output.push(character);
            position += character.len_utf8();
            continue;
        }
        let start = position;
        let data_uri = value[start..].to_ascii_lowercase().starts_with("data:");
        while position < value.len() {
            let character = value[position..]
                .chars()
                .next()
                .expect("position is in bounds");
            if character.is_whitespace() || (!data_uri && character == ',') {
                break;
            }
            position += character.len_utf8();
        }
        let raw = &value[start..position];
        let url = raw.strip_suffix(',').unwrap_or(raw);
        output.push_str(&rewrite_one(url, document_path, replacements));
        output.push_str(&raw[url.len()..]);
    }
    output
}

fn rewrite_one(reference: &str, document_path: &str, replacements: &[(String, String)]) -> String {
    let Ok(Some(source)) = resolve_reference(document_path, reference) else {
        return reference.to_string();
    };
    replacements
        .iter()
        .find(|(old, _)| old == &source)
        .map_or_else(
            || reference.to_string(),
            |(_, target)| rewrite_reference(reference, document_path, target),
        )
}

fn rewrite_opf_manifest(opf: &str, opf_path: &str, replacements: &[(String, String)]) -> String {
    static OPF_ITEM: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)(<item\b)([^>]*)(/?>)"#).expect("valid OPF item regex")
    });
    static OPF_HREF: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(\bhref\s*=\s*[\"'])([^\"']+)([\"'])"#).expect("valid href regex")
    });
    static OPF_MEDIA_TYPE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(\bmedia-type\s*=\s*[\"'])([^\"']+)([\"'])"#)
            .expect("valid media type regex")
    });
    OPF_ITEM
        .replace_all(opf, |captures: &Captures<'_>| {
            let attributes = &captures[2];
            let Some(href_match) = OPF_HREF.captures(attributes) else {
                return captures[0].to_string();
            };
            let raw_href = &href_match[2];
            let Ok(Some(source)) = resolve_reference(opf_path, raw_href) else {
                return captures[0].to_string();
            };
            let Some((_, target)) = replacements.iter().find(|(old, _)| old == &source) else {
                return captures[0].to_string();
            };
            let rewritten_href = rewrite_reference(raw_href, opf_path, target);
            let attributes = OPF_HREF
                .replace(attributes, format!("${{1}}{rewritten_href}${{3}}"))
                .into_owned();
            let attributes = OPF_MEDIA_TYPE
                .replace(
                    &attributes,
                    format!("${{1}}{}${{3}}", media_type_for(target)),
                )
                .into_owned();
            format!("{}{}{}", &captures[1], attributes, &captures[3])
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::{convert_image, ImageMode, ImageProcessOutcome, ImageTask};
    use crate::rust_backend::epub::EpubWorkspace;
    use crate::task_types::{ImageTaskOptions, TaskOptions};
    use image::{DynamicImage, ImageFormat, Rgba, RgbaImage};
    use std::{collections::BTreeMap, io::Cursor};

    #[test]
    fn webp_conversion_updates_manifest_and_references() {
        let image = DynamicImage::ImageRgba8(RgbaImage::from_pixel(2, 2, Rgba([220, 20, 20, 128])));
        let mut image_bytes = Cursor::new(Vec::new());
        image
            .write_to(&mut image_bytes, image::ImageFormat::WebP)
            .unwrap();
        let mut members = BTreeMap::new();
        members.insert("mimetype".to_string(), b"application/epub+zip".to_vec());
        members.insert(
            "OPS/package.opf".to_string(),
            br#"<package><manifest><item id="cover" href="Images/cover.webp" media-type="image/webp"/></manifest></package>"#.to_vec(),
        );
        members.insert(
            "OPS/chapter.xhtml".to_string(),
            br#"<html><body><img src="Images/cover.webp?rev=1#hero"/></body></html>"#.to_vec(),
        );
        members.insert(
            "OPS/Images/cover.webp".to_string(),
            image_bytes.into_inner(),
        );
        let mut workspace = EpubWorkspace {
            members,
            opf_path: "OPS/package.opf".to_string(),
        };

        let outcome = ImageTask::new(ImageMode::WebpToImage)
            .process(
                &mut workspace,
                &TaskOptions::Image(ImageTaskOptions {
                    quality: Some(82),
                    ..ImageTaskOptions::default()
                }),
                |_| {},
            )
            .unwrap();

        assert!(matches!(outcome, ImageProcessOutcome::Success));
        assert!(workspace.members.contains_key("OPS/Images/cover.png"));
        assert!(!workspace.members.contains_key("OPS/Images/cover.webp"));
        assert!(std::str::from_utf8(&workspace.members["OPS/package.opf"])
            .unwrap()
            .contains("media-type=\"image/png\""));
        assert!(std::str::from_utf8(&workspace.members["OPS/chapter.xhtml"])
            .unwrap()
            .contains("Images/cover.png?rev=1#hero"));
    }

    #[test]
    fn compression_preserves_detected_png_format_when_member_is_named_jpg() {
        let image = DynamicImage::ImageRgba8(RgbaImage::from_pixel(2, 2, Rgba([20, 30, 40, 255])));
        let mut bytes = Cursor::new(Vec::new());
        image.write_to(&mut bytes, ImageFormat::Png).unwrap();

        let converted = convert_image(
            &bytes.into_inner(),
            "jpg",
            ImageMode::Compress,
            82,
            82,
            false,
            false,
        )
        .unwrap();

        assert!(converted.bytes.starts_with(b"\x89PNG\r\n\x1a\n"));
        assert_eq!(converted.extension, "jpg");
    }
}
