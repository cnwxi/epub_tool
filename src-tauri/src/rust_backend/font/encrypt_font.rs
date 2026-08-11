//! Shared EPUB font encryption planning and XHTML rewriting.

use super::{
    font_cmap::{rewrite_unicode_cmap, unicode_cmap},
    font_obfuscation::{build_obfuscation_mapping, html_entity_mapping, split_obfuscation_text},
    font_style::{
        compute_epub_font_document, normalize_font_family, ComputedFontMap, FontFace,
        FontFaceResolver, FontRequest,
    },
};
use crate::rust_backend::text_encoding::{
    decode_epub_text, encode_epub_text, text_kind_for_path, TextKind,
};
use crate::rust_backend::{epub::EpubWorkspace, EpubTask, TaskOutcome, TaskUpdate};
use crate::task_types::{FontTaskOptions, TaskOptions, TaskType};
use rand::Rng;
use regex::Regex;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
    sync::LazyLock,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObfuscatedFont {
    /// Rewritten font data with its Unicode cmap rebound to obfuscation glyphs.
    pub data: Vec<u8>,
    /// Source XHTML character to hexadecimal HTML entity replacements.
    pub html_replacements: BTreeMap<char, String>,
    /// Source characters actually present in the font and rebound in its cmap.
    pub obfuscated_text: String,
    /// Punctuation and symbols retained at their original codepoints.
    pub passthrough_text: String,
}

/// Native `encrypt_font` implementation backed by the shared Stylo font plan.
pub struct EncryptFontTask;

#[derive(Debug, Clone)]
pub(crate) struct FontEncryptionPlan {
    available_font_paths: BTreeSet<String>,
    target_fonts: BTreeSet<String>,
    target_families: BTreeSet<String>,
    xhtml_members: Vec<String>,
    computed_fonts: BTreeMap<String, ComputedFontMap>,
    font_resolvers: BTreeMap<String, FontFaceResolver>,
}

#[derive(Debug, Clone)]
struct ElementContext {
    name: String,
    font_request: Option<FontRequest>,
    rewrites_text: bool,
}

impl EpubTask for EncryptFontTask {
    fn task_type(&self) -> TaskType {
        TaskType::EncryptFont
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        options.font().is_some()
    }

    fn process(
        &self,
        input: &Path,
        workspace: &mut EpubWorkspace,
        options: &TaskOptions,
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String> {
        let plan = FontEncryptionPlan::build(workspace, input, options)?;
        let text_by_font = plan.collect_target_text(workspace)?;

        let mut replacements = BTreeMap::<String, BTreeMap<char, char>>::new();
        let mut rng = rand::thread_rng();
        for font_path in plan.target_fonts() {
            let Some(font_data) = workspace.members.get(font_path) else {
                return Err(format!("EPUB 缺少目标字体: {font_path}"));
            };
            let text = text_by_font.get(font_path).map_or("", String::as_str);
            let obfuscated = obfuscate_font_data(font_data, text, &mut rng)?;
            if obfuscated.html_replacements.is_empty() {
                continue;
            }
            let mapping = obfuscated
                .html_replacements
                .into_iter()
                .map(|(source, entity)| Ok((source, entity_target_char(&entity)?)))
                .collect::<Result<BTreeMap<_, _>, String>>()?;
            workspace.members.insert(font_path.clone(), obfuscated.data);
            replacements.insert(font_path.clone(), mapping);
        }

        for member in plan.xhtml_members() {
            let source = text_member(workspace, member, "XHTML")?;
            let rewritten = plan.rewrite_xhtml(member, &source, &replacements)?;
            workspace.members.insert(
                member.clone(),
                encode_epub_text(&rewritten, text_kind_for_path(member)),
            );
        }
        update(TaskUpdate::message(format!(
            "Rust 字体加密完成：处理 {} 个字体、{} 个 XHTML 文件。",
            replacements.len(),
            plan.xhtml_members().len()
        )));
        Ok(TaskOutcome::Success)
    }
}

impl FontEncryptionPlan {
    pub(crate) fn build(
        workspace: &EpubWorkspace,
        input: &Path,
        options: &TaskOptions,
    ) -> Result<Self, String> {
        Self::build_for_font_formats(workspace, selected_families(input, options)?, false, false)
    }

