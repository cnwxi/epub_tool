//! Selector matching probe for the future Rust font tasks.
//!
//! The Python font implementations use `cssselect2` against EPUB XHTML.  This
//! module keeps Rust's candidate selector engine behind a small, testable API
//! until its behaviour has passed the full font-task regression suite.

use dom_query::{Document, Matcher};

/// Counts the XHTML elements matched by one CSS selector.
///
/// Invalid selectors are reported instead of being silently ignored.
pub fn matching_element_count(xhtml: &str, selector: &str) -> Result<usize, String> {
    let matcher = Matcher::new(selector)
        .map_err(|error| format!("不支持的 CSS 选择器 {selector:?}: {error:?}"))?;
    let document = Document::from(xhtml);
    Ok(document.select_matcher(&matcher).length())
}

/// Returns specificity for the conservative selector subset used by the first
/// native font-rule index. Unsupported selector features are an explicit
/// compatibility signal, not an approximation.
pub fn simple_selector_specificity(selector: &str) -> Result<[i64; 3], String> {
    let mut ids = 0_i64;
    let mut classes = 0_i64;
    let mut types = 0_i64;
    let mut expect_compound_start = true;
    let mut characters = selector.trim().chars().peekable();
    if characters.peek().is_none() {
        return Err("CSS 选择器不能为空".to_string());
    }
    while let Some(character) = characters.next() {
        if character.is_whitespace() {
            expect_compound_start = true;
            while characters.peek().is_some_and(|next| next.is_whitespace()) {
                characters.next();
            }
            continue;
        }
        match character {
            '>' | '+' | '~' => expect_compound_start = true,
            '#' => {
                consume_identifier(&mut characters)
                    .ok_or_else(|| "CSS id 选择器缺少名称".to_string())?;
                ids += 1;
                expect_compound_start = false;
            }
            '.' => {
                consume_identifier(&mut characters)
                    .ok_or_else(|| "CSS class 选择器缺少名称".to_string())?;
                classes += 1;
                expect_compound_start = false;
            }
            '*' if expect_compound_start => expect_compound_start = false,
            ':' | '[' | ']' | '|' | ',' => {
                return Err(format!("CSS 选择器功能尚未完成迁移: {selector}"));
            }
            _ if expect_compound_start && is_identifier_start(character) => {
                consume_identifier_tail(&mut characters);
                types += 1;
                expect_compound_start = false;
            }
            _ => return Err(format!("CSS 选择器包含不支持的 token: {selector}")),
        }
    }
    (!expect_compound_start)
        .then_some([ids, classes, types])
        .ok_or_else(|| format!("CSS 选择器不完整: {selector}"))
}

fn consume_identifier(characters: &mut std::iter::Peekable<std::str::Chars<'_>>) -> Option<()> {
    let first = characters.next()?;
    if !is_identifier_start(first) {
        return None;
    }
    consume_identifier_tail(characters);
    Some(())
}

fn consume_identifier_tail(characters: &mut std::iter::Peekable<std::str::Chars<'_>>) {
    while characters
        .peek()
        .is_some_and(|character| is_identifier_continue(*character))
    {
        characters.next();
    }
}

fn is_identifier_start(character: char) -> bool {
    character.is_ascii_alphabetic() || character == '_' || character == '-' || !character.is_ascii()
}

fn is_identifier_continue(character: char) -> bool {
    is_identifier_start(character) || character.is_ascii_digit()
}

#[cfg(test)]
mod tests {
    use super::{matching_element_count, simple_selector_specificity};

    #[test]
    fn matches_the_python_complex_selector_golden_vector() {
        let xhtml = r#"<html lang="zh-Hans"><body>
<p class="target">甲</p><p class="target">乙</p><p class="target">丙</p>
</body></html>"#;

        assert_eq!(
            matching_element_count(xhtml, r#"[lang|="zh"] .target:nth-child(2n+1)"#),
            Ok(2)
        );
    }

    #[test]
    fn matches_the_python_xhtml_nth_of_type_golden_vector() {
        let xhtml = r#"<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<br /><p class="target">甲</p><p class="target">乙</p>
</body></html>"#;

        assert_eq!(
            matching_element_count(xhtml, "body > p.target:nth-of-type(2)"),
            Ok(1)
        );
    }

    #[test]
    fn matches_functional_selector_lists_used_by_python() {
        let xhtml = r#"<html><body><div class="wrapper">
<p class="target">甲</p><p class="other">乙</p><p>丙</p>
</div></body></html>"#;

        assert_eq!(
            matching_element_count(xhtml, ".wrapper :is(.target, .other)"),
            Ok(2)
        );
    }

    #[test]
    fn reports_invalid_selectors_for_python_fallback() {
        assert!(matching_element_count("<p>甲</p>", ".target[").is_err());
    }

    #[test]
    fn calculates_specificity_for_the_initial_native_subset() {
        assert_eq!(
            simple_selector_specificity("body > p.target"),
            Ok([0, 1, 2])
        );
        assert_eq!(
            simple_selector_specificity("#chapter .target.sys"),
            Ok([1, 2, 0])
        );
        assert!(simple_selector_specificity(":is(.target, .other)").is_err());
    }
}
