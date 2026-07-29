//! CSS cascade primitives shared by the future native font tasks.
//!
//! This mirrors the precedence calculation used by the Python font services.
//! Parsing stylesheets and applying rules to XHTML intentionally remains
//! separate: a task must not opt into Rust dispatch until both layers have
//! passed the complete Python regression suite.

const MAX_LAYER_ORDER: i64 = 1_000_000;
const MAX_SCOPE_PROXIMITY: i64 = 1_000_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct FontRulePrecedence {
    important: bool,
    layer_score: i64,
    specificity: [i64; 4],
    scope_score: i64,
    order: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FontRuleValue {
    Font,
    Blocker,
    Inherit,
    RevertLayer,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CascadedFontRule<T> {
    pub value: FontRuleValue,
    pub font: Option<T>,
    pub precedence: FontRulePrecedence,
}

/// Builds the font rule priority tuple used by the Python CSS cascade.
///
/// `specificity` is the ordinary three-part selector specificity. Inline
/// declarations use the additional inline component and therefore outrank any
/// non-important stylesheet declaration regardless of selector specificity.
pub fn font_rule_precedence(
    important: bool,
    specificity: [i64; 3],
    order: usize,
    is_inline: bool,
    layer_order: Option<usize>,
    scope_proximity: Option<usize>,
) -> FontRulePrecedence {
    let specificity = if is_inline {
        [1, 0, 0, 0]
    } else {
        [0, specificity[0], specificity[1], specificity[2]]
    };
    let layer_score = if is_inline && important {
        MAX_LAYER_ORDER * 2
    } else if important {
        -layer_order
            .and_then(|value| i64::try_from(value).ok())
            .unwrap_or(MAX_LAYER_ORDER)
    } else {
        layer_order
            .and_then(|value| i64::try_from(value).ok())
            .unwrap_or(MAX_LAYER_ORDER)
    };
    let scope_score = -scope_proximity
        .and_then(|value| i64::try_from(value).ok())
        .unwrap_or(MAX_SCOPE_PROXIMITY);
    FontRulePrecedence {
        important,
        layer_score,
        specificity,
        scope_score,
        order,
    }
}

/// Selects the effective declaration and applies CSS `revert-layer`.
///
/// The Python implementation removes the current cascade layer and all higher
/// layers of the same importance before selecting again.  Returning a
/// reference keeps the eventual rule-to-XHTML index allocation-free.
pub fn select_cascaded_font_rule<T>(rules: &[CascadedFontRule<T>]) -> Option<&CascadedFontRule<T>> {
    let mut candidates: Vec<_> = rules.iter().collect();
    while !candidates.is_empty() {
        candidates.sort_unstable_by(|left, right| right.precedence.cmp(&left.precedence));
        let selected = candidates[0];
        if selected.value != FontRuleValue::RevertLayer {
            return Some(selected);
        }
        let selected_precedence = selected.precedence;
        candidates.retain(|candidate| {
            !(candidate.precedence.important == selected_precedence.important
                && candidate.precedence.layer_score >= selected_precedence.layer_score)
        });
    }
    None
}

#[cfg(test)]
mod tests {
    use super::{font_rule_precedence, select_cascaded_font_rule, CascadedFontRule, FontRuleValue};

    fn rule(
        font: &'static str,
        value: FontRuleValue,
        important: bool,
        specificity: [i64; 3],
        order: usize,
        is_inline: bool,
        layer_order: Option<usize>,
        scope_proximity: Option<usize>,
    ) -> CascadedFontRule<&'static str> {
        CascadedFontRule {
            value,
            font: Some(font),
            precedence: font_rule_precedence(
                important,
                specificity,
                order,
                is_inline,
                layer_order,
                scope_proximity,
            ),
        }
    }

    #[test]
    fn important_then_inline_and_selector_specificity_match_python_precedence() {
        let normal_inline = rule(
            "inline",
            FontRuleValue::Font,
            false,
            [0, 0, 0],
            1,
            true,
            None,
            None,
        );
        let important_selector = rule(
            "important",
            FontRuleValue::Font,
            true,
            [0, 1, 0],
            2,
            false,
            None,
            None,
        );

        assert_eq!(
            select_cascaded_font_rule(&[normal_inline, important_selector])
                .and_then(|selected| selected.font),
            Some("important")
        );
    }

    #[test]
    fn unlayered_normal_rules_outrank_layered_normal_rules() {
        let layered = rule(
            "layered",
            FontRuleValue::Font,
            false,
            [1, 0, 0],
            9,
            false,
            Some(4),
            None,
        );
        let unlayered = rule(
            "unlayered",
            FontRuleValue::Font,
            false,
            [0, 0, 1],
            1,
            false,
            None,
            None,
        );

        assert_eq!(
            select_cascaded_font_rule(&[layered, unlayered]).and_then(|selected| selected.font),
            Some("unlayered")
        );
    }

    #[test]
    fn revert_layer_reveals_the_next_lower_layer_like_python() {
        let base = rule(
            "base",
            FontRuleValue::Font,
            false,
            [0, 1, 0],
            1,
            false,
            Some(0),
            None,
        );
        let revert = rule(
            "revert",
            FontRuleValue::RevertLayer,
            false,
            [0, 1, 0],
            2,
            false,
            Some(1),
            None,
        );
        let later = rule(
            "later",
            FontRuleValue::Font,
            false,
            [0, 1, 0],
            1,
            false,
            Some(1),
            None,
        );

        assert_eq!(
            select_cascaded_font_rule(&[base, revert, later]).and_then(|selected| selected.font),
            Some("base")
        );
    }

    #[test]
    fn closer_scope_outranks_source_order() {
        let outer = rule(
            "outer",
            FontRuleValue::Font,
            false,
            [0, 1, 0],
            2,
            false,
            None,
            Some(3),
        );
        let inner = rule(
            "inner",
            FontRuleValue::Font,
            false,
            [0, 1, 0],
            1,
            false,
            None,
            Some(1),
        );

        assert_eq!(
            select_cascaded_font_rule(&[outer, inner]).and_then(|selected| selected.font),
            Some("inner")
        );
    }
}