    pub(crate) fn build_for_decryption(
        workspace: &EpubWorkspace,
        input: &Path,
        options: &TaskOptions,
    ) -> Result<Self, String> {
        Self::build_for_font_formats(workspace, selected_families(input, options)?, true, false)
    }

    pub(crate) fn build_for_target_scan(workspace: &EpubWorkspace) -> Result<Self, String> {
        Self::build_for_font_formats(workspace, None, true, true)
    }

    fn build_for_font_formats(
        workspace: &EpubWorkspace,
        requested_families: Option<BTreeSet<String>>,
        tolerate_missing_fonts: bool,
        allow_empty_xhtml: bool,
    ) -> Result<Self, String> {
        for (member, data) in &workspace.members {
            if !member.to_ascii_lowercase().ends_with(".css") {
                continue;
            }
            decode_epub_text(data, TextKind::Css, member)?;
        }
        let xhtml_members: Vec<_> = workspace
            .members
            .keys()
            .filter(|member| {
                let lower = member.to_ascii_lowercase();
                lower.ends_with(".xhtml") || lower.ends_with(".html") || lower.ends_with(".htm")
            })
            .cloned()
            .collect();
        if xhtml_members.is_empty() && !allow_empty_xhtml {
            return Err("EPUB 没有 XHTML 文件，当前 Rust 实现暂不支持".to_string());
        }
        let mut computed_fonts = BTreeMap::new();
        let mut font_resolvers = BTreeMap::new();
        let mut active_faces = Vec::<FontFace>::new();
        for member in &xhtml_members {
            let source = text_member(workspace, member, "XHTML")?;
            let document = compute_epub_font_document(&source, member, &workspace.members)?;
            active_faces.extend(document.faces.iter().cloned());
            computed_fonts.insert(member.clone(), document.styles);
            font_resolvers.insert(member.clone(), FontFaceResolver::new(document.faces));
        }
        let available_font_paths = active_faces
            .iter()
            .flat_map(|face| face.sources.iter())
            .filter(|source| workspace.members.contains_key(*source))
            .cloned()
            .collect::<BTreeSet<_>>();
        let mut target_fonts = BTreeSet::new();
        let mut target_families = BTreeSet::new();
        for face in &active_faces {
            let family = normalize_font_family(&face.family);
            let is_requested = requested_families
                .as_ref()
                .is_none_or(|requested| requested.contains(&family));
            if !is_requested {
                continue;
            }
            let Some(font_path) = face
                .sources
                .iter()
                .find(|source| available_font_paths.contains(*source))
            else {
                if tolerate_missing_fonts {
                    continue;
                }
                return Err(format!(
                    "@font-face 引用的字体不存在: {}",
                    face.sources.join(", ")
                ));
            };
            if !is_supported_font_path(font_path) {
                return Err(format!("Rust 字体任务暂不支持该格式: {font_path}"));
            }
            target_fonts.insert(font_path.clone());
            target_families.insert(family);
        }
        Ok(Self {
            available_font_paths,
            target_fonts,
            target_families,
            xhtml_members,
            computed_fonts,
            font_resolvers,
        })
    }

    pub(crate) fn collect_target_text(
        &self,
        workspace: &EpubWorkspace,
    ) -> Result<BTreeMap<String, String>, String> {
        let mut text_by_font = BTreeMap::<String, String>::new();
        for member in &self.xhtml_members {
            let source = text_member(workspace, member, "XHTML")?;
            transform_xhtml(
                &source,
                self.computed_font_map(member)?,
                self.font_resolver(member)?,
                &self.available_font_paths,
                &BTreeMap::new(),
                None,
                |font: &str, _family: &str, text: &str| {
                    if self.target_fonts.contains(font) {
                        text_by_font
                            .entry(font.to_string())
                            .or_default()
                            .push_str(text);
                    }
                },
            )?;
        }
        Ok(text_by_font)
    }

    pub(crate) fn used_target_families(
        &self,
        workspace: &EpubWorkspace,
    ) -> Result<BTreeSet<String>, String> {
        let mut families = BTreeSet::new();
        for member in &self.xhtml_members {
            let source = text_member(workspace, member, "XHTML")?;
            transform_xhtml(
                &source,
                self.computed_font_map(member)?,
                self.font_resolver(member)?,
                &self.available_font_paths,
                &BTreeMap::new(),
                None,
                |font, family, _text| {
                    if self.target_fonts.contains(font) {
                        families.insert(family.to_string());
                    }
                },
            )?;
        }
        Ok(families)
    }

