//! Shared EPUB rewrite primitives. Parsing remains conservative and reports
//! unsupported EPUB structures explicitly.

use super::workspace::{resolve_reference, EpubWorkspace};
use crate::rust_backend::text_encoding::{decode_epub_text, TextKind};
use percent_encoding::percent_decode_str;
use regex::Regex;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum ResourceType {
    Text,
    Css,
    Image,
    Font,
    Audio,
    Video,
    Other,
}

impl ResourceType {
    pub const fn directory(self) -> &'static str {
        match self {
            Self::Text => "Text",
            Self::Css => "Styles",
            Self::Image => "Images",
            Self::Font => "Fonts",
            Self::Audio => "Audio",
            Self::Video => "Video",
            Self::Other => "Misc",
        }
    }
}

#[derive(Debug, Clone)]
pub struct ManifestItem {
    pub id: String,
    pub href: String,
    pub media_type: String,
    pub properties: String,
    pub source_path: String,
    pub resource_type: ResourceType,
}

#[derive(Debug, Clone)]
pub struct ParsedBook {
    pub opf: String,
    pub opf_path: String,
    pub container: Vec<u8>,
    pub items: Vec<ManifestItem>,
    pub toc_id: Option<String>,
}

impl ParsedBook {
    pub fn parse(workspace: &EpubWorkspace) -> Result<Self, String> {
        let opf = workspace
            .members
            .get(&workspace.opf_path)
            .ok_or_else(|| "EPUB 缺少 OPF 文件".to_string())?;
        let opf = decode_epub_text(opf, TextKind::Xml, &workspace.opf_path)?;
        let container = workspace
            .members
            .get("META-INF/container.xml")
            .ok_or_else(|| "EPUB 缺少 META-INF/container.xml".to_string())?
            .clone();
        let manifest = extract_tag_block(&opf, "manifest")
            .ok_or_else(|| "OPF 缺少 manifest，当前 Rust 实现暂不支持".to_string())?;
        static ITEM_PATTERN: LazyLock<Regex> =
            LazyLock::new(|| Regex::new(r"(?is)<item\b[^>]*>").expect("valid item regex"));
        let mut items = Vec::new();
        for matched in ITEM_PATTERN.find_iter(manifest) {
            let attrs = parse_attributes(matched.as_str())?;
            let Some(id) = attrs.get("id").filter(|value| !value.is_empty()) else {
                return Err("manifest item 缺少 id，当前 Rust 实现暂不支持".to_string());
            };
            let Some(raw_href) = attrs.get("href") else {
                return Err("manifest item 缺少 href，当前 Rust 实现暂不支持".to_string());
            };
            let href = percent_decode_str(raw_href)
                .decode_utf8()
                .map_err(|_| "manifest href 不是 UTF-8，当前 Rust 实现暂不支持".to_string())?
                .into_owned();
            let source_path = resolve_reference(&workspace.opf_path, &href)?.ok_or_else(|| {
                "manifest href 不是 EPUB 内路径，当前 Rust 实现暂不支持".to_string()
            })?;
            if !workspace.members.contains_key(&source_path) {
                return Err(format!("manifest 资源不存在: {source_path}"));
            }
            items.push(ManifestItem {
                id: id.clone(),
                href,
                media_type: attrs.get("media-type").cloned().unwrap_or_default(),
                properties: attrs.get("properties").cloned().unwrap_or_default(),
                source_path: source_path.clone(),
                resource_type: resource_type(&source_path, attrs.get("media-type")),
            });
        }
        if items.is_empty() {
            return Err("manifest 没有资源，当前 Rust 实现暂不支持".to_string());
        }
        let spine_open = find_open_tag(&opf, "spine");
        let toc_id = spine_open
            .as_deref()
            .map(parse_attributes)
            .transpose()?
            .and_then(|attrs| attrs.get("toc").cloned());
        Ok(Self {
            opf,
            opf_path: workspace.opf_path.clone(),
            container,
            items,
            toc_id,
        })
    }

    pub fn ensure_all_resources_are_manifested(
        &self,
        workspace: &EpubWorkspace,
    ) -> Result<(), String> {
        let known: BTreeSet<&str> = self
            .items
            .iter()
            .map(|item| item.source_path.as_str())
            .collect();
        for path in workspace.members.keys() {
            if path == "mimetype" || path.starts_with("META-INF/") || path == &self.opf_path {
                continue;
            }
            if !known.contains(path.as_str()) {
                return Err(format!("存在未登记资源，当前 Rust 实现暂不支持: {path}"));
            }
        }
        Ok(())
    }
}

