//! Shared Stylo-backed computed font styles for EPUB XHTML.

use crate::rust_backend::text_encoding::{decode_epub_text, encode_epub_text, TextKind};
use blitz_dom::{node::SpecialElementData, DocumentConfig, StyleThreading};
use blitz_html::HtmlDocument;
use blitz_traits::net::{Bytes, NetHandler, NetProvider, Request};
use dom_query::Document as QueryDocument;
use std::{collections::BTreeMap, sync::Arc};
use style::font_face::{ComputedFontStyleDescriptor, FontStyle as FaceStyle, Source as FaceSource};
#[cfg(test)]
use style::shared_lock::SharedRwLockReadGuard;
#[cfg(test)]
use style::stylesheets::Origin;
use style::stylesheets::{CssRule, CustomMediaMap, FontFaceRule};
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

#[derive(Debug, Clone, PartialEq)]
pub struct FontFace {
    pub family: String,
    pub sources: Vec<String>,
    pub weight: (f32, f32),
    pub style: FontStyleRange,
    pub stretch: (f32, f32),
    pub unicode_ranges: Vec<(u32, u32)>,
    pub source_order: usize,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FontStyleRange {
    Normal,
    Italic,
    Oblique(f32, f32),
}

#[derive(Debug, Default, Clone)]
pub struct FontFaceResolver {
    faces: Vec<FontFace>,
}

pub(crate) struct ResolvedFont<'a> {
    pub family: &'a str,
    pub source: &'a str,
}

impl FontFaceResolver {
    pub fn new(faces: Vec<FontFace>) -> Self {
        Self { faces }
    }

    #[cfg(test)]
    pub fn resolve<'a>(
        &'a self,
        request: &FontRequest,
        character: char,
        source_exists: impl Fn(&str) -> bool,
    ) -> Option<&'a str> {
        self.resolve_match(request, character, source_exists)
            .map(|resolved| resolved.source)
    }

    pub(crate) fn resolve_match<'a>(
        &'a self,
        request: &FontRequest,
        character: char,
        source_exists: impl Fn(&str) -> bool,
    ) -> Option<ResolvedFont<'a>> {
        for family in &request.families {
            let normalized = normalize_font_family(family);
            let mut candidates = self
                .faces
                .iter()
                .filter(|face| normalize_font_family(&face.family) == normalized)
                .filter(|face| {
                    face.unicode_ranges.is_empty()
                        || face
                            .unicode_ranges
                            .iter()
                            .any(|(start, end)| (*start..=*end).contains(&u32::from(character)))
                })
                .collect::<Vec<_>>();
            candidates.sort_by(|left, right| {
                face_distance(left, request)
                    .partial_cmp(&face_distance(right, request))
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| right.source_order.cmp(&left.source_order))
            });
            for face in candidates {
                if let Some(source) = face.sources.iter().find(|source| source_exists(source)) {
                    return Some(ResolvedFont {
                        family: &face.family,
                        source,
                    });
                }
            }
        }
        None
    }
}

fn face_distance(face: &FontFace, request: &FontRequest) -> f32 {
    range_distance(request.stretch, face.stretch) * 1_000_000.0
        + style_distance(request.style, face.style) * 10_000.0
        + range_distance(request.weight, face.weight)
}

fn range_distance(value: f32, range: (f32, f32)) -> f32 {
    if value < range.0 {
        range.0 - value
    } else if value > range.1 {
        value - range.1
    } else {
        0.0
    }
}

fn style_distance(request: FontStyle, face: FontStyleRange) -> f32 {
    match (request, face) {
        (FontStyle::Normal, FontStyleRange::Normal)
        | (FontStyle::Italic, FontStyleRange::Italic) => 0.0,
        (FontStyle::Oblique(angle), FontStyleRange::Oblique(min, max)) => {
            range_distance(angle, (min, max))
        }
        (FontStyle::Italic, FontStyleRange::Oblique(_, _))
        | (FontStyle::Oblique(_), FontStyleRange::Italic) => 1.0,
        _ => 2.0,
    }
}

pub(crate) fn normalize_font_family(value: &str) -> String {
    value.trim().to_lowercase()
}

/// Computed font properties keyed by stable markers added only to the
/// temporary DOM. The original XHTML is never serialized through an HTML
/// parser, so entity spelling and unrelated markup remain byte-for-byte intact.
#[derive(Debug, Default, Clone)]
pub struct ComputedFontMap {
    by_marker: BTreeMap<usize, FontRequest>,
}