    pub(crate) fn target_fonts(&self) -> &BTreeSet<String> {
        &self.target_fonts
    }

    pub(crate) fn target_families(&self) -> &BTreeSet<String> {
        &self.target_families
    }

    pub(crate) fn xhtml_members(&self) -> &[String] {
        &self.xhtml_members
    }

    pub(crate) fn rewrite_xhtml(
        &self,
        member: &str,
        source: &str,
        replacements: &BTreeMap<String, BTreeMap<char, char>>,
    ) -> Result<String, String> {
        transform_xhtml(
            source,
            self.computed_font_map(member)?,
            self.font_resolver(member)?,
            &self.available_font_paths,
            replacements,
            None,
            |_, _, _| {},
        )
    }

    pub(crate) fn rewrite_xhtml_with_ocr_failures(
        &self,
        member: &str,
        source: &str,
        replacements: &BTreeMap<String, BTreeMap<char, char>>,
        failure_markup: &BTreeMap<String, BTreeMap<char, String>>,
    ) -> Result<String, String> {
        transform_xhtml(
            source,
            self.computed_font_map(member)?,
            self.font_resolver(member)?,
            &self.available_font_paths,
            replacements,
            Some(failure_markup),
            |_, _, _| {},
        )
    }

    fn computed_font_map(&self, member: &str) -> Result<&ComputedFontMap, String> {
        self.computed_fonts
            .get(member)
            .ok_or_else(|| format!("缺少 XHTML 的 Stylo 计算样式: {member}"))
    }

