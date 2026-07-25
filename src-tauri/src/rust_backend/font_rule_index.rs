//! Maps conservative CSS font rules to XHTML DOM nodes.
//!
//! This is the bridge between stylesheet parsing, selector matching and the
//! shared cascade primitives. It refuses declarations that still require
//! custom-property computation or unsupported selector specificity.

use super::{
    font_cascade::{font_rule_precedence, CascadedFontRule, FontRuleValue},
    font_selectors::simple_selector_specificity,
    font_stylesheet::{CssDeclaration, FontStylesheet},
    font_values::{normalize_font_family, parse_font_value, ParsedFontValue},
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
    let mut index = FontRuleIndex::new();
    for rule in &stylesheet.rules {
        let specificity = simple_selector_specificity(&rule.selector)?;
        let declaration = select_font_declaration(&rule.declarations)?;
        let Some(declaration) = declaration else {
            continue;
        };
        let cascaded = declaration_to_rule(
            declaration,
            specificity,
            rule.source_order,
            font_family_to_file,
        )?;
        let matcher = Matcher::new(&rule.selector)
            .map_err(|error| format!("不支持的 CSS 选择器 {:?}: {error:?}", rule.selector))?;
        for node in document.select_matcher(&matcher).nodes() {
            index.entry(node.id).or_default().push(cascaded.clone());
        }
    }
    Ok(index)
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
            return Err("all 属性需要完整 CSS reset 语义，需使用 Python 兼容实现".to_string());
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
    source_order: usize,
    font_family_to_file: &BTreeMap<String, String>,
) -> Result<CascadedFontRule<String>, String> {
    let value = parse_font_value(&declaration.name, &declaration.value)?;
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
            return Err("CSS reset 字体值需要 Python 兼容实现".to_string());
        }
        ParsedFontValue::NeedsCustomPropertyResolution => {
            return Err("CSS 自定义属性尚未索引到 XHTML 元素，需使用 Python 兼容实现".to_string());
        }
    };
    Ok(CascadedFontRule {
        value,
        font,
        precedence: font_rule_precedence(
            declaration.important,
            specificity,
            source_order
                .checked_mul(1_000)
                .and_then(|value| value.checked_add(declaration.declaration_order))
                .ok_or_else(|| "CSS 规则顺序超出范围".to_string())?,
            false,
            None,
            None,
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::build_font_rule_index;
    use crate::rust_backend::{
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
    fn rejects_custom_property_rules_until_element_inheritance_is_available() {
        let stylesheet =
            parse_font_stylesheet(".target { --font: TargetFont; font-family: var(--font); }")
                .expect("stylesheet should parse");
        assert!(
            build_font_rule_index("<p class=\"target\">甲</p>", &stylesheet, &BTreeMap::new(),)
                .is_err()
        );
    }
}
