//! Unicode cmap helpers used by the font-encryption migration.

use std::collections::BTreeMap;
use ttf_parser::Face;
use write_fonts::{read::FontRef, tables::cmap::Cmap, types::GlyphId, FontBuilder};

pub fn unicode_cmap(data: &[u8]) -> Result<BTreeMap<u32, u16>, String> {
    let face = Face::parse(data, 0).map_err(|error| format!("无法解析字体: {error:?}"))?;
    let cmap = face
        .tables()
        .cmap
        .ok_or_else(|| "字体缺少 cmap 表".to_string())?;
    let mut mappings = BTreeMap::new();
    for subtable in cmap.subtables {
        if !subtable.is_unicode() {
            continue;
        }
        subtable.codepoints(|codepoint| {
            if let Some(character) = char::from_u32(codepoint) {
                if let Some(glyph) = face.glyph_index(character) {
                    mappings.insert(codepoint, glyph.0);
                }
            }
        });
    }
    if mappings.is_empty() {
        return Err("字体没有可用 Unicode cmap 映射".to_string());
    }
    Ok(mappings)
}

/// Rebuild the Unicode cmap while copying every other OpenType table verbatim.
///
/// This is deliberately a narrow primitive: callers must have already decided
/// that replacing the font's Unicode cmap is safe for the EPUB. Glyph data,
/// metrics, names, layout tables and all non-cmap tables are retained.
pub fn rewrite_unicode_cmap(
    data: &[u8],
    replacements: &BTreeMap<u32, u16>,
    removed_codepoints: &[u32],
) -> Result<Vec<u8>, String> {
    let mut mappings = unicode_cmap(data)?;
    for codepoint in removed_codepoints {
        mappings.remove(codepoint);
    }
    for (codepoint, glyph_id) in replacements {
        if char::from_u32(*codepoint).is_none() {
            return Err(format!("无效 Unicode 码位: U+{codepoint:04X}"));
        }
        mappings.insert(*codepoint, *glyph_id);
    }

    let cmap = Cmap::from_mappings(mappings.into_iter().filter_map(|(codepoint, glyph_id)| {
        char::from_u32(codepoint).map(|character| (character, GlyphId::new(u32::from(glyph_id))))
    }))
    .map_err(|error| format!("构建 cmap 失败: {error}"))?;
    let font = FontRef::new(data).map_err(|error| format!("读取字体失败: {error}"))?;
    let mut builder = FontBuilder::new();
    builder
        .add_table(&cmap)
        .map_err(|error| format!("写入 cmap 失败: {error}"))?
        .copy_missing_tables(font);
    let rewritten = builder.build();

    // Parse the result before returning it, so callers cannot accidentally
    // package malformed font data when a writer behaviour changes.
    unicode_cmap(&rewritten)?;
    Ok(rewritten)
}

#[cfg(test)]
mod tests {
    use super::{rewrite_unicode_cmap, unicode_cmap};
    use std::collections::BTreeMap;

    #[test]
    fn rejects_non_font_bytes() {
        assert!(unicode_cmap(b"not-a-font").is_err());
    }

    #[test]
    fn rejects_invalid_replacement_codepoint() {
        let mut replacements = BTreeMap::new();
        replacements.insert(0x11_0000, 1);
        assert!(rewrite_unicode_cmap(b"not-a-font", &replacements, &[]).is_err());
    }
}