    fn font_resolver(&self, member: &str) -> Result<&FontFaceResolver, String> {
        self.font_resolvers
            .get(member)
            .ok_or_else(|| format!("缺少 XHTML 的 Stylo 字体 face: {member}"))
    }
}

fn is_supported_font_path(path: &str) -> bool {
    let path = path.to_ascii_lowercase();
    [".ttf", ".otf", ".woff", ".woff2"]
        .iter()
        .any(|extension| path.ends_with(extension))
}

fn selected_families(
    input: &Path,
    options: &TaskOptions,
) -> Result<Option<BTreeSet<String>>, String> {
    let FontTaskOptions {
        target_font_families_by_file,
        target_font_families,
        ..
    } = options
        .font()
        .ok_or_else(|| "字体任务需要 font options".to_string())?;
    let input_text = input.to_string_lossy();
    let families = target_font_families_by_file
        .get(input_text.as_ref())
        .or_else(|| {
            let canonical_input = std::fs::canonicalize(input).ok()?;
            target_font_families_by_file
                .iter()
                .find(|(source, _)| {
                    std::fs::canonicalize(source).ok().as_ref() == Some(&canonical_input)
                })
                .map(|(_, families)| families)
        })
        .unwrap_or(target_font_families);
    if families.is_empty() {
        return Ok(None);
    }
    families
        .iter()
        .map(|value| {
            let value = normalize_font_family(value);
            (!value.is_empty())
                .then_some(value)
                .ok_or_else(|| "目标字体 family 必须是非空字符串".to_string())
        })
        .collect::<Result<BTreeSet<_>, _>>()
        .map(Some)
}

fn text_member(workspace: &EpubWorkspace, member: &str, label: &str) -> Result<String, String> {
    let data = workspace
        .members
        .get(member)
        .ok_or_else(|| format!("EPUB 缺少 {label} 文件: {member}"))?;
    decode_epub_text(data, text_kind_for_path(member), member)
}

fn entity_target_char(entity: &str) -> Result<char, String> {
    let codepoint = entity
        .strip_prefix("&#x")
        .and_then(|value| u32::from_str_radix(value.trim_end_matches(';'), 16).ok())
        .ok_or_else(|| format!("无效 HTML 混淆实体: {entity}"))?;
    char::from_u32(codepoint).ok_or_else(|| format!("无效 HTML 混淆码位: {entity}"))
}

fn transform_xhtml(
    source: &str,
    computed_fonts: &ComputedFontMap,
    font_resolver: &FontFaceResolver,
    available_font_paths: &BTreeSet<String>,
    replacements: &BTreeMap<String, BTreeMap<char, char>>,
    failure_markup: Option<&BTreeMap<String, BTreeMap<char, String>>>,
    mut visit_text: impl FnMut(&str, &str, &str),
) -> Result<String, String> {
    static HTML_ENTITY: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9._-]*);")
            .expect("literal entity regex")
    });
    let mut result = String::with_capacity(source.len());
    let mut stack = Vec::<ElementContext>::new();
    let mut cursor = 0;
    let mut marker = 0;
    while cursor < source.len() {
        let next_tag = source[cursor..].find('<').map(|offset| cursor + offset);
        let text_end = next_tag.unwrap_or(source.len());
        let text = &source[cursor..text_end];
        if let Some(context) = stack.last() {
            if context.rewrites_text {
                let request = context
                    .font_request
                    .as_ref()
                    .ok_or_else(|| format!("Stylo 未返回 <{}> 的计算字体样式", context.name))?;
                result.push_str(&rewrite_text_with_resolver(
                    text,
                    request,
                    font_resolver,
                    available_font_paths,
                    replacements,
                    failure_markup,
                    &HTML_ENTITY,
                    &mut visit_text,
                ));
            } else {
                result.push_str(text);
            }
        } else {
            result.push_str(text);
        }
        if text_end == source.len() {
            break;
        }
        if source[text_end..].starts_with("<!--") {
            let end = terminated_markup_end(source, text_end, "-->", "XHTML 注释")?;
            result.push_str(&source[text_end..end]);
            cursor = end;
            continue;
        }
        if source[text_end..].starts_with("<![CDATA[") {
            let end = terminated_markup_end(source, text_end, "]]>", "XHTML CDATA")?;
            result.push_str(&source[text_end..end]);
            cursor = end;
            continue;
        }
        let end = tag_end(source, text_end)?;
        let tag = &source[text_end..=end];
        if tag.starts_with("<?") || tag.starts_with("<!") {
            result.push_str(tag);
            cursor = end + 1;
            continue;
        }
        if tag.starts_with("</") {
            let name = tag_name(&tag[2..tag.len() - 1])?;
            let context = stack
                .pop()
                .ok_or_else(|| "XHTML 出现多余闭合标签".to_string())?;
            if context.name != name {
                return Err(format!("XHTML 标签未正确嵌套: </{name}>"));
            }
            result.push_str(tag);
            cursor = end + 1;
            continue;
        }
        let inner = &tag[1..tag.len() - 1];
        let name = tag_name(inner.trim_end_matches('/').trim_end())?;
        let empty = inner.trim_end().ends_with('/') || is_void_element(&name);
        let current_marker = marker;
        marker += 1;
        if !empty {
            let font_request = computed_fonts.get(current_marker).cloned();
            stack.push(ElementContext {
                rewrites_text: !is_non_rendered_text_element(&name),
                name,
                font_request,
            });
        }
        result.push_str(tag);
        cursor = end + 1;
    }
    if !stack.is_empty() {
        return Err("XHTML 标签未闭合".to_string());
    }
    Ok(result)
}

#[allow(clippy::too_many_arguments)]
fn rewrite_text_with_resolver(
    text: &str,
    request: &FontRequest,
    resolver: &FontFaceResolver,
    available_font_paths: &BTreeSet<String>,
    replacements: &BTreeMap<String, BTreeMap<char, char>>,
    failure_markup: Option<&BTreeMap<String, BTreeMap<char, String>>>,
    entity: &Regex,
    visit_text: &mut impl FnMut(&str, &str, &str),
) -> String {
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for matched in entity.find_iter(text) {
        rewrite_resolved_fragment(
            &mut result,
            &text[cursor..matched.start()],
            request,
            resolver,
            available_font_paths,
            replacements,
            failure_markup,
            visit_text,
        );
        result.push_str(matched.as_str());
        cursor = matched.end();
    }
    rewrite_resolved_fragment(
        &mut result,
        &text[cursor..],
        request,
        resolver,
        available_font_paths,
        replacements,
        failure_markup,
        visit_text,
    );
    result
}

