//! Model-independent building blocks for `encrypt_font`.
//!
//! Full font encryption still depends on CSS cascade resolution and XHTML text
//! rewriting. Production dispatch therefore accepts only the conservative
//! subset implemented below and retains the Python sidecar for all others.

use super::{
    font_cmap::{rewrite_unicode_cmap, unicode_cmap},
    font_obfuscation::{build_obfuscation_mapping, html_entity_mapping, split_obfuscation_text},
    font_stylesheet::parse_font_stylesheet,
    font_values::{normalize_font_family, parse_font_value, ParsedFontValue},
};
use crate::rust_backend::{epub::{workspace::resolve_reference, EpubWorkspace}, EpubTask, TaskOutcome};
use rand::{rngs::StdRng, Rng, SeedableRng};
use regex::Regex;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
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

/// Native `encrypt_font` implementation for the EPUB subset whose complete
/// cascade and XHTML write-back semantics are covered below. Any feature not
/// represented here makes `supports_input()` return false, so Tauri retains
/// the Python sidecar instead of applying a partial font mapping.
pub struct EncryptFontTask;

#[derive(Debug, Clone)]
pub(crate) struct FontEncryptionPlan {
    selector_rules: Vec<StrictFontRule>,
    target_fonts: BTreeSet<String>,
    target_families: BTreeSet<String>,
    xhtml_members: Vec<String>,
}

#[derive(Debug, Clone)]
enum FontRuleEffect {
    Inherit,
    Reset,
    Font(String),
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
enum StrictSelector {
    Class(String),
    Tag(String),
    TagClass { tag: String, class: String },
}

impl StrictSelector {
    fn matches(&self, tag: &str, classes: Option<&str>) -> bool {
        match self {
            Self::Class(class) => has_class(classes, class),
            Self::Tag(expected) => expected == tag,
            Self::TagClass {
                tag: expected_tag,
                class,
            } => expected_tag == tag && has_class(classes, class),
        }
    }

    fn specificity(&self) -> [u8; 3] {
        match self {
            Self::Class(_) => [0, 1, 0],
            Self::Tag(_) => [0, 0, 1],
            Self::TagClass { .. } => [0, 1, 1],
        }
    }
}

#[derive(Debug, Clone)]
struct StrictFontRule {
    selector: StrictSelector,
    effect: FontRuleEffect,
    source_order: usize,
}

#[derive(Debug, Clone)]
struct ElementContext {
    name: String,
    font: Option<String>,
}

impl EpubTask for EncryptFontTask {
    fn task_type(&self) -> &'static str {
        "encrypt_font"
    }

    fn supports_options(&self, options: &Value) -> bool {
        options.as_object().is_some_and(|values| {
            values
                .keys()
                .all(|key| key == "target_font_families_by_file")
                && values
                    .get("target_font_families_by_file")
                    .is_none_or(Value::is_object)
        })
    }

    fn supports_input(&self, input: &Path, options: &Value) -> bool {
        EpubWorkspace::load(input, |_| {})
            .and_then(|workspace| {
                let plan = FontEncryptionPlan::build(&workspace, input, options)?;
                plan.validate_execution(&workspace)
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
            let source = utf8_member(workspace, member, "XHTML")?;
            let rewritten = plan.rewrite_xhtml(&source, &replacements)?;
            workspace
                .members
                .insert(member.clone(), rewritten.into_bytes());
        }
        log(format!(
            "Rust 字体加密完成：处理 {} 个字体、{} 个 XHTML 文件。",
            replacements.len(),
            plan.xhtml_members().len()
        ));
        Ok(TaskOutcome::Success)
    }
}

impl FontEncryptionPlan {
    pub(crate) fn build(
        workspace: &EpubWorkspace,
        input: &Path,
        options: &Value,
    ) -> Result<Self, String> {
        Self::build_for_font_formats(workspace, input, options, false, false)
    }

