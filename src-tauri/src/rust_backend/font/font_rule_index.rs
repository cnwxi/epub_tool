//! Maps conservative CSS font rules to XHTML DOM nodes.
//!
//! This is the bridge between stylesheet parsing, selector matching and the
//! shared cascade primitives. It refuses unsupported selector specificity and
//! CSS features which have not yet passed the Python regression suite.

use super::{
    font_cascade::{font_rule_precedence, CascadedFontRule, FontRulePrecedence, FontRuleValue},
    font_selectors::simple_selector_specificity,
    font_stylesheet::{parse_inline_font_declarations, CssDeclaration, FontStylesheet},
    font_values::{normalize_font_family, resolve_font_value, ParsedFontValue},
};
use dom_query::{Document, Matcher, NodeId};
use std::collections::BTreeMap;

pub type FontRuleIndex = BTreeMap<NodeId, Vec<CascadedFontRule<String>>>;

/// Indexes every supported stylesheet declaration against matching XHTML
/// elements. `font_family_to_file` must use normalized family names as keys.
pub fn build_font_rule_index(
    xhtml: &str,
    stylesheet: &FontStylesheet,
    font_family_to_file: &BTreeMap<String, String>,
) -> Result<FontRuleIndex, String> {
    let document = Document::from(xhtml);
    let mut parent_by_node = BTreeMap::new();
    for node in document.root().descendants() {
        if !node.is_element() {
            continue;
        }
        if let Some(parent) = node.parent().filter(|parent| parent.is_element()) {
            parent_by_node.insert(node.id, parent.id);
        }
    }
    let matched_rules = stylesheet
        .rules
        .iter()
        .map(|rule| match_rule_nodes(&document, rule))
        .collect::<Result<Vec<_>, _>>()?;
    let inline_declarations = inline_declarations_by_node(&document)?;
    let custom_records = build_custom_property_records(&matched_rules, &inline_declarations)?;
    let mut custom_cache = BTreeMap::new();
    let mut index = FontRuleIndex::new();
    for matched_rule in &matched_rules {
        let rule = matched_rule.rule;
        let declaration = select_font_declaration(&rule.declarations)?;
        let Some(declaration) = declaration else {
            continue;
        };
        for node_id in &matched_rule.node_ids {
            let custom_properties = computed_custom_properties(
                *node_id,
                &parent_by_node,
                &custom_records,
                &mut custom_cache,
            )?;
            let cascaded = declaration_to_rule(
                declaration,
                matched_rule.specificity,
                rule_declaration_order(rule.source_order, declaration)?,
                false,
                font_family_to_file,
                &custom_properties,
            )?;
            index.entry(*node_id).or_default().push(cascaded);
        }
    }
    for (node_id, declarations) in &inline_declarations {
        let Some(declaration) = select_font_declaration(declarations)? else {
            continue;
        };
        let custom_properties = computed_custom_properties(
            *node_id,
            &parent_by_node,
            &custom_records,
            &mut custom_cache,
        )?;
        let cascaded = declaration_to_rule(
            declaration,
            [0, 0, 0],
            declaration.declaration_order,
            true,
            font_family_to_file,
            &custom_properties,
        )?;
        index.entry(*node_id).or_default().push(cascaded);
    }
    Ok(index)
}

struct MatchedRule<'a> {
    rule: &'a super::font_stylesheet::CssFontRule,
    specificity: [i64; 3],
    node_ids: Vec<NodeId>,
}

#[derive(Debug, Clone)]
struct CustomPropertyRecord {
    value: String,
    precedence: FontRulePrecedence,
}

fn match_rule_nodes<'a>(
    document: &Document,
    rule: &'a super::font_stylesheet::CssFontRule,
) -> Result<MatchedRule<'a>, String> {
    let specificity = simple_selector_specificity(&rule.selector)?;
    let matcher = Matcher::new(&rule.selector)
        .map_err(|error| format!("不支持的 CSS 选择器 {:?}: {error:?}", rule.selector))?;
    let node_ids = document
        .select_matcher(&matcher)
        .nodes()
        .iter()
        .map(|node| node.id)
        .collect();
    Ok(MatchedRule {
        rule,
        specificity,
        node_ids,
    })
}

fn inline_declarations_by_node(
    document: &Document,
) -> Result<BTreeMap<NodeId, Vec<CssDeclaration>>, String> {
    let mut result = BTreeMap::new();
    for node in document.root().descendants() {
        if !node.is_element() {
            continue;
        }
        let Some(style) = node.attr("style") else {
            continue;
        };
        let declarations = parse_inline_font_declarations(&style)?;
        if !declarations.is_empty() {
            result.insert(node.id, declarations);
        }
    }
    Ok(result)
}