impl ComputedFontMap {
    pub fn get(&self, marker: usize) -> Option<&FontRequest> {
        self.by_marker.get(&marker)
    }

    #[cfg(test)]
    pub(crate) fn remove_marker(&mut self, marker: usize) {
        self.by_marker.remove(&marker);
    }
}

#[derive(Debug, Default, Clone)]
pub struct ComputedFontDocument {
    pub styles: ComputedFontMap,
    pub faces: Vec<FontFace>,
}

/// Resolve author styles with Stylo and return the computed font request for
/// each element in source order.
#[cfg(test)]
pub fn compute_font_map(
    xhtml: &str,
    author_stylesheets: &[&str],
) -> Result<ComputedFontMap, String> {
    let annotated = annotate_elements(xhtml)?;
    let document_source = inject_author_stylesheets(&annotated, author_stylesheets)?;
    compute_document_font_styles(
        &document_source,
        DocumentConfig {
            base_url: Some("https://epub-tool.invalid/".to_string()),
            style_threading: StyleThreading::Sequential,
            ..DocumentConfig::default()
        },
    )
    .map(|document| document.styles)
}

/// Resolve an EPUB document with only its linked and inline stylesheets. CSS
/// resources, including recursive `@import` requests, are served from the EPUB
/// member map without network or filesystem access.
pub fn compute_epub_font_document(
    xhtml: &str,
    document_member: &str,
    members: &BTreeMap<String, Vec<u8>>,
) -> Result<ComputedFontDocument, String> {
    let annotated = annotate_elements(xhtml)?;
    let document_source = prepare_epub_document_styles(&annotated);
    let stylesheets = members
        .iter()
        .filter(|(member, _)| member.to_ascii_lowercase().ends_with(".css"))
        .map(|(member, data)| {
            decode_epub_text(data, TextKind::Css, member)
                .map(|text| (member.clone(), encode_epub_text(&text, TextKind::Css)))
        })
        .collect::<Result<BTreeMap<_, _>, _>>()?;
    compute_document_font_styles(
        &document_source,
        DocumentConfig {
            base_url: Some(stylesheet_base_url(document_member)),
            net_provider: Some(Arc::new(EpubStylesheetProvider { stylesheets })),
            style_threading: StyleThreading::Sequential,
            ..DocumentConfig::default()
        },
    )
}

fn compute_document_font_styles(
    document_source: &str,
    config: DocumentConfig,
) -> Result<ComputedFontDocument, String> {
    let mut document = HtmlDocument::from_html(document_source, config);
    document.handle_messages();
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
        let Some(styles) = node.primary_styles() else {
            continue;
        };
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
    let faces = active_font_faces(&mut document)?;
    Ok(ComputedFontDocument {
        styles: ComputedFontMap { by_marker },
        faces,
    })
}

fn active_font_faces(document: &mut HtmlDocument) -> Result<Vec<FontFace>, String> {
    let stylesheets = document
        .tree()
        .iter()
        .filter_map(|(_, node)| {
            let element = node.element_data()?;
            match &element.special_data {
                SpecialElementData::Stylesheet(stylesheet) => Some(stylesheet.clone()),
                _ => None,
            }
        })
        .collect::<Vec<_>>();
    let lock = document.guard().clone();
    let guard = lock.read();
    let custom_media = CustomMediaMap::default();
    let device = document.stylist_device();
    let mut faces = Vec::new();
    for stylesheet in stylesheets {
        let contents = stylesheet.0.contents.read_with(&guard);
        for rule in contents.effective_rules(device, &custom_media, &guard) {
            let CssRule::FontFace(rule) = rule else {
                continue;
            };
            let rule = rule.read_with(&guard);
            if let Some(face) = font_face_from_rule(rule, faces.len()) {
                faces.push(face);
            }
        }
    }
    Ok(faces)
}

struct EpubStylesheetProvider {
    stylesheets: BTreeMap<String, Vec<u8>>,
}

impl NetProvider for EpubStylesheetProvider {
    fn fetch(&self, _doc_id: usize, request: Request, handler: Box<dyn NetHandler>) {
        let member =
            percent_encoding::percent_decode_str(request.url.path().trim_start_matches('/'))
                .decode_utf8()
                .ok()
                .map(|value| value.into_owned());
        let data = member
            .as_deref()
            .and_then(|member| self.stylesheets.get(member))
            .cloned()
            .unwrap_or_default();
        handler.bytes(request.url.to_string(), Bytes::from(data));
    }
}