    pub(crate) fn build_for_decryption(
        workspace: &EpubWorkspace,
        input: &Path,
        options: &Value,
    ) -> Result<Self, String> {
        Self::build_for_font_formats(workspace, input, options, true, true)
    }

    fn build_for_font_formats(
        workspace: &EpubWorkspace,
        input: &Path,
        options: &Value,
        allow_opentype: bool,
        tolerate_missing_fonts: bool,
    ) -> Result<Self, String> {
        let requested_families = selected_families(input, options)?;
        let mut font_by_family = BTreeMap::new();
        let mut raw_rules = Vec::new();
        for (member, data) in &workspace.members {
            if !member.to_ascii_lowercase().ends_with(".css") {
                continue;
            }
            let css = std::str::from_utf8(data)
                .map_err(|_| format!("CSS 不是 UTF-8，需使用 Python 兼容实现: {member}"))?;
            let stylesheet = parse_font_stylesheet(css)?;
            for face in stylesheet.font_faces {
                if face.sources.len() != 1 {
                    return Err(format!(
                        "@font-face 需要多个 src，需使用 Python 兼容实现: {member}"
                    ));
                }
                let font_path = resolve_reference(member, &face.sources[0])?
                    .ok_or_else(|| format!("@font-face 不是本地字体引用: {member}"))?;
                let lower_path = font_path.to_ascii_lowercase();
                if !lower_path.ends_with(".ttf")
                    && !(allow_opentype && lower_path.ends_with(".otf"))
                {
                    return Err(format!("Rust 字体任务暂不支持该格式: {font_path}"));
                }
                if !workspace.members.contains_key(&font_path) {
                    if tolerate_missing_fonts {
                        continue;
                    }
                    return Err(format!("@font-face 引用的字体不存在: {font_path}"));
                }
                let family = normalize_font_family(&face.family);
                if font_by_family
                    .insert(family.clone(), font_path.clone())
                    .is_some_and(|previous| previous != font_path)
                {
                    return Err(format!(
                        "字体 family 重复声明，需使用 Python 兼容实现: {family}"
                    ));
                }
            }
            raw_rules.extend(stylesheet.rules);
        }
        let mut selector_rules = Vec::new();
        let mut seen_selectors = BTreeSet::new();
        for (source_order, rule) in raw_rules.into_iter().enumerate() {
            let selector = strict_selector(&rule.selector)?;
            if rule
                .declarations
                .iter()
                .any(|item| item.name.starts_with("--"))
            {
                return Err("CSS 自定义属性需使用 Python 兼容实现".to_string());
            }
            let font_declarations: Vec<_> = rule
                .declarations
                .iter()
                .filter(|item| matches!(item.name.as_str(), "font-family" | "font" | "all"))
                .collect();
            if font_declarations.len() != 1 {
                return Err(format!("CSS 规则的字体声明不唯一: {}", rule.selector));
            }
            let declaration = font_declarations[0];
            let effect = match parse_font_value(&declaration.name, &declaration.value)? {
                ParsedFontValue::Candidates(candidates) => {
                    let font = candidates
                        .iter()
                        .find_map(|candidate| font_by_family.get(&normalize_font_family(candidate)))
                        .cloned();
                    font.map_or(FontRuleEffect::Reset, FontRuleEffect::Font)
                }
                ParsedFontValue::Inherit => FontRuleEffect::Inherit,
                ParsedFontValue::Reset => FontRuleEffect::Reset,
                ParsedFontValue::RevertLayer | ParsedFontValue::NeedsCustomPropertyResolution => {
                    return Err("CSS 级联语义需使用 Python 兼容实现".to_string());
                }
            };
            if !seen_selectors.insert(selector.clone()) {
                return Err(format!(
                    "CSS 字体选择器重复，需使用 Python 兼容实现: {}",
                    rule.selector
                ));
            }
            selector_rules.push(StrictFontRule {
                selector,
                effect,
                source_order,
            });
        }
        let target_pairs: Vec<_> = font_by_family
            .into_iter()
            .filter(|(family, _)| {
                requested_families
                    .as_ref()
                    .is_none_or(|requested| requested.contains(family))
            })
            .collect();
        let target_families = target_pairs
            .iter()
            .map(|(family, _)| family.clone())
            .collect();
        let target_fonts = target_pairs
            .into_iter()
            .map(|(_, font)| font)
            .collect();
        let xhtml_members: Vec<_> = workspace
            .members
            .keys()
            .filter(|member| {
                let lower = member.to_ascii_lowercase();
                lower.ends_with(".xhtml") || lower.ends_with(".html")
            })
            .cloned()
            .collect();
        if xhtml_members.is_empty() {
            return Err("EPUB 没有 XHTML 文件，需使用 Python 兼容实现".to_string());
        }
        for member in &xhtml_members {
            let source = utf8_member(workspace, member, "XHTML")?;
            validate_xhtml_subset(&source, &selector_rules)?;
        }
        Ok(Self {
            selector_rules,
            target_fonts,
            target_families,
            xhtml_members,
        })
    }

