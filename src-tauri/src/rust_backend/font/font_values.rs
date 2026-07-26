//! CSS font declaration value parsing for the native font-task migration.
//!
//! The parser deliberately returns an error for syntax it cannot preserve
//! rather than guessing which packaged font applies.

use std::collections::{BTreeMap, BTreeSet};

const GENERIC_FONT_FAMILIES: &[&str] = &[
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "emoji",
    "math",
    "fangsong",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
];

const FONT_SIZE_KEYWORDS: &[&str] = &[
    "xx-small",
    "x-small",
    "small",
    "medium",
    "large",
    "x-large",
    "xx-large",
    "xxx-large",
    "larger",
    "smaller",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParsedFontValue {
    Candidates(Vec<String>),
    Inherit,
    RevertLayer,
    /// `initial` and `revert` are handled by the cascade, not as a font name.
    Reset,
    /// A custom property must be resolved against the target XHTML element.
    NeedsCustomPropertyResolution,
}

/// Parses a `font-family`, `font`, or `all` declaration value.
pub fn parse_font_value(property_name: &str, value: &str) -> Result<ParsedFontValue, String> {
    let property_name = property_name.trim().to_ascii_lowercase();
    if !matches!(property_name.as_str(), "font-family" | "font" | "all") {
        return Err(format!("不是字体相关 CSS 属性: {property_name}"));
    }
    let value = trim_important(value)?;
    if contains_var_function(value)? {
        return Ok(ParsedFontValue::NeedsCustomPropertyResolution);
    }
    if let Some(keyword) = css_wide_keyword(value) {
        return Ok(match keyword {
            "inherit" | "unset" => ParsedFontValue::Inherit,
            "revert-layer" => ParsedFontValue::RevertLayer,
            "initial" | "revert" => ParsedFontValue::Reset,
            _ => unreachable!("css_wide_keyword only returns known values"),
        });
    }
    match property_name.as_str() {
        "font-family" => Ok(ParsedFontValue::Candidates(parse_font_family_list(value)?)),
        "font" => {
            let family = font_shorthand_family(value)?;
            Ok(ParsedFontValue::Candidates(parse_font_family_list(
                &family,
            )?))
        }
        "all" => Err("all 属性的非全局值不会指定 font-family".to_string()),
        _ => unreachable!(),
    }
}

/// Resolves CSS custom properties in a font declaration and then parses it.
///
/// Values are intentionally supplied by the cascade layer instead of reading
/// a stylesheet here: CSS custom properties inherit and need the target XHTML
/// element's computed property map.
pub fn resolve_font_value(
    property_name: &str,
    value: &str,
    custom_properties: &BTreeMap<String, String>,
) -> Result<ParsedFontValue, String> {
    let value = resolve_css_variables(value, custom_properties)?;
    parse_font_value(property_name, &value)
}

/// Resolves `var(--name[, fallback])` references recursively.
///
/// Missing variables, circular references and malformed CSS are errors. This
/// mirrors the Python service's invalid-at-computed-value behaviour: its font
/// cascade can then inherit from the parent instead of treating the raw string
/// as a font family name.
pub fn resolve_css_variables(
    value: &str,
    custom_properties: &BTreeMap<String, String>,
) -> Result<String, String> {
    resolve_css_variables_inner(value, custom_properties, &BTreeSet::new())
}

/// Normalizes a font family exactly as the Python services do for lookups.
pub fn normalize_font_family(value: &str) -> String {
    value
        .trim()
        .trim_matches(['\'', '"'])
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_ascii_lowercase()
}

pub fn is_generic_font_family(value: &str) -> bool {
    GENERIC_FONT_FAMILIES.contains(&normalize_font_family(value).as_str())
}

fn trim_important(value: &str) -> Result<&str, String> {
    let segments = split_top_level(value, ';')?;
    if segments.len() != 1 {
        return Err("字体声明值不能包含顶层分号".to_string());
    }
    let value = value.trim();
    let lower = value.to_ascii_lowercase();
    if let Some(position) = lower.rfind("!important") {
        if lower[position + "!important".len()..].trim().is_empty() {
            return Ok(value[..position].trim());
        }
    }
    Ok(value)
}

fn css_wide_keyword(value: &str) -> Option<&'static str> {
    let value = value.trim();
    (!value.contains(char::is_whitespace))
        .then(|| value.to_ascii_lowercase())
        .and_then(|value| match value.as_str() {
            "inherit" => Some("inherit"),
            "initial" => Some("initial"),
            "unset" => Some("unset"),
            "revert" => Some("revert"),
            "revert-layer" => Some("revert-layer"),
            _ => None,
        })
}