#[allow(clippy::too_many_arguments)]
fn rewrite_resolved_fragment(
    output: &mut String,
    text: &str,
    request: &FontRequest,
    resolver: &FontFaceResolver,
    available_font_paths: &BTreeSet<String>,
    replacements: &BTreeMap<String, BTreeMap<char, char>>,
    failure_markup: Option<&BTreeMap<String, BTreeMap<char, String>>>,
    visit_text: &mut impl FnMut(&str, &str, &str),
) {
    for character in text.chars() {
        let resolved = resolver.resolve_match(request, character, |source| {
            available_font_paths.contains(source)
        });
        let Some(resolved) = resolved else {
            output.push(character);
            continue;
        };
        let mut visible = String::new();
        visible.push(character);
        visit_text(resolved.source, resolved.family, &visible);
        if let Some(markup) = failure_markup
            .and_then(|failures| failures.get(resolved.source))
            .and_then(|failures| failures.get(&character))
        {
            output.push_str(markup);
        } else {
            output.push(
                replacements
                    .get(resolved.source)
                    .and_then(|mapping| mapping.get(&character))
                    .copied()
                    .unwrap_or(character),
            );
        }
    }
}

fn terminated_markup_end(
    source: &str,
    start: usize,
    terminator: &str,
    label: &str,
) -> Result<usize, String> {
    source[start..]
        .find(terminator)
        .map(|offset| start + offset + terminator.len())
        .ok_or_else(|| format!("{label}未闭合"))
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

fn tag_name(value: &str) -> Result<String, String> {
    let name = value
        .trim_start()
        .split(|character: char| character.is_ascii_whitespace() || character == '/')
        .next()
        .unwrap_or("");
    (!name.is_empty()
        && name.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, ':' | '-' | '_')
        }))
    .then(|| name.to_ascii_lowercase())
    .ok_or_else(|| "XHTML 标签名无效".to_string())
}

fn is_void_element(name: &str) -> bool {
    matches!(
        name,
        "area"
            | "base"
            | "br"
            | "col"
            | "embed"
            | "hr"
            | "img"
            | "input"
            | "link"
            | "meta"
            | "param"
            | "source"
            | "track"
            | "wbr"
    )
}

/// Text in document metadata and stylesheet/script source is not rendered by
/// the EPUB reading surface, so it must not participate in font glyph mapping.
fn is_non_rendered_text_element(name: &str) -> bool {
    matches!(name, "script" | "style" | "title")
}

/// Moves glyph bindings from source text codepoints to generated obfuscation
/// codepoints while leaving all non-cmap font tables untouched.
pub fn rewrite_cmap_for_obfuscation(
    font_data: &[u8],
    replacements: &BTreeMap<u32, u16>,
    source_text: &str,
) -> Result<Vec<u8>, String> {
    let removed_codepoints: Vec<u32> = source_text.chars().map(u32::from).collect();
    rewrite_unicode_cmap(font_data, replacements, &removed_codepoints)
}

