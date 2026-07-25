//! Model-independent building blocks for `encrypt_font`.
//!
//! Full font encryption still depends on CSS cascade resolution and XHTML text
//! rewriting, so it is intentionally not registered for production dispatch.

use super::font_cmap::rewrite_unicode_cmap;
use std::collections::BTreeMap;

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

#[cfg(test)]
mod tests {
    use super::rewrite_cmap_for_obfuscation;
    use std::collections::BTreeMap;

    #[test]
    fn rejects_non_font_input() {
        assert!(rewrite_cmap_for_obfuscation(b"not-a-font", &BTreeMap::new(), "你").is_err());
    }
}
