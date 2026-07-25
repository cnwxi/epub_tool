//! Model-independent building blocks for `decrypt_font`.
//!
//! OCR inference, glyph rendering and CSS cascade resolution deliberately stay
//! on the Python sidecar until their output has complete EPUB regressions.

use std::collections::BTreeMap;

const OCR_PERIOD_ALIASES: [char; 3] = ['.', '．', '｡'];
const OCR_HANGUL_OBFUSCATION_START: u32 = 0xAC00;
const OCR_HANGUL_OBFUSCATION_END: u32 = 0xD7AF;

#[derive(Debug, Clone, PartialEq)]
pub struct OcrTextResult {
    pub text: String,
    pub confidence: f32,
}

/// Decodes the first batch item of a CTC recognition output.
///
/// `prediction` is indexed as `[time_step][token_id]`, where token zero is
/// the CTC blank. It mirrors Python's `OnnxGlyphOcrBackend.decode_prediction`:
/// repeated non-blank tokens are collapsed, blank tokens reset repetition, and
/// confidence is the mean selected-token score.
pub fn decode_ctc_prediction(prediction: &[Vec<f32>], characters: &[String]) -> OcrTextResult {
    let mut text = String::new();
    let mut scores = Vec::new();
    let mut previous = None;
    for timestep in prediction {
        let Some((token_id, score)) = timestep
            .iter()
            .enumerate()
            .max_by(|(_, left), (_, right)| left.total_cmp(right))
        else {
            continue;
        };
        if token_id == 0 || previous == Some(token_id) {
            previous = Some(token_id);
            continue;
        }
        previous = Some(token_id);
        if let Some(character) = characters.get(token_id) {
            text.push_str(character);
            scores.push(*score);
        }
    }
    let confidence = (!scores.is_empty())
        .then(|| scores.iter().sum::<f32>() / scores.len() as f32)
        .unwrap_or(0.0);
    OcrTextResult { text, confidence }
}

pub fn format_ocr_progress(processed_count: usize, total_count: usize) -> String {
    if total_count == 0 {
        return String::new();
    }
    format!(
        "，进度 {processed_count}/{total_count} ({:.1}%)",
        processed_count as f64 / total_count as f64 * 100.0
    )
}

pub fn is_ocr_obfuscation_hint_char(character: char) -> bool {
    let codepoint = character as u32;
    matches!(character, '\u{E000}'..='\u{F8FF}')
        || (OCR_HANGUL_OBFUSCATION_START..=OCR_HANGUL_OBFUSCATION_END).contains(&codepoint)
}

pub fn normalize_ocr_text(text: &str, hint_char: Option<char>, period_like_glyph: bool) -> String {
    let normalized: String = text
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    if normalized.chars().count() != 1 {
        return normalized;
    }
    let Some(hint_char) = hint_char else {
        return normalized;
    };
    if !is_ocr_obfuscation_hint_char(hint_char) {
        return normalized;
    }
    let character = normalized
        .chars()
        .next()
        .expect("one character checked above");
    if OCR_PERIOD_ALIASES.contains(&character) || (character == '0' && period_like_glyph) {
        "。".to_string()
    } else {
        normalized
    }
}

pub fn filter_text_by_cmap(text: &str, cmap: Option<&BTreeMap<u32, u16>>) -> String {
    let Some(cmap) = cmap else {
        return text.to_string();
    };
    text.chars()
        .filter(|character| cmap.contains_key(&(*character as u32)))
        .collect()
}

pub fn format_ocr_failure_placeholder(character: char, status_code: &str) -> String {
    format!("[U+{:04X} {status_code}]", character as u32)
}

#[cfg(test)]
mod tests {
    use super::{
        decode_ctc_prediction, filter_text_by_cmap, format_ocr_failure_placeholder,
        format_ocr_progress, is_ocr_obfuscation_hint_char, normalize_ocr_text,
    };
    use std::collections::BTreeMap;

    #[test]
    fn normalizes_period_aliases_only_for_obfuscated_hints() {
        assert_eq!(normalize_ocr_text("．", Some('\u{E000}'), false), "。");
        assert_eq!(normalize_ocr_text("0", Some('\u{D73C}'), true), "。");
        assert_eq!(normalize_ocr_text("0", Some('０'), true), "0");
        assert_eq!(normalize_ocr_text(" 你 ", Some('\u{E000}'), false), "你");
        assert!(is_ocr_obfuscation_hint_char('\u{D73C}'));
    }

    #[test]
    fn filters_only_characters_present_in_font_cmap() {
        let cmap = BTreeMap::from([(u32::from('你'), 1_u16), (u32::from('A'), 2_u16)]);
        assert_eq!(filter_text_by_cmap("你缺A", Some(&cmap)), "你A");
        assert_eq!(filter_text_by_cmap("你缺A", None), "你缺A");
    }

    #[test]
    fn formats_failure_placeholders_like_python() {
        assert_eq!(
            format_ocr_failure_placeholder('\u{E000}', "OCR_LOW_CONF"),
            "[U+E000 OCR_LOW_CONF]"
        );
    }

    #[test]
    fn decodes_ctc_output_like_python_onnx_backend() {
        let characters = vec![
            "blank".to_string(),
            "你".to_string(),
            "好".to_string(),
            " ".to_string(),
        ];
        let prediction = vec![
            vec![0.0, 0.9, 0.0, 0.0],
            vec![0.0, 0.8, 0.0, 0.0],
            vec![0.7, 0.0, 0.0, 0.0],
            vec![0.0, 0.0, 0.95, 0.0],
            vec![0.6, 0.0, 0.0, 0.0],
        ];
        let result = decode_ctc_prediction(&prediction, &characters);
        assert_eq!(result.text, "你好");
        assert!((result.confidence - 0.925).abs() < f32::EPSILON);
    }

    #[test]
    fn formats_ocr_progress_like_python() {
        assert_eq!(format_ocr_progress(3, 12), "，进度 3/12 (25.0%)");
        assert_eq!(format_ocr_progress(0, 0), "");
    }
}