fn prepare_epub_document_styles(source: &str) -> String {
    let document = QueryDocument::from(source);
    let links = document.select("link").nodes().to_vec();
    for link in links {
        let rel = link
            .attr("rel")
            .map(|value| value.to_string())
            .unwrap_or_default();
        let rel_tokens = rel
            .split_ascii_whitespace()
            .map(str::to_ascii_lowercase)
            .collect::<Vec<_>>();
        if !rel_tokens.iter().any(|token| token == "stylesheet") {
            continue;
        }
        if rel_tokens.iter().any(|token| token == "alternate") {
            link.set_attr("rel", "");
            continue;
        }
        let Some(media) = link.attr("media").map(|value| value.trim().to_string()) else {
            continue;
        };
        if media.is_empty() {
            continue;
        }
        let Some(href) = link.attr("href").map(|value| value.to_string()) else {
            continue;
        };
        link.rename("style");
        link.remove_attrs(&["rel", "href", "media"]);
        link.set_text(format!(
            "@import url(\"{}\") {media};",
            css_string_contents(&href)
        ));
    }

    let styles = document.select("style").nodes().to_vec();
    for style in styles {
        let Some(media) = style.attr("media").map(|value| value.trim().to_string()) else {
            continue;
        };
        if media.is_empty() {
            continue;
        }
        let css = style.text().to_string();
        style.remove_attr("media");
        style.set_text(format!("@media {media} {{\n{css}\n}}"));
    }
    document.html().to_string()
}

fn css_string_contents(value: &str) -> String {
    value
        .chars()
        .flat_map(|character| match character {
            '\\' => "\\\\".chars().collect::<Vec<_>>(),
            '"' => "\\\"".chars().collect(),
            '\n' => "\\A ".chars().collect(),
            '\r' => "\\D ".chars().collect(),
            character => vec![character],
        })
        .collect()
}

/// Parse `@font-face` descriptors with Stylo. URL sources are returned as
/// EPUB member paths resolved relative to the stylesheet member.
#[cfg(test)]
pub fn parse_font_faces(
    css: &str,
    stylesheet_member: &str,
    first_source_order: usize,
) -> Result<Vec<FontFace>, String> {
    let base_url = stylesheet_base_url(stylesheet_member);
    let document = HtmlDocument::from_html(
        "<html><head></head><body></body></html>",
        DocumentConfig {
            base_url: Some(base_url),
            style_threading: StyleThreading::Sequential,
            ..DocumentConfig::default()
        },
    );
    let stylesheet = document.make_stylesheet(css, Origin::Author);
    let guard = document.guard().read();
    let contents = stylesheet.0.contents.read_with(&guard);
    let rules = contents.rules.read_with(&guard);
    let mut faces = Vec::new();
    collect_font_faces(&rules.0, &guard, &mut faces)?;
    for (offset, face) in faces.iter_mut().enumerate() {
        face.source_order = first_source_order + offset;
    }
    Ok(faces)
}

#[cfg(test)]
fn collect_font_faces(
    rules: &[CssRule],
    guard: &SharedRwLockReadGuard<'_>,
    output: &mut Vec<FontFace>,
) -> Result<(), String> {
    for rule in rules {
        match rule {
            CssRule::FontFace(rule) => {
                let rule = rule.read_with(guard);
                if let Some(face) = font_face_from_rule(rule, output.len()) {
                    output.push(face);
                }
            }
            CssRule::Media(rule) => {
                collect_font_faces(&rule.rules.read_with(guard).0, guard, output)?
            }
            CssRule::Supports(rule) => {
                collect_font_faces(&rule.rules.read_with(guard).0, guard, output)?
            }
            CssRule::LayerBlock(rule) => {
                collect_font_faces(&rule.rules.read_with(guard).0, guard, output)?
            }
            CssRule::Scope(rule) => {
                collect_font_faces(&rule.rules.read_with(guard).0, guard, output)?
            }
            _ => {}
        }
    }
    Ok(())
}