fn contains_var_function(value: &str) -> Result<bool, String> {
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if bytes[index..].len() >= 4
            && bytes[index..index + 4].eq_ignore_ascii_case(b"var(")
            && (index == 0 || !is_ident_continue(bytes[index - 1] as char))
        {
            return Ok(true);
        }
        index += 1;
    }
    Ok(false)
}

fn resolve_css_variables_inner(
    value: &str,
    custom_properties: &BTreeMap<String, String>,
    seen: &BTreeSet<String>,
) -> Result<String, String> {
    let bytes = value.as_bytes();
    let mut output = String::with_capacity(value.len());
    let mut copied_until = 0;
    let mut index = 0;
    while index < bytes.len() {
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if is_var_function_at(bytes, index) {
            let close = matching_paren(bytes, index + 3)?;
            output.push_str(&value[copied_until..index]);
            let arguments = &value[index + 4..close];
            let (name, fallback) = split_var_arguments(arguments)?;
            let replacement = if let Some(property_value) = custom_properties.get(name) {
                if seen.contains(name) {
                    resolve_var_fallback(fallback, custom_properties, seen)?
                } else {
                    let mut next_seen = seen.clone();
                    next_seen.insert(name.to_string());
                    resolve_css_variables_inner(property_value, custom_properties, &next_seen)?
                }
            } else {
                resolve_var_fallback(fallback, custom_properties, seen)?
            };
            output.push_str(&replacement);
            index = close + 1;
            copied_until = index;
            continue;
        }
        index += 1;
    }
    output.push_str(&value[copied_until..]);
    Ok(output)
}

fn resolve_var_fallback(
    fallback: Option<&str>,
    custom_properties: &BTreeMap<String, String>,
    seen: &BTreeSet<String>,
) -> Result<String, String> {
    let fallback = fallback.ok_or_else(|| "未定义或循环的 CSS 自定义属性".to_string())?;
    resolve_css_variables_inner(fallback, custom_properties, seen)
}

fn is_var_function_at(bytes: &[u8], index: usize) -> bool {
    bytes[index..].len() >= 4
        && bytes[index..index + 4].eq_ignore_ascii_case(b"var(")
        && (index == 0 || !is_ident_continue(bytes[index - 1] as char))
}

fn split_var_arguments(arguments: &str) -> Result<(&str, Option<&str>), String> {
    let parts = split_top_level(arguments, ',')?;
    let name = parts
        .first()
        .map(|value| value.trim())
        .filter(|value| is_custom_property_name(value))
        .ok_or_else(|| "var() 的第一个参数必须是自定义属性名".to_string())?;
    let fallback = match parts.len() {
        1 => None,
        _ => Some(arguments[arguments.find(',').expect("split result has comma") + 1..].trim()),
    };
    Ok((name, fallback))
}

fn is_custom_property_name(value: &str) -> bool {
    value.starts_with("--")
        && value.len() > 2
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || character == '-' || character == '_'
        })
}

fn font_shorthand_family(value: &str) -> Result<String, String> {
    let tokens = tokenize_top_level(value)?;
    let Some(size_index) = tokens.iter().position(|token| is_font_size_token(token)) else {
        return Err("font shorthand 缺少 font-size".to_string());
    };
    let mut family_tokens = tokens[size_index + 1..].to_vec();
    if family_tokens.first().is_some_and(|token| token == "/") {
        if family_tokens.len() < 2 {
            return Err("font shorthand 缺少 line-height".to_string());
        }
        family_tokens.drain(..2);
    }
    (!family_tokens.is_empty())
        .then(|| family_tokens.join(" "))
        .ok_or_else(|| "font shorthand 缺少 font-family".to_string())
}

fn is_font_size_token(token: &str) -> bool {
    let lower = token.to_ascii_lowercase();
    FONT_SIZE_KEYWORDS.contains(&lower.as_str())
        || lower.starts_with("calc(")
        || lower.starts_with("clamp(")
        || lower.starts_with("max(")
        || lower.starts_with("min(")
        || has_dimension_or_percentage_suffix(&lower)
}

