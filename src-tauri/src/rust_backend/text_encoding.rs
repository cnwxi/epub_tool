//! Decoding and UTF-8 normalization for text members stored in EPUB archives.

use encoding_rs::Encoding;
use regex::Regex;
use std::sync::LazyLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TextKind {
    Xml,
    Html,
    Css,
}

impl TextKind {
    const fn label(self) -> &'static str {
        match self {
            Self::Xml => "XML",
            Self::Html => "HTML",
            Self::Css => "CSS",
        }
    }
}

pub(crate) fn text_kind_for_path(path: &str) -> TextKind {
    let extension = path
        .rsplit_once('.')
        .map(|(_, extension)| extension)
        .unwrap_or_default();
    match extension.to_ascii_lowercase().as_str() {
        "css" => TextKind::Css,
        "html" | "htm" => TextKind::Html,
        _ => TextKind::Xml,
    }
}

/// Decodes an EPUB text member. UTF-8 is always preferred; legacy encodings
/// are used only when UTF-8 validation fails and an unambiguous marker exists.
pub(crate) fn decode_epub_text(data: &[u8], kind: TextKind, path: &str) -> Result<String, String> {
    let utf8_data = data.strip_prefix(b"\xEF\xBB\xBF").unwrap_or(data);
    if let Ok(text) = std::str::from_utf8(utf8_data) {
        return Ok(text.to_string());
    }

    if let Some(text) = decode_with_bom(data, kind, path)? {
        return Ok(text);
    }
    if let Some((encoding, data)) = sniff_unicode_encoding(data) {
        return decode_named_encoding(data, encoding, kind, path);
    }
    if let Some(encoding) = declared_encoding(data, kind) {
        return decode_named_encoding(data, &encoding, kind, path);
    }

    Err(format!(
        "{} 不是有效 UTF-8，且没有可识别的 BOM 或编码声明: {path}",
        kind.label()
    ))
}

/// Returns UTF-8 bytes after updating declarations that would otherwise claim
/// the original legacy encoding.
pub(crate) fn encode_epub_text(text: &str, kind: TextKind) -> Vec<u8> {
    match kind {
        TextKind::Xml => normalize_xml_encoding(text),
        TextKind::Html => normalize_html_encoding(text),
        TextKind::Css => normalize_css_encoding(text),
    }
    .into_bytes()
}

fn decode_with_bom(data: &[u8], kind: TextKind, path: &str) -> Result<Option<String>, String> {
    if data.starts_with(b"\x00\x00\xFE\xFF") {
        return decode_utf32(&data[4..], false, kind, path).map(Some);
    }
    if data.starts_with(b"\xFF\xFE\x00\x00") {
        return decode_utf32(&data[4..], true, kind, path).map(Some);
    }
    if data.starts_with(b"\xFE\xFF") {
        return decode_utf16(&data[2..], false, kind, path).map(Some);
    }
    if data.starts_with(b"\xFF\xFE") {
        return decode_utf16(&data[2..], true, kind, path).map(Some);
    }
    Ok(None)
}

fn sniff_unicode_encoding(data: &[u8]) -> Option<(&'static str, &[u8])> {
    match data {
        [0, 0, 0, b'<', ..] => Some(("utf-32be", data)),
        [b'<', 0, 0, 0, ..] => Some(("utf-32le", data)),
        [0, b'<', 0, b'?', ..] => Some(("utf-16be", data)),
        [b'<', 0, b'?', 0, ..] => Some(("utf-16le", data)),
        _ => None,
    }
}

fn declared_encoding(data: &[u8], kind: TextKind) -> Option<String> {
    let header = String::from_utf8_lossy(&data[..data.len().min(1024)]);
    match kind {
        TextKind::Xml => {
            static XML_ENCODING: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(r#"(?i)<\?xml\b[^>]*\bencoding\s*=\s*[\"']([^\"']+)[\"']"#)
                    .expect("valid XML encoding regex")
            });
            XML_ENCODING
                .captures(&header)
                .map(|captures| captures[1].to_string())
        }
        TextKind::Html => {
            static HTML_CHARSET: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(r#"(?i)<meta\b[^>]*\bcharset\s*=\s*[\"']?([^\"'\s/>]+)"#)
                    .expect("valid HTML charset regex")
            });
            static HTML_CONTENT_TYPE: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(
                    r#"(?i)<meta\b[^>]*\bcontent\s*=\s*[\"'][^\"']*?\bcharset\s*=\s*([^\"';\s]+)"#,
                )
                .expect("valid HTML content-type charset regex")
            });
            HTML_CHARSET
                .captures(&header)
                .or_else(|| HTML_CONTENT_TYPE.captures(&header))
                .map(|captures| captures[1].to_string())
        }
        TextKind::Css => {
            static CSS_ENCODING: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(r#"(?i)^\s*@charset\s+[\"']([^\"']+)[\"']\s*;"#)
                    .expect("valid CSS encoding regex")
            });
            CSS_ENCODING
                .captures(&header)
                .map(|captures| captures[1].to_string())
        }
    }
}

fn decode_named_encoding(
    data: &[u8],
    label: &str,
    kind: TextKind,
    path: &str,
) -> Result<String, String> {
    if label.eq_ignore_ascii_case("utf-32") || label.eq_ignore_ascii_case("utf-32be") {
        return decode_utf32(data, false, kind, path);
    }
    if label.eq_ignore_ascii_case("utf-32le") {
        return decode_utf32(data, true, kind, path);
    }
    let encoding = Encoding::for_label(label.as_bytes())
        .ok_or_else(|| format!("{} 声明了不支持的编码 {label}: {path}", kind.label()))?;
    let (text, had_errors) = encoding.decode_without_bom_handling(data);
    if had_errors {
        return Err(format!(
            "无法按 {} 解码 {}: {path}",
            encoding.name(),
            kind.label()
        ));
    }
    Ok(text.into_owned())
}

