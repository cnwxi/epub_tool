//! Character filtering and cmap rebinding plans for `encrypt_font`.
//!
//! Punctuation, symbols and whitespace are preserved because changing their
//! codepoints can affect EPUB line-breaking. This module uses Unicode general
//! category and East Asian Width data before a font's cmap is rewritten.

use icu_properties::{
    props::{EastAsianWidth, GeneralCategory},
    CodePointMapData,
};
use rand::{seq::SliceRandom, Rng};
use std::collections::{BTreeMap, BTreeSet};

const HANGUL_START: u32 = 0xAC00;
const HANGUL_END: u32 = 0xD7AF;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObfuscationText {
    pub obfuscate: String,
    pub passthrough: String,
}

/// Applies the stable character policy while preserving first occurrence order.
/// Control and format characters are omitted entirely.
pub fn split_obfuscation_text(text: &str) -> ObfuscationText {
    let categories = CodePointMapData::<GeneralCategory>::new();
    let widths = CodePointMapData::<EastAsianWidth>::new();
    let mut obfuscate = String::new();
    let mut passthrough = String::new();
    let mut seen_obfuscate = BTreeSet::new();
    let mut seen_passthrough = BTreeSet::new();
    for character in text.chars() {
        let category = categories.get(character);
        if is_control_category(category) {
            continue;
        }
        let target = if should_obfuscate(character, category, widths.get(character)) {
            (&mut obfuscate, &mut seen_obfuscate)
        } else {
            (&mut passthrough, &mut seen_passthrough)
        };
        if target.1.insert(character) {
            target.0.push(character);
        }
    }
    ObfuscationText {
        obfuscate,
        passthrough,
    }
}

/// Builds a randomized one-to-one source-to-obfuscation-codepoint mapping.
///
/// `preserved_text` codepoints are excluded so punctuation and symbols left in
/// XHTML cannot collide with glyphs rebound by the rewritten cmap.
pub fn build_obfuscation_mapping<R: Rng + ?Sized>(
    plain_text: &str,
    preserved_text: &str,
    rng: &mut R,
) -> Result<BTreeMap<u32, u32>, String> {
    let categories = CodePointMapData::<GeneralCategory>::new();
    let widths = CodePointMapData::<EastAsianWidth>::new();
    let preserved = preserved_text
        .chars()
        .map(u32::from)
        .collect::<BTreeSet<_>>();
    let mut groups = BTreeMap::<PoolKind, Vec<u32>>::new();
    for character in plain_text.chars() {
        let codepoint = u32::from(character);
        let pool = pool_for(character, categories.get(character), widths.get(character))
            .ok_or_else(|| format!("字符不符合字体混淆规则: U+{codepoint:04X}"))?;
        let group = groups.entry(pool).or_default();
        if !group.contains(&codepoint) {
            group.push(codepoint);
        }
    }

    let mut mapping = BTreeMap::new();
    for (pool, source) in groups {
        let targets = select_pool_targets(&source, pool.codepoints(), &preserved, rng)?;
        mapping.extend(source.into_iter().zip(targets));
    }
    Ok(mapping)
}