pub fn resource_type(href: &str, media_type: Option<&String>) -> ResourceType {
    let media_type = media_type.map(String::as_str).unwrap_or_default();
    let lower = href.to_ascii_lowercase();
    if media_type == "application/xhtml+xml"
        || lower.ends_with(".xhtml")
        || lower.ends_with(".html")
    {
        ResourceType::Text
    } else if media_type == "text/css" || lower.ends_with(".css") {
        ResourceType::Css
    } else if media_type.contains("image/") {
        ResourceType::Image
    } else if media_type.contains("font/")
        || lower.ends_with(".ttf")
        || lower.ends_with(".otf")
        || lower.ends_with(".woff")
    {
        ResourceType::Font
    } else if media_type.contains("audio/") {
        ResourceType::Audio
    } else if media_type.contains("video/") {
        ResourceType::Video
    } else {
        ResourceType::Other
    }
}

pub fn split_slim_href(href: &str) -> (String, String, bool) {
    let (directory, basename) = href.rsplit_once('/').map_or(("", href), |parts| parts);
    let (stem, extension) = split_extension(basename);
    let lower = stem.to_ascii_lowercase();
    if !lower.ends_with("slim") {
        return (href.to_string(), extension.to_string(), false);
    }
    let mut removed = &stem[..stem.len() - "slim".len()];
    if removed.ends_with(['~', '_', '-']) {
        removed = &removed[..removed.len() - 1];
    }
    let name = format!("{removed}{extension}");
    (
        if directory.is_empty() {
            name
        } else {
            format!("{directory}/{name}")
        },
        extension.to_string(),
        true,
    )
}

pub fn split_extension(value: &str) -> (&str, &str) {
    value
        .rfind('.')
        .filter(|index| *index > 0)
        .map_or((value, ""), |index| value.split_at(index))
}

pub fn basename(value: &str) -> &str {
    value.rsplit('/').next().unwrap_or(value)
}

pub fn replace_tag_block(source: &str, tag: &str, replacement: &str) -> Result<String, String> {
    let pattern =
        crate::rust_backend::util::cached_regex(&format!(r"(?is)<{tag}\b[^>]*>.*?</{tag}\s*>"))
            .map_err(|error| format!("创建 OPF 正则失败: {error}"))?;
    if !pattern.is_match(source) {
        return Err(format!("OPF 缺少 <{tag}> 区块"));
    }
    Ok(pattern.replacen(source, 1, replacement).into_owned())
}

pub fn parse_attributes(tag: &str) -> Result<BTreeMap<String, String>, String> {
    let bytes = tag.as_bytes();
    let mut attributes = BTreeMap::new();
    let mut index = 0;
    while index < bytes.len() {
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        while bytes
            .get(index)
            .is_some_and(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b':' | b'-' | b'_'))
        {
            index += 1;
        }
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        if bytes.get(index) != Some(&b'=') {
            index += 1;
            continue;
        }
        let mut name_start = index;
        while name_start > 0 && bytes[name_start - 1].is_ascii_whitespace() {
            name_start -= 1;
        }
        let mut name_end = name_start;
        while name_end > 0
            && (bytes[name_end - 1].is_ascii_alphanumeric()
                || matches!(bytes[name_end - 1], b':' | b'-' | b'_'))
        {
            name_end -= 1;
        }
        let name = tag[name_end..name_start].to_ascii_lowercase();
        index += 1;
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        let quote = *bytes
            .get(index)
            .filter(|value| matches!(value, b'\'' | b'"'))
            .ok_or_else(|| "OPF 属性没有引号，当前 Rust 实现暂不支持".to_string())?;
        let value_start = index + 1;
        let value_end = bytes[value_start..]
            .iter()
            .position(|value| *value == quote)
            .map(|offset| value_start + offset)
            .ok_or_else(|| "OPF 属性引号未闭合，当前 Rust 实现暂不支持".to_string())?;
        if !name.is_empty() {
            attributes.insert(name, tag[value_start..value_end].to_string());
        }
        index = value_end + 1;
    }
    Ok(attributes)
}

fn extract_tag_block<'a>(source: &'a str, tag: &str) -> Option<&'a str> {
    let pattern =
        crate::rust_backend::util::cached_regex(&format!(r"(?is)<{tag}\b[^>]*>(.*?)</{tag}\s*>"))
            .ok()?;
    pattern
        .captures(source)?
        .get(1)
        .map(|capture| capture.as_str())
}

fn find_open_tag(source: &str, tag: &str) -> Option<String> {
    let pattern = crate::rust_backend::util::cached_regex(&format!(r"(?is)<{tag}\b[^>]*>")).ok()?;
    pattern
        .find(source)
        .map(|matched| matched.as_str().to_string())
}