fn decode_utf16(
    data: &[u8],
    little_endian: bool,
    kind: TextKind,
    path: &str,
) -> Result<String, String> {
    if !data.len().is_multiple_of(2) {
        return Err(format!("{} 的 UTF-16 字节长度无效: {path}", kind.label()));
    }
    let values = data.chunks_exact(2).map(|chunk| {
        if little_endian {
            u16::from_le_bytes([chunk[0], chunk[1]])
        } else {
            u16::from_be_bytes([chunk[0], chunk[1]])
        }
    });
    char::decode_utf16(values)
        .collect::<Result<String, _>>()
        .map_err(|error| format!("无法按 UTF-16 解码 {} {path}: {error}", kind.label()))
}

fn decode_utf32(
    data: &[u8],
    little_endian: bool,
    kind: TextKind,
    path: &str,
) -> Result<String, String> {
    if !data.len().is_multiple_of(4) {
        return Err(format!("{} 的 UTF-32 字节长度无效: {path}", kind.label()));
    }
    data.chunks_exact(4)
        .map(|chunk| {
            let value = if little_endian {
                u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            } else {
                u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            };
            char::from_u32(value).ok_or_else(|| format!("UTF-32 包含无效码点 {value:#X}: {path}"))
        })
        .collect()
}

fn normalize_xml_encoding(text: &str) -> String {
    static XML_ENCODING: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)(<\?xml\b[^>]*\bencoding\s*=\s*[\"'])[^\"']*([\"'])"#)
            .expect("valid XML encoding regex")
    });
    XML_ENCODING.replace(text, "${1}UTF-8${2}").into_owned()
}

fn normalize_css_encoding(text: &str) -> String {
    static CSS_ENCODING: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)^(\s*@charset\s+)[\"'][^\"']+[\"'](\s*;)"#)
            .expect("valid CSS encoding regex")
    });
    CSS_ENCODING.replace(text, "${1}\"UTF-8\"${2}").into_owned()
}

fn normalize_html_encoding(text: &str) -> String {
    static HTML_CHARSET: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(<meta\b[^>]*\bcharset\s*=\s*[\"']?)[^\"'\s/>]+"#)
            .expect("valid HTML charset regex")
    });
    static HTML_CONTENT_TYPE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(<meta\b[^>]*\bcontent\s*=\s*[\"'][^\"']*?\bcharset\s*=\s*)[^\"';\s]+"#)
            .expect("valid HTML content-type charset regex")
    });
    let normalized = HTML_CHARSET.replace(text, "${1}UTF-8");
    HTML_CONTENT_TYPE
        .replace(&normalized, "${1}UTF-8")
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::{decode_epub_text, encode_epub_text, text_kind_for_path, TextKind};
    use encoding_rs::GBK;

    #[test]
    fn decodes_declared_gbk_xml_and_normalizes_its_declaration() {
        let (data, _, had_errors) =
            GBK.encode(r#"<?xml version="1.0" encoding="GBK"?><p>中文</p>"#);
        assert!(!had_errors);
        let text = decode_epub_text(&data, TextKind::Xml, "OPS/chapter.xhtml").unwrap();
        assert!(text.contains("中文"));
        assert_eq!(
            String::from_utf8(encode_epub_text(&text, TextKind::Xml)).unwrap(),
            r#"<?xml version="1.0" encoding="UTF-8"?><p>中文</p>"#
        );
    }

    #[test]
    fn decodes_declared_gbk_css_and_normalizes_its_charset() {
        let (data, _, had_errors) = GBK.encode("@charset \"gbk\";\n.title { content: \"中文\"; }");
        assert!(!had_errors);
        let text = decode_epub_text(&data, TextKind::Css, "OPS/style.css").unwrap();
        assert!(text.contains("中文"));
        assert!(String::from_utf8(encode_epub_text(&text, TextKind::Css))
            .unwrap()
            .starts_with("@charset \"UTF-8\";"));
    }

    #[test]
    fn decodes_declared_gbk_html_and_normalizes_its_meta_charset() {
        let (data, _, had_errors) = GBK.encode("<html><meta charset=gbk><p>中文</p></html>");
        assert!(!had_errors);
        let text = decode_epub_text(&data, TextKind::Html, "OPS/chapter.html").unwrap();
        assert!(text.contains("中文"));
        assert!(String::from_utf8(encode_epub_text(&text, TextKind::Html))
            .unwrap()
            .contains("charset=UTF-8"));
        assert_eq!(text_kind_for_path("OPS/chapter.html"), TextKind::Html);
    }

    #[test]
    fn keeps_valid_utf8_as_the_default_even_with_a_conflicting_declaration() {
        let data = r#"<?xml version="1.0" encoding="GBK"?><p>中文</p>"#.as_bytes();
        assert_eq!(
            decode_epub_text(data, TextKind::Xml, "OPS/chapter.xhtml").unwrap(),
            r#"<?xml version="1.0" encoding="GBK"?><p>中文</p>"#
        );
    }

    #[test]
    fn decodes_utf16_xml_with_a_bom() {
        let mut data = b"\xFF\xFE".to_vec();
        data.extend("<p>中文</p>".encode_utf16().flat_map(u16::to_le_bytes));
        assert_eq!(
            decode_epub_text(&data, TextKind::Xml, "OPS/chapter.xhtml").unwrap(),
            "<p>中文</p>"
        );
    }
}
