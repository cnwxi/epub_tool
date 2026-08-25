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

#[cfg(test)]
mod tests {
    use super::parse_attributes;

    #[test]
    fn parses_quoted_item_attributes() {
        let attributes =
            parse_attributes(r#"<item id="a" href='Images/a.jpg' media-type="image/jpeg"/>"#)
                .unwrap();
        assert_eq!(attributes["href"], "Images/a.jpg");
    }
}