fn build_custom_property_records(
    matched_rules: &[MatchedRule<'_>],
    inline_declarations: &BTreeMap<NodeId, Vec<CssDeclaration>>,
) -> Result<BTreeMap<NodeId, BTreeMap<String, Vec<CustomPropertyRecord>>>, String> {
    let mut result: BTreeMap<NodeId, BTreeMap<String, Vec<CustomPropertyRecord>>> = BTreeMap::new();
    for matched_rule in matched_rules {
        for declaration in &matched_rule.rule.declarations {
            if !declaration.name.starts_with("--") {
                continue;
            }
            let precedence = font_rule_precedence(
                declaration.important,
                matched_rule.specificity,
                rule_declaration_order(matched_rule.rule.source_order, declaration)?,
                false,
                None,
                None,
            );
            for node_id in &matched_rule.node_ids {
                result
                    .entry(*node_id)
                    .or_default()
                    .entry(declaration.name.clone())
                    .or_default()
                    .push(CustomPropertyRecord {
                        value: declaration.value.clone(),
                        precedence,
                    });
            }
        }
    }
    for (node_id, declarations) in inline_declarations {
        for declaration in declarations {
            if !declaration.name.starts_with("--") {
                continue;
            }
            let precedence = font_rule_precedence(
                declaration.important,
                [0, 0, 0],
                declaration.declaration_order,
                true,
                None,
                None,
            );
            result
                .entry(*node_id)
                .or_default()
                .entry(declaration.name.clone())
                .or_default()
                .push(CustomPropertyRecord {
                    value: declaration.value.clone(),
                    precedence,
                });
        }
    }
    Ok(result)
}

fn computed_custom_properties(
    node_id: NodeId,
    parent_by_node: &BTreeMap<NodeId, NodeId>,
    records_by_node: &BTreeMap<NodeId, BTreeMap<String, Vec<CustomPropertyRecord>>>,
    cache: &mut BTreeMap<NodeId, BTreeMap<String, String>>,
) -> Result<BTreeMap<String, String>, String> {
    if let Some(properties) = cache.get(&node_id) {
        return Ok(properties.clone());
    }
    let mut properties = parent_by_node
        .get(&node_id)
        .map(|parent| computed_custom_properties(*parent, parent_by_node, records_by_node, cache))
        .transpose()?
        .unwrap_or_default();
    if let Some(node_records) = records_by_node.get(&node_id) {
        for (name, records) in node_records {
            let Some(selected) = records.iter().max_by_key(|record| record.precedence) else {
                continue;
            };
            match css_wide_keyword(&selected.value) {
                Some("inherit") | Some("unset") => {}
                Some("initial") | Some("revert") => {
                    properties.remove(name);
                }
                Some("revert-layer") => {
                    return Err("自定义属性 revert-layer 需要 CSS layer 兼容实现".to_string());
                }
                _ => {
                    properties.insert(name.clone(), selected.value.clone());
                }
            }
        }
    }
    cache.insert(node_id, properties.clone());
    Ok(properties)
}

fn css_wide_keyword(value: &str) -> Option<&'static str> {
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

fn select_font_declaration(
    declarations: &[CssDeclaration],
) -> Result<Option<&CssDeclaration>, String> {
    let mut selected = None;
    for declaration in declarations {
        if !matches!(declaration.name.as_str(), "font-family" | "font" | "all") {
            continue;
        }
        if declaration.name == "all" {
            return Err("all 属性需要完整 CSS reset 语义，当前 Rust 实现暂不支持".to_string());
        }
        let priority = (declaration.important, declaration.declaration_order);
        if selected.is_none_or(|current: &CssDeclaration| {
            priority >= (current.important, current.declaration_order)
        }) {
            selected = Some(declaration);
        }
    }
    Ok(selected)
}

fn declaration_to_rule(
    declaration: &CssDeclaration,
    specificity: [i64; 3],
    order: usize,
    is_inline: bool,
    font_family_to_file: &BTreeMap<String, String>,
    custom_properties: &BTreeMap<String, String>,
) -> Result<CascadedFontRule<String>, String> {
    let value = resolve_font_value(&declaration.name, &declaration.value, custom_properties)?;
    let (value, font) = match value {
        ParsedFontValue::Candidates(candidates) => {
            let font = candidates
                .iter()
                .find_map(|candidate| font_family_to_file.get(&normalize_font_family(candidate)))
                .cloned();
            (
                font.as_ref()
                    .map_or(FontRuleValue::Blocker, |_| FontRuleValue::Font),
                font,
            )
        }
        ParsedFontValue::Inherit => (FontRuleValue::Inherit, None),
        ParsedFontValue::RevertLayer => (FontRuleValue::RevertLayer, None),
        ParsedFontValue::Reset => {
            return Err("CSS reset 字体值当前 Rust 实现暂不支持".to_string());
        }
        ParsedFontValue::NeedsCustomPropertyResolution => {
            return Err("CSS 自定义属性尚未索引到 XHTML 元素，当前 Rust 实现暂不支持".to_string());
        }
    };
    Ok(CascadedFontRule {
        value,
        font,
        precedence: font_rule_precedence(
            declaration.important,
            specificity,
            order,
            is_inline,
            None,
            None,
        ),
    })
}

fn rule_declaration_order(
    source_order: usize,
    declaration: &CssDeclaration,
) -> Result<usize, String> {
    source_order
        .checked_mul(1_000)
        .and_then(|value| value.checked_add(declaration.declaration_order))
        .ok_or_else(|| "CSS 规则顺序超出范围".to_string())
}

#[cfg(test)]
mod tests {
    use super::build_font_rule_index;
    use crate::rust_backend::font::{
        font_cascade::select_cascaded_font_rule, font_stylesheet::parse_font_stylesheet,
    };
    use std::collections::BTreeMap;

    #[test]
    fn indexes_the_python_reference_font_rule_to_its_xhtml_element() {
        let stylesheet = parse_font_stylesheet(
            "@font-face { font-family: Obf; src: url(obf.ttf); } .obf { font-family: Obf, serif; }",
        )
        .expect("stylesheet should parse");
        let fonts = BTreeMap::from([("obf".to_string(), "OEBPS/Fonts/obf.ttf".to_string())]);
        let index = build_font_rule_index(
            "<html><body><p class=\"obf\">甲</p><p>乙</p></body></html>",
            &stylesheet,
            &fonts,
        )
        .expect("rule index should build");

        assert_eq!(index.len(), 1);
        assert_eq!(
            index
                .values()
                .next()
                .and_then(|records| select_cascaded_font_rule(records))
                .and_then(|record| record.font.as_deref()),
            Some("OEBPS/Fonts/obf.ttf")
        );
    }

    #[test]
    fn lets_important_rules_win_on_the_same_xhtml_node() {
        let stylesheet = parse_font_stylesheet(
            ".target { font-family: TargetFont !important; } .sys { font-family: serif; }",
        )
        .expect("stylesheet should parse");
        let fonts = BTreeMap::from([(
            "targetfont".to_string(),
            "OEBPS/Fonts/target.ttf".to_string(),
        )]);
        let index = build_font_rule_index(
            "<html><body><p class=\"target sys\">甲</p></body></html>",
            &stylesheet,
            &fonts,
        )
        .expect("rule index should build");

        assert_eq!(
            index
                .values()
                .next()
                .and_then(|records| select_cascaded_font_rule(records))
                .and_then(|record| record.font.as_deref()),
            Some("OEBPS/Fonts/target.ttf")
        );
    }

    #[test]
    fn custom_property_rules_require_a_mapped_font() {
        let stylesheet =
            parse_font_stylesheet(".target { --font: TargetFont; font-family: var(--font); }")
                .expect("stylesheet should parse");
        let index =
            build_font_rule_index("<p class=\"target\">甲</p>", &stylesheet, &BTreeMap::new())
                .expect("custom property should resolve before font lookup");
        assert!(index
            .values()
            .next()
            .and_then(|records| select_cascaded_font_rule(records))
            .is_some_and(|record| record.font.is_none()));
    }

    #[test]
    fn resolves_inherited_custom_property_fonts_on_matching_nodes() {
        let stylesheet = parse_font_stylesheet(
            ".parent { --font: TargetFont; } .target { font-family: var(--font), serif; }",
        )
        .expect("stylesheet should parse");
        let fonts = BTreeMap::from([(
            "targetfont".to_string(),
            "OEBPS/Fonts/target.ttf".to_string(),
        )]);
        let index = build_font_rule_index(
            "<html><body><section class=\"parent\"><p class=\"target\">甲</p></section></body></html>",
            &stylesheet,
            &fonts,
        )
        .expect("custom property should inherit");

        assert_eq!(
            index
                .values()
                .next()
                .and_then(|records| select_cascaded_font_rule(records))
                .and_then(|record| record.font.as_deref()),
            Some("OEBPS/Fonts/target.ttf")
        );
    }

    #[test]
    fn applies_inline_font_and_custom_property_precedence() {
        let stylesheet =
            parse_font_stylesheet(".target { --font: serif; font-family: TargetFont !important; }")
                .expect("stylesheet should parse");
        let fonts = BTreeMap::from([(
            "targetfont".to_string(),
            "OEBPS/Fonts/target.ttf".to_string(),
        )]);
        let index = build_font_rule_index(
            "<p class=\"target\" style=\"--font: TargetFont; font-family: var(--font);\">甲</p>",
            &stylesheet,
            &fonts,
        )
        .expect("inline declarations should parse");

        assert_eq!(
            index
                .values()
                .next()
                .and_then(|records| select_cascaded_font_rule(records))
                .and_then(|record| record.font.as_deref()),
            Some("OEBPS/Fonts/target.ttf"),
            "stylesheet !important must win over normal inline font-family"
        );
    }

    #[test]
    fn inline_custom_property_overrides_normal_stylesheet_value() {
        let stylesheet =
            parse_font_stylesheet(".target { --font: serif; font-family: var(--font); }")
                .expect("stylesheet should parse");
        let fonts = BTreeMap::from([(
            "targetfont".to_string(),
            "OEBPS/Fonts/target.ttf".to_string(),
        )]);
        let index = build_font_rule_index(
            "<p class=\"target\" style=\"--font: TargetFont;\">甲</p>",
            &stylesheet,
            &fonts,
        )
        .expect("inline custom property should resolve");

        assert_eq!(
            index
                .values()
                .next()
                .and_then(|records| select_cascaded_font_rule(records))
                .and_then(|record| record.font.as_deref()),
            Some("OEBPS/Fonts/target.ttf")
        );
    }
}