fn font_face_from_rule(rule: &FontFaceRule, source_order: usize) -> Option<FontFace> {
    let descriptors = &rule.descriptors;
    let family = descriptors.font_family.as_ref()?;
    let sources = descriptors
        .src
        .as_ref()
        .map(|sources| {
            sources
                .0
                .iter()
                .filter_map(|source| match source {
                    FaceSource::Url(source) => epub_member_from_url(source.url.as_str()),
                    FaceSource::Local(_) => None,
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if sources.is_empty() {
        return None;
    }
    let weight = descriptors
        .font_weight
        .as_ref()
        .and_then(|range| range.compute())
        .map(|range| (range.0.value(), range.1.value()))
        .unwrap_or((400.0, 400.0));
    let stretch = descriptors
        .font_stretch
        .as_ref()
        .and_then(|range| range.compute())
        .map(|range| (range.0.to_percentage().0, range.1.to_percentage().0))
        .unwrap_or((1.0, 1.0));
    let style = descriptors
        .font_style
        .as_ref()
        .and_then(FaceStyle::compute)
        .map(|style| match style {
            ComputedFontStyleDescriptor::Italic => FontStyleRange::Italic,
            ComputedFontStyleDescriptor::Oblique(min, max) => {
                let min = min.to_float();
                let max = max.to_float();
                if min == 0.0 && max == 0.0 {
                    FontStyleRange::Normal
                } else {
                    FontStyleRange::Oblique(min, max)
                }
            }
        })
        .unwrap_or(FontStyleRange::Normal);
    let unicode_ranges = descriptors
        .unicode_range
        .as_ref()
        .map(|ranges| {
            ranges
                .iter()
                .map(|range| (range.start, range.end))
                .collect()
        })
        .unwrap_or_default();
    Some(FontFace {
        family: family.name.as_ref().to_string(),
        sources,
        weight,
        style,
        stretch,
        unicode_ranges,
        source_order,
    })
}

fn stylesheet_base_url(member: &str) -> String {
    let encoded = member
        .split('/')
        .map(|part| {
            percent_encoding::utf8_percent_encode(part, percent_encoding::NON_ALPHANUMERIC)
                .to_string()
        })
        .collect::<Vec<_>>()
        .join("/");
    format!("https://epub-tool.invalid/{encoded}")
}

fn epub_member_from_url(url: &str) -> Option<String> {
    let path = url
        .strip_prefix("https://epub-tool.invalid/")?
        .split(['?', '#'])
        .next()?;
    let decoded = percent_encoding::percent_decode_str(path)
        .decode_utf8()
        .ok()?
        .into_owned();
    (!decoded.is_empty()).then(|| decoded.trim_start_matches('/').to_string())
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

#[cfg(test)]
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
    if source.to_ascii_lowercase().contains(NODE_MARKER_ATTRIBUTE) {
        return Err(format!("XHTML 已包含保留属性 {NODE_MARKER_ATTRIBUTE}"));
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

#[cfg(test)]
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
    use super::{
        compute_epub_font_document, compute_font_map, parse_font_faces, FontFaceResolver,
        FontRequest, FontStyle, FontStyleRange,
    };
    use std::collections::BTreeMap;

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
        let strong = map.get(6).expect("strong marker");
        assert_eq!(strong.families, ["Book Face", "monospace"]);
        assert_eq!(strong.weight, 900.0);
        assert_eq!(strong.style, FontStyle::Italic);
        assert_eq!(strong.stretch, 1.25);
    }

    #[test]
    fn preserves_self_closing_markup_while_annotating_temporary_dom() {
        let xhtml = r#"<html><head/><body><img src="x"/><p>text</p></body></html>"#;
        let map = compute_font_map(xhtml, &["p { font-family: Target; }"]).expect("Stylo styles");
        assert_eq!(map.get(4).expect("paragraph").families[0], "Target");
    }

    #[test]
    fn stylo_parses_font_faces_and_resolver_honors_descriptors_and_source_order() {
        let css = r#"
            @font-face {
                font-family: Book;
                src: local("Ignored"), url("../Fonts/regular.woff2") format("woff2"), url(../Fonts/regular.ttf);
                font-weight: 300 500;
                font-style: normal;
                font-stretch: 75% 100%;
                unicode-range: U+4E00-9FFF;
            }
            @font-face {
                font-family: Book;
                src: url(../Fonts/bold.otf);
                font-weight: 700;
                font-style: italic;
            }
        "#;
        let faces = parse_font_faces(css, "OEBPS/Styles/book.css", 10).expect("font faces");
        assert_eq!(faces.len(), 2);
        assert_eq!(
            faces[0].sources,
            ["OEBPS/Fonts/regular.woff2", "OEBPS/Fonts/regular.ttf"]
        );
        assert_eq!(faces[0].weight, (300.0, 500.0));
        assert_eq!(faces[0].stretch, (0.75, 1.0));
        assert_eq!(faces[0].unicode_ranges, [(0x4E00, 0x9FFF)]);
        assert_eq!(faces[1].style, FontStyleRange::Italic);

        let resolver = FontFaceResolver::new(faces);
        let request = FontRequest {
            families: vec!["Book".to_string(), "serif".to_string()],
            weight: 700.0,
            style: FontStyle::Italic,
            stretch: 1.0,
        };
        assert_eq!(
            resolver.resolve(&request, '字', |_| true),
            Some("OEBPS/Fonts/bold.otf")
        );
    }

    #[test]
    fn epub_stylesheets_follow_document_scope_imports_order_and_screen_media() {
        let xhtml = r#"<html><head>
            <link rel="stylesheet" href="../Styles/block.css"/>
            <link rel="stylesheet" href="../Styles/target.css"/>
            <link rel="stylesheet" href="../Styles/print.css" media="print"/>
            <link rel="alternate stylesheet" href="../Styles/alternate.css"/>
            <style media="print">.target { font-family: PrintInline !important; }</style>
        </head><body><p class="target">text</p></body></html>"#;
        let members = BTreeMap::from([
            (
                "OPS/Styles/block.css".to_string(),
                b".target { font-family: Block; }".to_vec(),
            ),
            (
                "OPS/Styles/target.css".to_string(),
                b"@import url(\"imported.css\"); @font-face { font-family: Target; src: url(../Fonts/target.ttf); } .target { font-family: Target; }".to_vec(),
            ),
            (
                "OPS/Styles/imported.css".to_string(),
                b".target { font-family: Imported; }".to_vec(),
            ),
            (
                "OPS/Styles/print.css".to_string(),
                b"@font-face { font-family: Print; src: url(../Fonts/print.ttf); } .target { font-family: Print !important; }".to_vec(),
            ),
            (
                "OPS/Styles/alternate.css".to_string(),
                b"@font-face { font-family: Alternate; src: url(../Fonts/alternate.ttf); } .target { font-family: Alternate !important; }".to_vec(),
            ),
            (
                "OPS/Styles/unused.css".to_string(),
                b"@font-face { font-family: Target; src: url(../Fonts/unused.ttf); } .target { font-family: Unused !important; }".to_vec(),
            ),
        ]);

        let document = compute_epub_font_document(xhtml, "OPS/Text/chapter.xhtml", &members)
            .expect("EPUB Stylo styles");
        assert_eq!(
            document.styles.get(8).expect("paragraph").families[0],
            "Target"
        );
        assert_eq!(
            document
                .faces
                .iter()
                .map(|face| face.family.as_str())
                .collect::<Vec<_>>(),
            ["Target"]
        );
        assert_eq!(
            document.faces[0].sources,
            ["OPS/Fonts/target.ttf"],
            "unlinked stylesheets must not contribute font faces"
        );
    }

    #[test]
    fn resolver_uses_all_faces_and_falls_through_missing_sources() {
        let css = r#"
            @font-face { font-family: Book; src: url(missing.woff2), url(regular.ttf); font-weight: 400; unicode-range: U+4E00-9FFF; }
            @font-face { font-family: Book; src: url(bold.woff2); font-weight: 700; unicode-range: U+4E00-9FFF; }
            @font-face { font-family: Book; src: url(latin.otf); font-weight: 700; unicode-range: U+0000-00FF; }
        "#;
        let resolver = FontFaceResolver::new(
            parse_font_faces(css, "OPS/Fonts/faces.css", 0).expect("font faces"),
        );
        let available = [
            "OPS/Fonts/regular.ttf",
            "OPS/Fonts/bold.woff2",
            "OPS/Fonts/latin.otf",
        ];
        let bold = FontRequest {
            families: vec!["Book".to_string()],
            weight: 700.0,
            style: FontStyle::Normal,
            stretch: 1.0,
        };
        let regular = FontRequest {
            weight: 400.0,
            ..bold.clone()
        };
        assert_eq!(
            resolver.resolve(&bold, '字', |source| available.contains(&source)),
            Some("OPS/Fonts/bold.woff2")
        );
        assert_eq!(
            resolver.resolve(&regular, '字', |source| available.contains(&source)),
            Some("OPS/Fonts/regular.ttf")
        );
        assert_eq!(
            resolver.resolve(&bold, 'A', |source| available.contains(&source)),
            Some("OPS/Fonts/latin.otf")
        );
    }
}