    pub(crate) fn collect_target_text(
        &self,
        workspace: &EpubWorkspace,
    ) -> Result<BTreeMap<String, String>, String> {
        let mut text_by_font = BTreeMap::<String, String>::new();
        for member in &self.xhtml_members {
            let source = utf8_member(workspace, member, "XHTML")?;
            transform_xhtml(
                &source,
                &self.selector_rules,
                &BTreeMap::new(),
                |font, text| {
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
        source: &str,
        replacements: &BTreeMap<String, BTreeMap<char, char>>,
    ) -> Result<String, String> {
        transform_xhtml(source, &self.selector_rules, replacements, |_, _| {})
    }

    /// Runs all font-table writes in memory before native dispatch is selected.
    /// Python can recover individual font errors, whereas a Rust task cannot
    /// safely begin writing an EPUB and then discover an unsupported table.
    fn validate_execution(&self, workspace: &EpubWorkspace) -> Result<(), String> {
        let text_by_font = self.collect_target_text(workspace)?;
        let mut rng = StdRng::seed_from_u64(0);
        for font_path in &self.target_fonts {
            let font_data = workspace
                .members
                .get(font_path)
                .ok_or_else(|| format!("EPUB 缺少目标字体: {font_path}"))?;
            obfuscate_font_data(
                font_data,
                text_by_font.get(font_path).map_or("", String::as_str),
                &mut rng,
            )?;
        }
        Ok(())
    }
}

fn selected_families(input: &Path, options: &Value) -> Result<Option<BTreeSet<String>>, String> {
    let Some(values) = options.as_object() else {
        return Err("字体任务选项必须是对象".to_string());
    };
    let Some(by_file) = values.get("target_font_families_by_file") else {
        return Ok(None);
    };
    let by_file = by_file
        .as_object()
        .ok_or_else(|| "target_font_families_by_file 必须是对象".to_string())?;
    let Some(families) = by_file.get(&input.to_string_lossy().to_string()) else {
        return Ok(None);
    };
    let families = families
        .as_array()
        .ok_or_else(|| "目标字体列表必须是数组".to_string())?;
    if families.is_empty() {
        return Ok(None);
    }
    families
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(normalize_font_family)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| "目标字体 family 必须是非空字符串".to_string())
        })
        .collect::<Result<BTreeSet<_>, _>>()
        .map(Some)
}

fn strict_selector(selector: &str) -> Result<StrictSelector, String> {
    let selector = selector.trim();
    if let Some(class) = selector.strip_prefix('.') {
        return valid_css_identifier(class)
            .then(|| StrictSelector::Class(class.to_string()))
            .ok_or_else(|| format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    if let Some((tag, class)) = selector.split_once('.') {
        return (valid_css_identifier(tag) && valid_css_identifier(class))
            .then(|| StrictSelector::TagClass {
                tag: tag.to_ascii_lowercase(),
                class: class.to_string(),
            })
            .ok_or_else(|| format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    valid_css_identifier(selector)
        .then(|| StrictSelector::Tag(selector.to_ascii_lowercase()))
        .ok_or_else(|| format!("Rust 字体加密不支持该 CSS 选择器: {selector}"))
}

fn valid_css_identifier(value: &str) -> bool {
    !value.is_empty()
        && value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, '-' | '_'))
}

fn utf8_member<'a>(
    workspace: &'a EpubWorkspace,
    member: &str,
    label: &str,
) -> Result<&'a str, String> {
    let data = workspace
        .members
        .get(member)
        .ok_or_else(|| format!("EPUB 缺少 {label} 文件: {member}"))?;
    std::str::from_utf8(data)
        .map_err(|_| format!("{label} 不是 UTF-8，需使用 Python 兼容实现: {member}"))
}

fn entity_target_char(entity: &str) -> Result<char, String> {
    let codepoint = entity
        .strip_prefix("&#x")
        .and_then(|value| u32::from_str_radix(value.trim_end_matches(';'), 16).ok())
        .ok_or_else(|| format!("无效 HTML 混淆实体: {entity}"))?;
    char::from_u32(codepoint).ok_or_else(|| format!("无效 HTML 混淆码位: {entity}"))
}

fn validate_xhtml_subset(source: &str, selector_rules: &[StrictFontRule]) -> Result<(), String> {
    transform_xhtml(source, selector_rules, &BTreeMap::new(), |_, _| {}).map(|_| ())
}

fn transform_xhtml(
    source: &str,
    selector_rules: &[StrictFontRule],
    replacements: &BTreeMap<String, BTreeMap<char, char>>,
    mut visit_text: impl FnMut(&str, &str),
) -> Result<String, String> {
    let entity = Regex::new(r"&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9._-]*);")
        .expect("literal entity regex");
    let mut result = String::with_capacity(source.len());
    let mut stack = Vec::<ElementContext>::new();
    let mut cursor = 0;
    while cursor < source.len() {
        let next_tag = source[cursor..].find('<').map(|offset| cursor + offset);
        let text_end = next_tag.unwrap_or(source.len());
        let text = &source[cursor..text_end];
        if let Some(font) = stack.last().and_then(|context| context.font.as_deref()) {
            let visible = entity.replace_all(text, "");
            visit_text(font, &visible);
            if let Some(mapping) = replacements.get(font) {
                result.push_str(&rewrite_text_preserving_entities(text, mapping, &entity));
            } else {
                result.push_str(text);
            }
        } else {
            result.push_str(text);
        }
        if text_end == source.len() {
            break;
        }
        let end = tag_end(source, text_end)?;
        let tag = &source[text_end..=end];
        if tag.starts_with("<!--") || tag.starts_with("<![CDATA[") {
            return Err("XHTML 注释或 CDATA 需使用 Python 兼容实现".to_string());
        }
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
        let empty = inner.trim_end().ends_with('/');
        let name = tag_name(inner.trim_end_matches('/').trim_end())?;
        if matches!(name.as_str(), "script" | "style") {
            return Err(format!("XHTML <{name}> 需使用 Python 兼容实现"));
        }
        let classes = tag_attribute(inner, "class")?;
        if let Some(style) = tag_attribute(inner, "style")? {
            let style = style.to_ascii_lowercase();
            if style.contains("font-family") || style.contains("font:") || style.contains("all:") {
                return Err("XHTML 内联字体样式需使用 Python 兼容实现".to_string());
            }
        }
        let matching_effect = selector_rules
            .iter()
            .filter(|rule| rule.selector.matches(&name, classes.as_deref()))
            .max_by_key(|rule| (rule.selector.specificity(), rule.source_order))
            .map(|rule| &rule.effect);
        let inherited = stack.last().and_then(|context| context.font.clone());
        let font = match matching_effect {
            Some(FontRuleEffect::Font(font)) => Some(font.clone()),
            Some(FontRuleEffect::Reset) => None,
            Some(FontRuleEffect::Inherit) | None => inherited,
        };
        if !empty {
            stack.push(ElementContext { name, font });
        }
        result.push_str(tag);
        cursor = end + 1;
    }
    if !stack.is_empty() {
        return Err("XHTML 标签未闭合".to_string());
    }
    Ok(result)
}

fn rewrite_text_preserving_entities(
    text: &str,
    mapping: &BTreeMap<char, char>,
    entity: &Regex,
) -> String {
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for matched in entity.find_iter(text) {
        result.extend(
            text[cursor..matched.start()]
                .chars()
                .map(|character| mapping.get(&character).copied().unwrap_or(character)),
        );
        result.push_str(matched.as_str());
        cursor = matched.end();
    }
    result.extend(
        text[cursor..]
            .chars()
            .map(|character| mapping.get(&character).copied().unwrap_or(character)),
    );
    result
}

fn has_class(classes: Option<&str>, expected: &str) -> bool {
    classes.is_some_and(|classes| {
        classes
            .split_ascii_whitespace()
            .any(|class| class == expected)
    })
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

fn tag_attribute(tag_inner: &str, target: &str) -> Result<Option<String>, String> {
    let mut index = tag_name_end(tag_inner)?;
    let bytes = tag_inner.as_bytes();
    while index < bytes.len() {
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        if index >= bytes.len() || bytes[index] == b'/' {
            break;
        }
        let name_start = index;
        while bytes
            .get(index)
            .is_some_and(|byte| byte.is_ascii_alphanumeric() || matches!(*byte, b':' | b'-' | b'_'))
        {
            index += 1;
        }
        if name_start == index {
            return Err("XHTML 属性名无效".to_string());
        }
        let name = &tag_inner[name_start..index];
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        if bytes.get(index) != Some(&b'=') {
            return Err("XHTML 属性必须有引号值".to_string());
        }
        index += 1;
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        let quote = *bytes
            .get(index)
            .ok_or_else(|| "XHTML 属性值缺失".to_string())?;
        if !matches!(quote, b'\'' | b'"') {
            return Err("XHTML 属性值必须使用引号".to_string());
        }
        index += 1;
        let value_start = index;
        while bytes.get(index) != Some(&quote) {
            if index >= bytes.len() {
                return Err("XHTML 属性值未闭合".to_string());
            }
            index += 1;
        }
        let value = &tag_inner[value_start..index];
        index += 1;
        if name.eq_ignore_ascii_case(target) {
            return Ok(Some(value.to_string()));
        }
    }
    Ok(None)
}

fn tag_name_end(tag_inner: &str) -> Result<usize, String> {
    let value = tag_inner.trim_start();
    let consumed = tag_inner.len() - value.len();
    let name = tag_name(value)?;
    Ok(consumed + name.len())
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
/// plan. Characters absent from the font are ignored exactly as Python's
/// `ensure_cmap_has_all_text()` does before it writes the output EPUB.
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
    use super::{obfuscate_font_data, rewrite_cmap_for_obfuscation};
    use rand::{rngs::StdRng, SeedableRng};
    use std::collections::BTreeMap;

    #[test]
    fn rejects_non_font_input() {
        assert!(rewrite_cmap_for_obfuscation(b"not-a-font", &BTreeMap::new(), "你").is_err());
    }

    #[test]
    fn rejects_non_font_input_before_building_an_encryption_plan() {
        let mut rng = StdRng::seed_from_u64(1);
        assert!(obfuscate_font_data(b"not-a-font", "你好", &mut rng).is_err());
    }
}