fn has_dimension_or_percentage_suffix(token: &str) -> bool {
    let Some(first) = token.chars().next() else {
        return false;
    };
    if !first.is_ascii_digit() && first != '+' && first != '-' && first != '.' {
        return false;
    }
    token.ends_with('%')
        || [
            "px", "pt", "pc", "em", "rem", "ex", "ch", "vw", "vh", "vmin", "vmax",
        ]
        .iter()
        .any(|unit| token.ends_with(unit))
}

fn parse_font_family_list(value: &str) -> Result<Vec<String>, String> {
    let groups = split_top_level(value, ',')?;
    let mut candidates: Vec<String> = Vec::with_capacity(groups.len());
    for group in groups {
        let group = group.trim();
        if group.is_empty() {
            return Err("font-family 包含空列表项".to_string());
        }
        let candidate = parse_font_family(group)?;
        if !candidates
            .iter()
            .any(|existing| normalize_font_family(existing) == normalize_font_family(&candidate))
        {
            candidates.push(candidate);
        }
    }
    Ok(candidates)
}

fn parse_font_family(value: &str) -> Result<String, String> {
    let value = value.trim();
    let bytes = value.as_bytes();
    if let Some(quote) = quote_at(bytes, 0) {
        let end = string_end(bytes, 0, quote)?;
        if value[end + 1..].trim().is_empty() {
            return Ok(value[1..end].to_string());
        }
        return Err("带引号的 font-family 后存在额外 token".to_string());
    }
    let tokens = tokenize_top_level(value)?;
    if tokens.is_empty()
        || tokens.iter().any(|token| {
            token == "/"
                || token == ","
                || token.starts_with('"')
                || token.starts_with('\'')
                || token.ends_with(')')
        })
    {
        return Err("font-family 包含不支持的 token".to_string());
    }
    Ok(tokens.join(" "))
}

fn tokenize_top_level(value: &str) -> Result<Vec<String>, String> {
    let bytes = value.as_bytes();
    let mut tokens = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index].is_ascii_whitespace() {
            index += 1;
            continue;
        }
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            let end = string_end(bytes, index, quote)?;
            tokens.push(value[index..=end].to_string());
            index = end + 1;
            continue;
        }
        let start = index;
        let mut parens = 0_usize;
        while index < bytes.len() {
            if let Some(quote) = quote_at(bytes, index) {
                index = skip_string(bytes, index, quote)?;
                continue;
            }
            if starts_comment(bytes, index) {
                index = skip_comment(bytes, index)?;
                continue;
            }
            match bytes[index] {
                b'(' => parens += 1,
                b')' => parens = parens.checked_sub(1).ok_or("多余的右括号")?,
                b'/' if parens == 0 => break,
                byte if byte.is_ascii_whitespace() && parens == 0 => break,
                _ => {}
            }
            index += 1;
        }
        if start == index && bytes[index] == b'/' {
            tokens.push("/".to_string());
            index += 1;
            continue;
        }
        if parens != 0 {
            return Err("括号未闭合".to_string());
        }
        tokens.push(value[start..index].to_string());
    }
    Ok(tokens)
}

fn split_top_level<'a>(value: &'a str, separator: char) -> Result<Vec<&'a str>, String> {
    let bytes = value.as_bytes();
    let mut result = Vec::new();
    let mut start = 0;
    let mut index = 0;
    let mut parens = 0_usize;
    while index < bytes.len() {
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        match bytes[index] {
            b'(' => parens += 1,
            b')' => parens = parens.checked_sub(1).ok_or("多余的右括号")?,
            byte if byte == separator as u8 && parens == 0 => {
                result.push(&value[start..index]);
                start = index + 1;
            }
            _ => {}
        }
        index += 1;
    }
    (parens == 0)
        .then_some(())
        .ok_or_else(|| "括号未闭合".to_string())?;
    result.push(&value[start..]);
    Ok(result)
}

fn matching_paren(bytes: &[u8], open: usize) -> Result<usize, String> {
    if bytes.get(open) != Some(&b'(') {
        return Err("CSS 函数缺少左括号".to_string());
    }
    let mut depth = 0_usize;
    let mut index = open;
    while index < bytes.len() {
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        match bytes[index] {
            b'(' => depth += 1,
            b')' => {
                depth = depth.checked_sub(1).ok_or("多余的右括号")?;
                if depth == 0 {
                    return Ok(index);
                }
            }
            _ => {}
        }
        index += 1;
    }
    Err("CSS 函数括号未闭合".to_string())
}