pub fn html_entity_mapping(mapping: &BTreeMap<u32, u32>) -> BTreeMap<char, String> {
    mapping
        .iter()
        .filter_map(|(source, target)| {
            char::from_u32(*source).map(|character| (character, format!("&#x{target:x}")))
        })
        .collect()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
enum PoolKind {
    AsciiAlnum,
    FullwidthAlnum,
    Layout,
}

impl PoolKind {
    fn codepoints(self) -> Vec<u32> {
        match self {
            Self::AsciiAlnum => "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                .chars()
                .map(u32::from)
                .collect(),
            Self::FullwidthAlnum => (u32::from('０')..=u32::from('９'))
                .chain(u32::from('Ａ')..=u32::from('Ｚ'))
                .chain(u32::from('ａ')..=u32::from('ｚ'))
                .collect(),
            Self::Layout => {
                let categories = CodePointMapData::<GeneralCategory>::new();
                let widths = CodePointMapData::<EastAsianWidth>::new();
                (HANGUL_START..=HANGUL_END)
                    .filter_map(char::from_u32)
                    .filter(|character| {
                        is_letter_or_number(categories.get(*character))
                            && is_wide(widths.get(*character))
                    })
                    .map(u32::from)
                    .collect()
            }
        }
    }
}

fn select_pool_targets<R: Rng + ?Sized>(
    source: &[u32],
    mut candidates: Vec<u32>,
    preserved: &BTreeSet<u32>,
    rng: &mut R,
) -> Result<Vec<u32>, String> {
    candidates.retain(|candidate| !preserved.contains(candidate));
    if source.len() > candidates.len() {
        return Err(format!(
            "可用同类混淆码位不足，需要 {} 个，最多 {} 个",
            source.len(),
            candidates.len()
        ));
    }
    candidates.shuffle(rng);
    let mut used = BTreeSet::new();
    let mut targets = Vec::with_capacity(source.len());
    for source_codepoint in source {
        let target = candidates
            .iter()
            .copied()
            .find(|candidate| !used.contains(candidate) && candidate != source_codepoint)
            .or_else(|| {
                candidates
                    .iter()
                    .copied()
                    .find(|candidate| !used.contains(candidate))
            })
            .ok_or_else(|| "可用同类混淆码位不足".to_string())?;
        used.insert(target);
        targets.push(target);
    }
    Ok(targets)
}

fn pool_for(character: char, category: GeneralCategory, width: EastAsianWidth) -> Option<PoolKind> {
    if character.is_ascii_alphanumeric() {
        Some(PoolKind::AsciiAlnum)
    } else if matches!(character, '０'..='９' | 'Ａ'..='Ｚ' | 'ａ'..='ｚ') {
        Some(PoolKind::FullwidthAlnum)
    } else if is_letter_or_number(category) && is_wide(width) {
        Some(PoolKind::Layout)
    } else {
        None
    }
}

fn should_obfuscate(character: char, category: GeneralCategory, width: EastAsianWidth) -> bool {
    pool_for(character, category, width).is_some()
}

fn is_letter_or_number(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::UppercaseLetter
            | GeneralCategory::LowercaseLetter
            | GeneralCategory::TitlecaseLetter
            | GeneralCategory::ModifierLetter
            | GeneralCategory::OtherLetter
            | GeneralCategory::DecimalNumber
            | GeneralCategory::LetterNumber
            | GeneralCategory::OtherNumber
    )
}

fn is_control_category(category: GeneralCategory) -> bool {
    matches!(
        category,
        GeneralCategory::Control
            | GeneralCategory::Format
            | GeneralCategory::PrivateUse
            | GeneralCategory::Surrogate
            | GeneralCategory::Unassigned
    )
}

fn is_wide(width: EastAsianWidth) -> bool {
    matches!(width, EastAsianWidth::Wide | EastAsianWidth::Fullwidth)
}

#[cfg(test)]
mod tests {
    use super::{build_obfuscation_mapping, html_entity_mapping, split_obfuscation_text};
    use rand::{rngs::StdRng, SeedableRng};
    use std::collections::BTreeSet;

    #[test]
    fn preserves_the_stable_clean_text_character_policy() {
        assert_eq!(
            split_obfuscation_text("AＡ中中， 。\u{0001}"),
            super::ObfuscationText {
                obfuscate: "AＡ中".to_string(),
                passthrough: "， 。".to_string(),
            }
        );
    }

    #[test]
    fn creates_non_colliding_same_class_obfuscation_mapping() {
        let mut rng = StdRng::seed_from_u64(42);
        let mapping = build_obfuscation_mapping("AＢ中文", "，。", &mut rng).expect("mapping");
        assert_eq!(mapping.len(), 4);
        assert!(mapping.iter().all(|(source, target)| source != target));
        assert_eq!(
            mapping.values().collect::<BTreeSet<_>>().len(),
            mapping.len()
        );
        assert!(mapping.values().all(|target| *target != u32::from('，')));
        assert_eq!(html_entity_mapping(&mapping).len(), mapping.len());
    }
}
