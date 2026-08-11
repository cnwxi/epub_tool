//! Shared Stylo-backed computed font styles for EPUB XHTML.

use blitz_dom::{DocumentConfig, StyleThreading};
use blitz_html::HtmlDocument;
use std::collections::BTreeMap;
use style::values::computed::font::{GenericFontFamily, SingleFontFamily};

const NODE_MARKER_ATTRIBUTE: &str = "data-epub-tool-node";

#[derive(Debug, Clone, PartialEq)]
pub struct FontRequest {
    pub families: Vec<String>,
    pub weight: f32,
    pub style: FontStyle,
    /// CSS font stretch as a ratio, where `1.0` is normal width.
    pub stretch: f32,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FontStyle {
    Normal,
    Italic,
    Oblique(f32),
}

/// Computed font properties keyed by stable markers added only to the
/// temporary DOM. The original XHTML is never serialized through an HTML
/// parser, so entity spelling and unrelated markup remain byte-for-byte intact.
#[derive(Debug, Default)]
pub struct ComputedFontMap {
    by_marker: BTreeMap<usize, FontRequest>,
}

impl ComputedFontMap {
    pub fn get(&self, marker: usize) -> Option<&FontRequest> {
        self.by_marker.get(&marker)
    }
}

/// Resolve author styles with Stylo and return the computed font request for
/// each element in source order.
pub fn compute_font_map(xhtml: &str, author_stylesheets: &[&str]) -> Result<ComputedFontMap, String> {
    let annotated = annotate_elements(xhtml)?;
    let document_source = inject_author_stylesheets(&annotated, author_stylesheets)?;
    let mut document = HtmlDocument::from_html(
        &document_source,
        DocumentConfig {
            style_threading: StyleThreading::Sequential,
            ..DocumentConfig::default()
        },
    );
    document.resolve_stylist(0.0);

    let mut by_marker = BTreeMap::new();
    for (_, node) in document.tree().iter() {
        let Some(marker) = node.attrs().and_then(|attributes| {
            attributes
                .iter()
                .find(|attribute| attribute.name.local.as_ref() == NODE_MARKER_ATTRIBUTE)
                .and_then(|attribute| attribute.value.parse::<usize>().ok())
        }) else {
            continue;
        };
        let styles = node
            .primary_styles()
            .ok_or_else(|| format!("Stylo 未计算 XHTML 元素样式: marker={marker}"))?;
        let font = styles.get_font();
        let family = font.clone_font_family();
        let families = family
            .families
            .iter()
            .map(font_family_name)
            .collect::<Vec<_>>();
        let computed_style = font.clone_font_style();
        let style = if computed_style == style::values::computed::FontStyle::NORMAL {
            FontStyle::Normal
        } else if computed_style == style::values::computed::FontStyle::ITALIC {
            FontStyle::Italic
        } else {
            FontStyle::Oblique(computed_style.oblique_degrees())
        };
        by_marker.insert(
            marker,
            FontRequest {
                families,
                weight: font.clone_font_weight().value(),
                style,
                stretch: font.clone_font_stretch().to_percentage().0,
            },
        );
    }
    Ok(ComputedFontMap { by_marker })
}

fn font_family_name(family: &SingleFontFamily) -> String {
    match family {
        SingleFontFamily::FamilyName(family) => family.name.as_ref().to_string(),
        SingleFontFamily::Generic(generic) => generic_family_name(*generic).to_string(),
    }
}

fn generic_family_name(family: GenericFontFamily) -> &'static str {
    match family {
        GenericFontFamily::None => "",
        GenericFontFamily::Serif => "serif",
        GenericFontFamily::SansSerif => "sans-serif",
        GenericFontFamily::Monospace => "monospace",
        GenericFontFamily::Cursive => "cursive",
        GenericFontFamily::Fantasy => "fantasy",
        GenericFontFamily::SystemUi => "system-ui",
    }
}

fn inject_author_stylesheets(xhtml: &str, stylesheets: &[&str]) -> Result<String, String> {
    if stylesheets.is_empty() {
        return Ok(xhtml.to_string());
    }
    if stylesheets
        .iter()
        .any(|stylesheet| stylesheet.to_ascii_lowercase().contains("</style"))
    {
        return Err("CSS 包含无法安全嵌入临时 Stylo DOM 的 </style>".to_string());
    }
    let mut style = String::from("<style data-epub-tool-styles=\"author\">\n");
    for stylesheet in stylesheets {
        style.push_str(stylesheet);
        style.push('\n');
    }
    style.push_str("</style>");

    if let Some(head_start) = find_start_tag(xhtml, "head")? {
        let head_end = tag_end(xhtml, head_start)?;
        let mut result = String::with_capacity(xhtml.len() + style.len());
        result.push_str(&xhtml[..head_end + 1]);
        result.push_str(&style);
        result.push_str(&xhtml[head_end + 1..]);
        Ok(result)
    } else {
        Ok(format!("{style}{xhtml}"))
    }
}