fn starts_comment(bytes: &[u8], index: usize) -> bool {
    bytes.get(index..index + 2) == Some(b"/*")
}

fn skip_comment(bytes: &[u8], index: usize) -> Result<usize, String> {
    let Some(offset) = bytes[index + 2..].windows(2).position(|item| item == b"*/") else {
        return Err("CSS 注释未闭合".to_string());
    };
    Ok(index + 2 + offset + 2)
}

fn quote_at(bytes: &[u8], index: usize) -> Option<u8> {
    matches!(bytes.get(index), Some(b'\'') | Some(b'"')).then_some(bytes[index])
}

fn skip_string(bytes: &[u8], index: usize, quote: u8) -> Result<usize, String> {
    Ok(string_end(bytes, index, quote)? + 1)
}

fn string_end(bytes: &[u8], start: usize, quote: u8) -> Result<usize, String> {
    let mut index = start + 1;
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            index += 2;
            continue;
        }
        if bytes[index] == quote {
            return Ok(index);
        }
        index += 1;
    }
    Err("CSS 字符串未闭合".to_string())
}

fn is_ident_continue(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '-' || character == '_'
}

#[cfg(test)]
mod tests {
    use super::{
        is_generic_font_family, normalize_font_family, parse_font_value, resolve_css_variables,
        resolve_font_value, ParsedFontValue,
    };
    use std::collections::BTreeMap;

    #[test]
    fn parses_python_font_family_candidates_and_generic_families() {
        assert_eq!(
            parse_font_value("font-family", r#""Target Font", serif"#),
            Ok(ParsedFontValue::Candidates(vec![
                "Target Font".to_string(),
                "serif".to_string(),
            ]))
        );
        assert!(is_generic_font_family("Serif"));
        assert_eq!(normalize_font_family(" 'Target   Font' "), "target font");
    }

    #[test]
    fn parses_python_font_shorthand_golden_value() {
        assert_eq!(
            parse_font_value("font", "italic 700 1em/1.4 TargetFont, serif"),
            Ok(ParsedFontValue::Candidates(vec![
                "TargetFont".to_string(),
                "serif".to_string(),
            ]))
        );
    }

    #[test]
    fn preserves_css_wide_keywords_and_defers_custom_properties() {
        assert_eq!(
            parse_font_value("font-family", "inherit"),
            Ok(ParsedFontValue::Inherit)
        );
        assert_eq!(
            parse_font_value("font-family", "revert-layer"),
            Ok(ParsedFontValue::RevertLayer)
        );
        assert_eq!(
            parse_font_value("font-family", "var(--target-font), serif"),
            Ok(ParsedFontValue::NeedsCustomPropertyResolution)
        );
    }

    #[test]
    fn rejects_malformed_values_for_python_fallback() {
        assert!(parse_font_value("font-family", "\"TargetFont").is_err());
        assert!(parse_font_value("font", "italic TargetFont").is_err());
    }

    #[test]
    fn resolves_python_custom_property_font_vectors() {
        let properties = BTreeMap::from([
            ("--target-font".to_string(), "\"TargetFont\"".to_string()),
            (
                "--inherited-font".to_string(),
                "var(--target-font)".to_string(),
            ),
            ("--block-font".to_string(), "serif".to_string()),
        ]);

        assert_eq!(
            resolve_font_value("font-family", "var(--inherited-font), serif", &properties),
            Ok(ParsedFontValue::Candidates(vec![
                "TargetFont".to_string(),
                "serif".to_string(),
            ]))
        );
        assert_eq!(
            resolve_font_value(
                "font-family",
                "var(--missing-font, \"TargetFont\", serif)",
                &properties
            ),
            Ok(ParsedFontValue::Candidates(vec![
                "TargetFont".to_string(),
                "serif".to_string(),
            ]))
        );
        assert_eq!(
            resolve_css_variables("var(--block-font)", &properties),
            Ok("serif".to_string())
        );
    }

    #[test]
    fn rejects_missing_and_cyclic_custom_properties() {
        let properties = BTreeMap::from([
            ("--one".to_string(), "var(--two)".to_string()),
            ("--two".to_string(), "var(--one)".to_string()),
        ]);
        assert!(resolve_css_variables("var(--missing)", &properties).is_err());
        assert!(resolve_css_variables("var(--one)", &properties).is_err());
    }
}
