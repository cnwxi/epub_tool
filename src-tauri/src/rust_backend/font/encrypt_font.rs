//! Model-independent building blocks for `encrypt_font`.
//!
//! Full font encryption still depends on CSS cascade resolution and XHTML text
//! rewriting. Production dispatch therefore accepts only the conservative
//! subset implemented below and reports unsupported CSS explicitly.

use super::{
    font_cmap::{rewrite_unicode_cmap, unicode_cmap},
    font_obfuscation::{build_obfuscation_mapping, html_entity_mapping, split_obfuscation_text},
    font_stylesheet::{parse_font_stylesheet, parse_inline_font_declarations, CssDeclaration},
    font_values::{normalize_font_family, resolve_font_value, ParsedFontValue},
};
use crate::rust_backend::{
    epub::{workspace::resolve_reference, EpubWorkspace},
    EpubTask, TaskOutcome,
};
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
/// cascade and XHTML write-back semantics are covered below. Unsupported
/// inputs are rejected before a partial font mapping can be written.
pub struct EncryptFontTask;

#[derive(Debug, Clone)]
pub(crate) struct FontEncryptionPlan {
    selector_rules: Vec<StrictFontRule>,
    font_by_family: BTreeMap<String, String>,
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct StrictSelector {
    compounds: Vec<CompoundSelector>,
    combinators: Vec<SelectorCombinator>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SelectorCombinator {
    Descendant,
    Child,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CompoundSelector {
    tag: Option<String>,
    id: Option<String>,
    classes: Vec<String>,
}

impl CompoundSelector {
    fn matches(&self, tag: &str, id: Option<&str>, classes: Option<&str>) -> bool {
        self.tag.as_deref().is_none_or(|expected| expected == tag)
            && self
                .id
                .as_deref()
                .is_none_or(|expected| id == Some(expected))
            && self.classes.iter().all(|class| has_class(classes, class))
    }

    fn specificity(&self) -> [u8; 3] {
        [
            self.id.is_some() as u8,
            u8::try_from(self.classes.len()).unwrap_or(u8::MAX),
            self.tag.is_some() as u8,
        ]
    }
}

impl StrictSelector {
    fn matches(
        &self,
        tag: &str,
        id: Option<&str>,
        classes: Option<&str>,
        ancestors: &[ElementContext],
    ) -> bool {
        let Some(last) = self.compounds.last() else {
            return false;
        };
        if !last.matches(tag, id, classes) {
            return false;
        }
        let mut ancestor_end = ancestors.len();
        for index in (0..self.combinators.len()).rev() {
            let compound = &self.compounds[index];
            match self.combinators[index] {
                SelectorCombinator::Child => {
                    let Some(parent_index) = ancestor_end.checked_sub(1) else {
                        return false;
                    };
                    if !compound.matches_context(&ancestors[parent_index]) {
                        return false;
                    }
                    ancestor_end = parent_index;
                }
                SelectorCombinator::Descendant => {
                    let Some(parent_index) = (0..ancestor_end)
                        .rev()
                        .find(|index| compound.matches_context(&ancestors[*index]))
                    else {
                        return false;
                    };
                    ancestor_end = parent_index;
                }
            }
        }
        true
    }

    fn specificity(&self) -> [u8; 3] {
        self.compounds
            .iter()
            .fold([0, 0, 0], |mut total, compound| {
                let specificity = compound.specificity();
                total[0] = total[0].saturating_add(specificity[0]);
                total[1] = total[1].saturating_add(specificity[1]);
                total[2] = total[2].saturating_add(specificity[2]);
                total
            })
    }
}

#[derive(Debug, Clone)]
struct StrictFontRule {
    selector: StrictSelector,
    declarations: Vec<CssDeclaration>,
    source_order: usize,
}

#[derive(Debug, Clone)]
struct ElementContext {
    name: String,
    id: Option<String>,
    classes: Option<String>,
    font: Option<String>,
    custom_properties: BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
struct FontDeclarationCandidate {
    declaration: CssDeclaration,
    /// CSS priority, in ascending order. The last two components retain the
    /// declaration's position after selector/source precedence has tied.
    precedence: (bool, bool, [u8; 3], usize, usize),
}

impl CompoundSelector {
    fn matches_context(&self, context: &ElementContext) -> bool {
        self.matches(
            &context.name,
            context.id.as_deref(),
            context.classes.as_deref(),
        )
    }
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
        Self::build_for_font_formats(workspace, input, options, true, false)
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
                .map_err(|_| format!("CSS 不是 UTF-8，当前 Rust 实现暂不支持: {member}"))?;
            let stylesheet = parse_font_stylesheet(css)?;
            for face in stylesheet.font_faces {
                let family = normalize_font_family(&face.family);
                let is_requested = requested_families
                    .as_ref()
                    .is_none_or(|requested| requested.contains(&family));
                if face.sources.len() != 1 {
                    if !is_requested {
                        continue;
                    }
                    return Err(format!(
                        "@font-face 需要多个 src，当前 Rust 实现暂不支持: {member}"
                    ));
                }
                let font_path = resolve_reference(member, &face.sources[0])?
                    .ok_or_else(|| format!("@font-face 不是本地字体引用: {member}"))?;
                let lower_path = font_path.to_ascii_lowercase();
                if !lower_path.ends_with(".ttf")
                    && !(allow_opentype && lower_path.ends_with(".otf"))
                    && is_requested
                {
                    return Err(format!("Rust 字体任务暂不支持该格式: {font_path}"));
                }
                if !workspace.members.contains_key(&font_path) {
                    if tolerate_missing_fonts || !is_requested {
                        continue;
                    }
                    return Err(format!("@font-face 引用的字体不存在: {font_path}"));
                }
                if font_by_family
                    .insert(family.clone(), font_path.clone())
                    .is_some_and(|previous| previous != font_path && is_requested)
                {
                    return Err(format!(
                        "字体 family 重复声明，当前 Rust 实现暂不支持: {family}"
                    ));
                }
            }
            raw_rules.extend(stylesheet.rules);
        }
        let mut selector_rules = Vec::new();
        for (source_order, rule) in raw_rules.into_iter().enumerate() {
            let selector = strict_selector(&rule.selector)?;
            if !rule.declarations.iter().any(|declaration| {
                declaration.name.starts_with("--")
                    || matches!(declaration.name.as_str(), "font-family" | "font" | "all")
            }) {
                continue;
            }
            selector_rules.push(StrictFontRule {
                selector,
                declarations: rule.declarations,
                source_order,
            });
        }
        let target_pairs: Vec<_> = font_by_family
            .iter()
            .filter(|(family, _)| {
                requested_families
                    .as_ref()
                    .is_none_or(|requested| requested.contains(*family))
            })
            .collect();
        let target_fonts = target_pairs
            .into_iter()
            .map(|(_, font)| font.clone())
            .collect::<BTreeSet<_>>();
        let target_families = font_by_family
            .iter()
            .filter(|(_, font)| target_fonts.contains(*font))
            .map(|(family, _)| family.clone())
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
            return Err("EPUB 没有 XHTML 文件，当前 Rust 实现暂不支持".to_string());
        }
        for member in &xhtml_members {
            let source = utf8_member(workspace, member, "XHTML")?;
            validate_xhtml_subset(&source, &selector_rules, &font_by_family)?;
        }
        Ok(Self {
            selector_rules,
            font_by_family,
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
                &self.font_by_family,
                &BTreeMap::new(),
                None,
                |font: &str, text: &str| {
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
        transform_xhtml(
            source,
            &self.selector_rules,
            &self.font_by_family,
            replacements,
            None,
            |_, _| {},
        )
    }

    pub(crate) fn rewrite_xhtml_with_ocr_failures(
        &self,
        source: &str,
        replacements: &BTreeMap<String, BTreeMap<char, char>>,
        failure_markup: &BTreeMap<String, BTreeMap<char, String>>,
    ) -> Result<String, String> {
        transform_xhtml(
            source,
            &self.selector_rules,
            &self.font_by_family,
            replacements,
            Some(failure_markup),
            |_, _| {},
        )
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

fn select_font_effect(
    candidates: &[FontDeclarationCandidate],
    font_by_family: &BTreeMap<String, String>,
    custom_properties: &BTreeMap<String, String>,
) -> Result<Option<FontRuleEffect>, String> {
    let Some(candidate) = candidates
        .iter()
        .filter(|candidate| {
            matches!(
                candidate.declaration.name.as_str(),
                "font-family" | "font" | "all"
            )
        })
        .max_by_key(|candidate| candidate.precedence)
    else {
        return Ok(None);
    };
    let value = match resolve_font_value(
        &candidate.declaration.name,
        &candidate.declaration.value,
        custom_properties,
    ) {
        Ok(value) => value,
        // A missing/cyclic `var()` makes this inherited CSS property invalid
        // at computed-value time. It therefore behaves as `unset`, rather
        // than exposing the parent rule that it had overridden.
        Err(error) if error == "未定义或循环的 CSS 自定义属性" => {
            return Ok(Some(FontRuleEffect::Inherit));
        }
        Err(error) => return Err(error),
    };
    let effect = match value {
        ParsedFontValue::Candidates(candidates) => candidates
            .iter()
            .find_map(|candidate| font_by_family.get(&normalize_font_family(candidate)))
            .cloned()
            .map_or(FontRuleEffect::Reset, FontRuleEffect::Font),
        ParsedFontValue::Inherit => FontRuleEffect::Inherit,
        ParsedFontValue::Reset => FontRuleEffect::Reset,
        ParsedFontValue::RevertLayer | ParsedFontValue::NeedsCustomPropertyResolution => {
            return Err("CSS 级联语义当前 Rust 实现暂不支持".to_string());
        }
    };
    Ok(Some(effect))
}

fn declaration_candidates(
    name: &str,
    id: Option<&str>,
    classes: Option<&str>,
    ancestors: &[ElementContext],
    selector_rules: &[StrictFontRule],
    inline_style: Option<&str>,
) -> Result<Vec<FontDeclarationCandidate>, String> {
    let mut candidates = Vec::new();
    for rule in selector_rules {
        if !rule.selector.matches(name, id, classes, ancestors) {
            continue;
        }
        let specificity = rule.selector.specificity();
        candidates.extend(
            rule.declarations
                .iter()
                .filter(|declaration| {
                    declaration.name.starts_with("--")
                        || matches!(declaration.name.as_str(), "font-family" | "font" | "all")
                })
                .cloned()
                .map(|declaration| FontDeclarationCandidate {
                    precedence: (
                        declaration.important,
                        false,
                        specificity,
                        rule.source_order,
                        declaration.declaration_order,
                    ),
                    declaration,
                }),
        );
    }
    if let Some(style) = inline_style {
        candidates.extend(
            parse_inline_font_declarations(style)?
                .into_iter()
                .filter(|declaration| {
                    declaration.name.starts_with("--")
                        || matches!(declaration.name.as_str(), "font-family" | "font" | "all")
                })
                .map(|declaration| FontDeclarationCandidate {
                    precedence: (
                        declaration.important,
                        true,
                        [0, 0, 0],
                        usize::MAX,
                        declaration.declaration_order,
                    ),
                    declaration,
                }),
        );
    }
    Ok(candidates)
}

fn computed_custom_properties(
    inherited: Option<&BTreeMap<String, String>>,
    candidates: &[FontDeclarationCandidate],
) -> Result<BTreeMap<String, String>, String> {
    let mut properties = inherited.cloned().unwrap_or_default();
    let mut selected = BTreeMap::<&str, &FontDeclarationCandidate>::new();
    for candidate in candidates
        .iter()
        .filter(|candidate| candidate.declaration.name.starts_with("--"))
    {
        let name = candidate.declaration.name.as_str();
        if selected
            .get(name)
            .is_none_or(|current| candidate.precedence >= current.precedence)
        {
            selected.insert(name, candidate);
        }
    }
    for (name, candidate) in selected {
        match custom_property_keyword(&candidate.declaration.value) {
            Some("inherit") | Some("unset") => {}
            Some("initial") | Some("revert") => {
                properties.remove(name);
            }
            Some("revert-layer") => {
                return Err("自定义属性 revert-layer 需要 CSS layer 兼容实现".to_string());
            }
            _ => {
                properties.insert(name.to_string(), candidate.declaration.value.clone());
            }
        }
    }
    Ok(properties)
}

fn custom_property_keyword(value: &str) -> Option<&'static str> {
    let value = value.trim();
    if value.contains(char::is_whitespace) {
        return None;
    }
    match value.to_ascii_lowercase().as_str() {
        "inherit" => Some("inherit"),
        "initial" => Some("initial"),
        "unset" => Some("unset"),
        "revert" => Some("revert"),
        "revert-layer" => Some("revert-layer"),
        _ => None,
    }
}

fn strict_selector(selector: &str) -> Result<StrictSelector, String> {
    let selector = selector.trim();
    if selector.is_empty() || selector.contains(['+', '~', ':', '[', ']', '*']) {
        return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    let mut compounds = Vec::new();
    let mut combinators = Vec::new();
    let mut pending_combinator = None;
    let mut current = String::new();
    let mut characters = selector.chars().peekable();
    while let Some(character) = characters.next() {
        if character.is_whitespace() {
            if !current.is_empty() {
                compounds.push(compound_selector(&current, selector)?);
                current.clear();
                pending_combinator = Some(SelectorCombinator::Descendant);
            }
            while characters.peek().is_some_and(|next| next.is_whitespace()) {
                characters.next();
            }
            continue;
        }
        if character == '>' {
            if !current.is_empty() {
                compounds.push(compound_selector(&current, selector)?);
                current.clear();
            }
            if compounds.is_empty()
                || pending_combinator.is_some_and(|value| value == SelectorCombinator::Child)
            {
                return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
            }
            pending_combinator = Some(SelectorCombinator::Child);
            while characters.peek().is_some_and(|next| next.is_whitespace()) {
                characters.next();
            }
            continue;
        }
        if current.is_empty() && !compounds.is_empty() {
            let Some(combinator) = pending_combinator.take() else {
                return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
            };
            combinators.push(combinator);
        }
        current.push(character);
    }
    if current.is_empty() {
        return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    if !compounds.is_empty() && combinators.len() != compounds.len() {
        return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    compounds.push(compound_selector(&current, selector)?);
    if combinators.len() + 1 != compounds.len() {
        return Err(format!("Rust 字体加密不支持该 CSS 选择器: {selector}"));
    }
    Ok(StrictSelector {
        compounds,
        combinators,
    })
}

fn compound_selector(value: &str, full_selector: &str) -> Result<CompoundSelector, String> {
    let mut tag = None;
    let mut id = None;
    let mut classes = Vec::new();
    let tag_end = value.find(['.', '#']).unwrap_or(value.len());
    if tag_end > 0 {
        let candidate = &value[..tag_end];
        if !valid_css_identifier(candidate) {
            return Err(format!("Rust 字体加密不支持该 CSS 选择器: {full_selector}"));
        }
        tag = Some(candidate.to_ascii_lowercase());
    }
    let mut cursor = tag_end;
    while cursor < value.len() {
        let marker = value.as_bytes()[cursor];
        cursor += 1;
        let suffix = &value[cursor..];
        let end = suffix.find(['.', '#']).unwrap_or(suffix.len());
        let value = &suffix[..end];
        if !valid_css_identifier(value) {
            return Err(format!("Rust 字体加密不支持该 CSS 选择器: {full_selector}"));
        }
        match marker {
            b'.' => classes.push(value.to_string()),
            b'#' if id.replace(value.to_string()).is_none() => {}
            _ => return Err(format!("Rust 字体加密不支持该 CSS 选择器: {full_selector}")),
        }
        cursor += end;
    }
    if tag.is_none() && id.is_none() && classes.is_empty() {
        return Err(format!("Rust 字体加密不支持该 CSS 选择器: {full_selector}"));
    }
    Ok(CompoundSelector { tag, id, classes })
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
        .map_err(|_| format!("{label} 不是 UTF-8，当前 Rust 实现暂不支持: {member}"))
}

fn entity_target_char(entity: &str) -> Result<char, String> {
    let codepoint = entity
        .strip_prefix("&#x")
        .and_then(|value| u32::from_str_radix(value.trim_end_matches(';'), 16).ok())
        .ok_or_else(|| format!("无效 HTML 混淆实体: {entity}"))?;
    char::from_u32(codepoint).ok_or_else(|| format!("无效 HTML 混淆码位: {entity}"))
}

fn validate_xhtml_subset(
    source: &str,
    selector_rules: &[StrictFontRule],
    font_by_family: &BTreeMap<String, String>,
) -> Result<(), String> {
    transform_xhtml(
        source,
        selector_rules,
        font_by_family,
        &BTreeMap::new(),
        None,
        |_, _| {},
    )
    .map(|_| ())
}

fn transform_xhtml(
    source: &str,
    selector_rules: &[StrictFontRule],
    font_by_family: &BTreeMap<String, String>,
    replacements: &BTreeMap<String, BTreeMap<char, char>>,
    failure_markup: Option<&BTreeMap<String, BTreeMap<char, String>>>,
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
            let failures = failure_markup.and_then(|by_font| by_font.get(font));
            if let Some(mapping) = replacements.get(font) {
                result.push_str(&rewrite_text_preserving_entities(
                    text, mapping, failures, &entity,
                ));
            } else if let Some(failures) = failures {
                result.push_str(&rewrite_text_preserving_entities(
                    text,
                    &BTreeMap::new(),
                    Some(failures),
                    &entity,
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
        let end = tag_end(source, text_end)?;
        let tag = &source[text_end..=end];
        if tag.starts_with("<!--") || tag.starts_with("<![CDATA[") {
            return Err("XHTML 注释或 CDATA 当前 Rust 实现暂不支持".to_string());
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
            return Err(format!("XHTML <{name}> 当前 Rust 实现暂不支持"));
        }
        let classes = tag_attribute(inner, "class")?;
        let id = tag_attribute(inner, "id")?;
        let inline_style = tag_attribute(inner, "style")?;
        let candidates = declaration_candidates(
            &name,
            id.as_deref(),
            classes.as_deref(),
            &stack,
            selector_rules,
            inline_style.as_deref(),
        )?;
        let inherited_properties = stack.last().map(|context| &context.custom_properties);
        let custom_properties = computed_custom_properties(inherited_properties, &candidates)?;
        let matching_effect = select_font_effect(&candidates, font_by_family, &custom_properties)?;
        let inherited = stack.last().and_then(|context| context.font.clone());
        let font = match matching_effect {
            Some(FontRuleEffect::Font(font)) => Some(font),
            Some(FontRuleEffect::Reset) => None,
            Some(FontRuleEffect::Inherit) | None => inherited,
        };
        if !empty {
            stack.push(ElementContext {
                name,
                id,
                classes,
                font,
                custom_properties,
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

fn rewrite_text_preserving_entities(
    text: &str,
    mapping: &BTreeMap<char, char>,
    failures: Option<&BTreeMap<char, String>>,
    entity: &Regex,
) -> String {
    let mut result = String::with_capacity(text.len());
    let mut cursor = 0;
    for matched in entity.find_iter(text) {
        rewrite_text_fragment(
            &mut result,
            &text[cursor..matched.start()],
            mapping,
            failures,
        );
        result.push_str(matched.as_str());
        cursor = matched.end();
    }
    rewrite_text_fragment(&mut result, &text[cursor..], mapping, failures);
    result
}

fn rewrite_text_fragment(
    output: &mut String,
    text: &str,
    mapping: &BTreeMap<char, char>,
    failures: Option<&BTreeMap<char, String>>,
) {
    for character in text.chars() {
        if let Some(markup) = failures.and_then(|failures| failures.get(&character)) {
            output.push_str(markup);
        } else {
            output.push(mapping.get(&character).copied().unwrap_or(character));
        }
    }
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