fn annotate_elements(source: &str) -> Result<String, String> {
    if source
        .to_ascii_lowercase()
        .contains(NODE_MARKER_ATTRIBUTE)
    {
        return Err(format!(
            "XHTML 已包含保留属性 {NODE_MARKER_ATTRIBUTE}"
        ));
    }
    let mut result = String::with_capacity(source.len() + source.len() / 16);
    let mut cursor = 0;
    let mut marker = 0;
    while let Some(offset) = source[cursor..].find('<') {
        let start = cursor + offset;
        result.push_str(&source[cursor..start]);
        if source[start..].starts_with("<!--") {
            let end = source[start + 4..]
                .find("-->")
                .map(|offset| start + 4 + offset + 3)
                .ok_or_else(|| "XHTML 注释未闭合".to_string())?;
            result.push_str(&source[start..end]);
            cursor = end;
            continue;
        }
        if source[start..].starts_with("<![CDATA[") {
            let end = source[start + 9..]
                .find("]]>")
                .map(|offset| start + 9 + offset + 3)
                .ok_or_else(|| "XHTML CDATA 未闭合".to_string())?;
            result.push_str(&source[start..end]);
            cursor = end;
            continue;
        }
        let end = tag_end(source, start)?;
        let tag = &source[start..=end];
        if tag.starts_with("</") || tag.starts_with("<!") || tag.starts_with("<?") {
            result.push_str(tag);
            cursor = end + 1;
            continue;
        }
        let insert_at = tag[..tag.len() - 1]
            .trim_end()
            .strip_suffix('/')
            .map_or(end, |without_slash| start + without_slash.len());
        result.push_str(&source[start..insert_at]);
        result.push_str(&format!(" {NODE_MARKER_ATTRIBUTE}=\"{marker}\""));
        result.push_str(&source[insert_at..=end]);
        marker += 1;
        cursor = end + 1;
    }
    result.push_str(&source[cursor..]);
    Ok(result)
}

fn find_start_tag(source: &str, target: &str) -> Result<Option<usize>, String> {
    let mut cursor = 0;
    while let Some(offset) = source[cursor..].find('<') {
        let start = cursor + offset;
        let end = tag_end(source, start)?;
        let tag = &source[start..=end];
        if !tag.starts_with("</") && !tag.starts_with("<!") && !tag.starts_with("<?") {
            let inner = tag[1..tag.len() - 1].trim_start();
            let name = inner
                .split(|character: char| character.is_ascii_whitespace() || character == '/')
                .next()
                .unwrap_or("");
            if name.eq_ignore_ascii_case(target) {
                return Ok(Some(start));
            }
        }
        cursor = end + 1;
    }
    Ok(None)
}

fn tag_end(source: &str, start: usize) -> Result<usize, String> {
    let bytes = source.as_bytes();
    let mut index = start + 1;
    let mut quote = None;
    while index < bytes.len() {
        match (quote, bytes[index]) {
            (Some(current), byte) if byte == current => quote = None,
            (None, b'\'' | b'"') => quote = Some(bytes[index]),
            (None, b'>') => return Ok(index),
            _ => {}
        }
        index += 1;
    }
    Err("XHTML 标签未闭合".to_string())
}

#[cfg(test)]
mod tests {
    use super::{compute_font_map, FontStyle};

    #[test]
    fn stylo_resolves_complex_selectors_inheritance_variables_and_important() {
        let xhtml = r#"<html><head></head><body class="root"><section><p>one</p><p><strong>two</strong></p></section></body></html>"#;
        let css = r#"
            .root { --book-font: "Book Face"; font-family: serif; }
            section > p:nth-child(2) { font: italic 650 16px var(--book-font), sans-serif; }
            p strong { font-family: "Wrong"; }
            section strong { font-family: var(--book-font), monospace !important; font-stretch: 125%; }
        "#;
        let map = compute_font_map(xhtml, &[css]).expect("Stylo styles");
        let strong = map.get(5).expect("strong marker");
        assert_eq!(strong.families, ["Book Face", "monospace"]);
        assert_eq!(strong.weight, 650.0);
        assert_eq!(strong.style, FontStyle::Italic);
        assert_eq!(strong.stretch, 1.25);
    }

    #[test]
    fn preserves_self_closing_markup_while_annotating_temporary_dom() {
        let xhtml = r#"<html><head/><body><img src="x"/><p>text</p></body></html>"#;
        let map = compute_font_map(xhtml, &["p { font-family: Target; }"])
            .expect("Stylo styles");
        assert_eq!(map.get(4).expect("paragraph").families[0], "Target");
    }
}
