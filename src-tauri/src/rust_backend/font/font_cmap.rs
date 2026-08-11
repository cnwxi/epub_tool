//! Unicode cmap helpers used by the font-encryption migration.

use std::collections::BTreeMap;
use ttf_parser::Face;
use write_fonts::{read::FontRef, tables::cmap::Cmap, types::GlyphId, FontBuilder};

pub fn unicode_cmap(data: &[u8]) -> Result<BTreeMap<u32, u16>, String> {
    let decoded = decode_font_container(data)?;
    unicode_cmap_sfnt(&decoded.sfnt)
}

pub fn sfnt_data(data: &[u8]) -> Result<Vec<u8>, String> {
    decode_font_container(data).map(|decoded| decoded.sfnt)
}

fn unicode_cmap_sfnt(data: &[u8]) -> Result<BTreeMap<u32, u16>, String> {
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
    let decoded = decode_font_container(data)?;
    let mut mappings = unicode_cmap_sfnt(&decoded.sfnt)?;
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
    let font = FontRef::new(&decoded.sfnt).map_err(|error| format!("读取字体失败: {error}"))?;
    let mut builder = FontBuilder::new();
    builder
        .add_table(&cmap)
        .map_err(|error| format!("写入 cmap 失败: {error}"))?
        .copy_missing_tables(font);
    let rewritten = builder.build();

    // Parse the result before returning it, so callers cannot accidentally
    // package malformed font data when a writer behaviour changes.
    unicode_cmap_sfnt(&rewritten)?;
    encode_font_container(rewritten, decoded.container)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FontContainer {
    Sfnt,
    Woff1 { major: u16, minor: u16 },
    Woff2,
}

struct DecodedFont {
    sfnt: Vec<u8>,
    container: FontContainer,
}

fn decode_font_container(data: &[u8]) -> Result<DecodedFont, String> {
    let signature = data.get(..4).unwrap_or_default();
    if signature == b"wOFF" {
        let major = data
            .get(20..22)
            .and_then(|value| value.try_into().ok())
            .map(u16::from_be_bytes)
            .ok_or_else(|| "WOFF1 头部不完整".to_string())?;
        let minor = data
            .get(22..24)
            .and_then(|value| value.try_into().ok())
            .map(u16::from_be_bytes)
            .ok_or_else(|| "WOFF1 头部不完整".to_string())?;
        let sfnt =
            woff::version1::decompress(data).ok_or_else(|| "无法解压 WOFF1 字体".to_string())?;
        return Ok(DecodedFont {
            sfnt,
            container: FontContainer::Woff1 { major, minor },
        });
    }
    if signature == b"wOF2" {
        let sfnt =
            woff::version2::decompress(data).ok_or_else(|| "无法解压 WOFF2 字体".to_string())?;
        return Ok(DecodedFont {
            sfnt,
            container: FontContainer::Woff2,
        });
    }
    Ok(DecodedFont {
        sfnt: data.to_vec(),
        container: FontContainer::Sfnt,
    })
}

fn encode_font_container(sfnt: Vec<u8>, container: FontContainer) -> Result<Vec<u8>, String> {
    match container {
        FontContainer::Sfnt => Ok(sfnt),
        FontContainer::Woff1 { major, minor } => {
            woff::version1::compress(&sfnt, usize::from(major), usize::from(minor))
                .ok_or_else(|| "无法重新编码 WOFF1 字体".to_string())
        }
        FontContainer::Woff2 => woff::version2::compress(&sfnt, "", 8, true)
            .ok_or_else(|| "无法重新编码 WOFF2 字体".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::{rewrite_unicode_cmap, sfnt_data, unicode_cmap};
    use std::collections::BTreeMap;

    fn true_type_fixture() -> Vec<u8> {
        let hex = include_str!("../../../tests/data/ttf-parser-demo.ttf.hex")
            .bytes()
            .filter(|byte| !byte.is_ascii_whitespace())
            .collect::<Vec<_>>();
        assert_eq!(hex.len() % 2, 0, "TrueType fixture hex must be even");
        hex.chunks_exact(2)
            .map(|pair| {
                let pair = std::str::from_utf8(pair).expect("ASCII hex fixture");
                u8::from_str_radix(pair, 16).expect("valid TrueType fixture hex")
            })
            .collect()
    }

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

    #[test]
    fn reads_and_rewrites_ttf_and_otf_in_the_original_sfnt_flavor() {
        let ttf = true_type_fixture();
        for (signature, font) in [
            (b"\0\x01\0\0".as_slice(), ttf.as_slice()),
            (b"OTTO".as_slice(), blitz_dom::BULLET_FONT),
        ] {
            let cmap = unicode_cmap(font).expect("source cmap");
            assert!(!cmap.is_empty());
            let rewritten = rewrite_unicode_cmap(font, &BTreeMap::new(), &[])
                .expect("rewrite uncompressed font");
            assert_eq!(&rewritten[..4], signature);
            assert!(unicode_cmap(&rewritten).is_ok());
        }
    }

    #[test]
    fn reads_and_rewrites_woff1_and_woff2_in_the_original_container() {
        let sfnt = blitz_dom::BULLET_FONT;
        let cmap = unicode_cmap(sfnt).expect("bundled OpenType cmap");
        for (signature, compressed) in [
            (
                b"wOFF".as_slice(),
                woff::version1::compress(sfnt, 1, 0).expect("WOFF1 encode"),
            ),
            (
                b"wOF2".as_slice(),
                woff::version2::compress(sfnt, "", 8, true).expect("WOFF2 encode"),
            ),
        ] {
            assert_eq!(unicode_cmap(&compressed).expect("compressed cmap"), cmap);
            let rewritten = rewrite_unicode_cmap(&compressed, &BTreeMap::new(), &[])
                .expect("compressed rewrite");
            assert_eq!(&rewritten[..4], signature);
            assert!(sfnt_data(&rewritten).is_ok());
        }
    }
}
