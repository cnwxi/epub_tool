//! Selector matching probe for the future Rust font tasks.
//!
//! The Python font implementations use `cssselect2` against EPUB XHTML.  This
//! module keeps Rust's candidate selector engine behind a small, testable API
//! until its behaviour has passed the full font-task regression suite.

use dom_query::{Document, Matcher};

/// Counts the XHTML elements matched by one CSS selector.
///
/// Invalid selectors are reported instead of being silently ignored so a
/// future native font task can deliberately fall back to the Python sidecar.
pub fn matching_element_count(xhtml: &str, selector: &str) -> Result<usize, String> {
    let matcher = Matcher::new(selector)
        .map_err(|error| format!("不支持的 CSS 选择器 {selector:?}: {error:?}"))?;
    let document = Document::from(xhtml);
    Ok(document.select_matcher(&matcher).length())
}

#[cfg(test)]
mod tests {
    use super::matching_element_count;

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
}