/// Rebinds a single font's cmap and returns the matching XHTML replacement
/// plan. Characters absent from the font cmap are ignored.
pub fn obfuscate_font_data<R: Rng + ?Sized>(
    font_data: &[u8],
    text: &str,
    rng: &mut R,
) -> Result<ObfuscatedFont, String> {
    let cmap = unicode_cmap(font_data)?;
    let cleaned = split_obfuscation_text(text);
    let obfuscated_text = filter_text_in_cmap(&cleaned.obfuscate, &cmap);
    let passthrough_text = filter_text_in_cmap(&cleaned.passthrough, &cmap);
    if obfuscated_text.is_empty() {
        return Ok(ObfuscatedFont {
            data: font_data.to_vec(),
            html_replacements: BTreeMap::new(),
            obfuscated_text,
            passthrough_text,
        });
    }
    let codepoints = build_obfuscation_mapping(&obfuscated_text, &passthrough_text, rng)?;
    let replacements = codepoints
        .iter()
        .map(|(source, target)| {
            let glyph = cmap
                .get(source)
                .copied()
                .ok_or_else(|| format!("字体 cmap 缺少 U+{source:04X}"))?;
            Ok((*target, glyph))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    let data = rewrite_cmap_for_obfuscation(font_data, &replacements, &obfuscated_text)?;
    Ok(ObfuscatedFont {
        data,
        html_replacements: html_entity_mapping(&codepoints),
        obfuscated_text,
        passthrough_text,
    })
}

fn filter_text_in_cmap(text: &str, cmap: &BTreeMap<u32, u16>) -> String {
    text.chars()
        .filter(|character| cmap.contains_key(&u32::from(*character)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::{obfuscate_font_data, rewrite_cmap_for_obfuscation, transform_xhtml};
    use crate::rust_backend::font::font_style::{
        compute_font_map, parse_font_faces, FontFaceResolver,
    };
    use rand::{rngs::StdRng, SeedableRng};
    use std::collections::{BTreeMap, BTreeSet};

    #[test]
    fn rejects_non_font_input() {
        assert!(rewrite_cmap_for_obfuscation(b"not-a-font", &BTreeMap::new(), "你").is_err());
    }

    #[test]
    fn rejects_non_font_input_before_building_an_encryption_plan() {
        let mut rng = StdRng::seed_from_u64(1);
        assert!(obfuscate_font_data(b"not-a-font", "你好", &mut rng).is_err());
    }

    #[test]
    fn stylo_rewrite_preserves_comments_cdata_entities_and_script_text() {
        let xhtml = r#"<html><head></head><body><p>甲<!-- > 甲 --><![CDATA[甲]]><span>甲</span>&#x7532;</p><script>甲</script></body></html>"#;
        let css = r#"
            @font-face { font-family: Target; src: url(../Fonts/target.ttf); }
            p, span, script { font-family: Target; }
        "#;
        let computed = compute_font_map(xhtml, &[css]).expect("Stylo styles");
        let resolver = FontFaceResolver::new(
            parse_font_faces(css, "OPS/Styles/style.css", 0).expect("font faces"),
        );
        let path = "OPS/Fonts/target.ttf".to_string();
        let available = BTreeSet::from([path.clone()]);
        let replacements = BTreeMap::from([(path, BTreeMap::from([('甲', '乙')]))]);

        let rewritten = transform_xhtml(
            xhtml,
            &computed,
            &resolver,
            &available,
            &replacements,
            None,
            |_, _, _| {},
        )
        .expect("rewrite XHTML");
        assert_eq!(
            rewritten,
            r#"<html><head></head><body><p>乙<!-- > 甲 --><![CDATA[甲]]><span>乙</span>&#x7532;</p><script>甲</script></body></html>"#
        );
    }

    #[test]
    fn stylo_rewrite_skips_title_without_a_computed_style() {
        let xhtml = r#"<html><head><title>Title</title></head><body><p>甲</p></body></html>"#;
        let css = r#"
            @font-face { font-family: Target; src: url(../Fonts/target.ttf); }
            p { font-family: Target; }
        "#;
        let mut computed = compute_font_map(xhtml, &[css]).expect("Stylo styles");
        computed.remove_marker(2);
        let resolver = FontFaceResolver::new(
            parse_font_faces(css, "OPS/Styles/style.css", 0).expect("font faces"),
        );
        let path = "OPS/Fonts/target.ttf".to_string();
        let available = BTreeSet::from([path.clone()]);
        let replacements = BTreeMap::from([(path, BTreeMap::from([('甲', '乙')]))]);

        let rewritten = transform_xhtml(
            xhtml,
            &computed,
            &resolver,
            &available,
            &replacements,
            None,
            |_, _, _| {},
        )
        .expect("title metadata does not need a computed font style");

        assert_eq!(
            rewritten,
            r#"<html><head><title>Title</title></head><body><p>乙</p></body></html>"#
        );
    }

    #[test]
    fn stylo_rewrite_rejects_missing_body_style_without_manual_fallback() {
        let xhtml = r#"<html><head><title>Title</title></head><body><p>甲</p></body></html>"#;
        let css = r#"
            @font-face { font-family: Target; src: url(../Fonts/target.ttf); }
            p { font-family: Target; }
        "#;
        let mut computed = compute_font_map(xhtml, &[css]).expect("Stylo styles");
        computed.remove_marker(4);
        let resolver = FontFaceResolver::new(
            parse_font_faces(css, "OPS/Styles/style.css", 0).expect("font faces"),
        );
        let path = "OPS/Fonts/target.ttf".to_string();
        let available = BTreeSet::from([path.clone()]);
        let replacements = BTreeMap::from([(path, BTreeMap::from([('甲', '乙')]))]);

        let error = transform_xhtml(
            xhtml,
            &computed,
            &resolver,
            &available,
            &replacements,
            None,
            |_, _, _| {},
        )
        .expect_err("missing body styles must not use a manual inheritance fallback");

        assert!(error.contains("Stylo 未返回 <p> 的计算字体样式"));
    }
}