/// MD5 is used solely to retain the established filename-obfuscation algorithm.
pub fn md5(input: &[u8]) -> [u8; 16] {
    const SHIFTS: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5,
        9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10,
        15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];
    const K: [u32; 64] = [
        0xd76a_a478,
        0xe8c7_b756,
        0x2420_70db,
        0xc1bd_ceee,
        0xf57c_0faf,
        0x4787_c62a,
        0xa830_4613,
        0xfd46_9501,
        0x6980_98d8,
        0x8b44_f7af,
        0xffff_5bb1,
        0x895c_d7be,
        0x6b90_1122,
        0xfd98_7193,
        0xa679_438e,
        0x49b4_0821,
        0xf61e_2562,
        0xc040_b340,
        0x265e_5a51,
        0xe9b6_c7aa,
        0xd62f_105d,
        0x0244_1453,
        0xd8a1_e681,
        0xe7d3_fbc8,
        0x21e1_cde6,
        0xc337_07d6,
        0xf4d5_0d87,
        0x455a_14ed,
        0xa9e3_e905,
        0xfcef_a3f8,
        0x676f_02d9,
        0x8d2a_4c8a,
        0xfffa_3942,
        0x8771_f681,
        0x6d9d_6122,
        0xfde5_380c,
        0xa4be_ea44,
        0x4bde_cfa9,
        0xf6bb_4b60,
        0xbebf_bc70,
        0x289b_7ec6,
        0xeaa1_27fa,
        0xd4ef_3085,
        0x0488_1d05,
        0xd9d4_d039,
        0xe6db_99e5,
        0x1fa2_7cf8,
        0xc4ac_5665,
        0xf429_2244,
        0x432a_ff97,
        0xab94_23a7,
        0xfc93_a039,
        0x655b_59c3,
        0x8f0c_cc92,
        0xffef_f47d,
        0x8584_5dd1,
        0x6fa8_7e4f,
        0xfe2c_e6e0,
        0xa301_4314,
        0x4e08_11a1,
        0xf753_7e82,
        0xbd3a_f235,
        0x2ad7_d2bb,
        0xeb86_d391,
    ];
    let bit_length = (input.len() as u64).wrapping_mul(8);
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend(bit_length.to_le_bytes());
    let (mut a0, mut b0, mut c0, mut d0) = (
        0x6745_2301_u32,
        0xefcd_ab89_u32,
        0x98ba_dcfe_u32,
        0x1032_5476_u32,
    );
    for chunk in padded.chunks_exact(64) {
        let mut words = [0_u32; 16];
        for (index, word) in words.iter_mut().enumerate() {
            *word = u32::from_le_bytes(chunk[index * 4..index * 4 + 4].try_into().expect("word"));
        }
        let (mut a, mut b, mut c, mut d) = (a0, b0, c0, d0);
        for index in 0..64 {
            let (f, g) = match index {
                0..=15 => ((b & c) | (!b & d), index),
                16..=31 => ((d & b) | (!d & c), (5 * index + 1) % 16),
                32..=47 => (b ^ c ^ d, (3 * index + 5) % 16),
                _ => (c ^ (b | !d), (7 * index) % 16),
            };
            let next = b.wrapping_add(
                (a.wrapping_add(f)
                    .wrapping_add(K[index])
                    .wrapping_add(words[g]))
                .rotate_left(SHIFTS[index]),
            );
            (a, d, c, b) = (d, c, b, next);
        }
        a0 = a0.wrapping_add(a);
        b0 = b0.wrapping_add(b);
        c0 = c0.wrapping_add(c);
        d0 = d0.wrapping_add(d);
    }
    let mut output = [0_u8; 16];
    for (index, value) in [a0, b0, c0, d0].into_iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&value.to_le_bytes());
    }
    output
}

pub fn md5_hex(input: &[u8]) -> String {
    md5(input)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{md5_hex, parse_attributes, split_slim_href};

    #[test]
    fn md5_matches_known_vectors() {
        assert_eq!(md5_hex(b"f2"), "3667f6a0c97490758d7dc9659d01ea34");
    }

    #[test]
    fn parses_quoted_item_attributes_and_slim_suffixes() {
        let attributes =
            parse_attributes(r#"<item id="a" href='Images/a.jpg' media-type="image/jpeg"/>"#)
                .unwrap();
        assert_eq!(attributes["href"], "Images/a.jpg");
        assert_eq!(
            split_slim_href("Images/base_slim.jpg"),
            ("Images/base.jpg".to_string(), ".jpg".to_string(), true)
        );
    }
}
