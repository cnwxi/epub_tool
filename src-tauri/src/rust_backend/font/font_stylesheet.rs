//! Conservative CSS stylesheet extraction for native font processing.
//!
//! The font services must know every applicable declaration.  This parser
//! accepts top-level rules and `@media` blocks with the same EPUB media target
//! (`screen`/`all`) as the reference implementation. Unsupported at-rules are
//! errors instead of being guessed.

use super::font_values::{parse_font_value, ParsedFontValue};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FontFaceRule {
    pub family: String,
    pub sources: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CssDeclaration {
    pub name: String,
    pub value: String,
    pub important: bool,
    pub declaration_order: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CssFontRule {
    pub selector: String,
    pub declarations: Vec<CssDeclaration>,
    pub source_order: usize,
}

#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct FontStylesheet {
    pub font_faces: Vec<FontFaceRule>,
    pub rules: Vec<CssFontRule>,
}

/// Parses `@font-face` and qualified font-related CSS rules.
///
/// `@media` blocks are evaluated for an EPUB reader (`screen`/`all`), while
/// `@supports`, `@layer`, `@scope`, imports and escaped CSS are rejected until
/// their Python-equivalent handling is available in Rust.
pub fn parse_font_stylesheet(css: &str) -> Result<FontStylesheet, String> {
    if css.contains('\\') {
        return Err("CSS 包含转义，当前 Rust 解析器暂不支持".to_string());
    }
    let mut stylesheet = FontStylesheet::default();
    let mut source_order = 0;
    parse_font_rules(css, &mut stylesheet, &mut source_order)?;
    Ok(stylesheet)
}

fn parse_font_rules(
    css: &str,
    stylesheet: &mut FontStylesheet,
    source_order: &mut usize,
) -> Result<(), String> {
    let bytes = css.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        index = skip_ignored(bytes, index)?;
        if index >= bytes.len() {
            break;
        }
        if bytes[index] == b'@' {
            let keyword_start = index + 1;
            let keyword_end = scan_ident(bytes, keyword_start);
            if keyword_end == keyword_start {
                return Err("无效 CSS at-rule".to_string());
            }
            let keyword = css[keyword_start..keyword_end].to_ascii_lowercase();
            index = skip_ignored(bytes, keyword_end)?;
            if keyword == "charset" {
                let end = css[index..]
                    .find(';')
                    .map(|offset| index + offset)
                    .ok_or_else(|| "@charset 缺少分号".to_string())?;
                let value = css[index..end].trim();
                if value.len() < 2
                    || !matches!(value.as_bytes().first(), Some(b'\'' | b'"'))
                    || value.as_bytes().first() != value.as_bytes().last()
                {
                    return Err("@charset 编码声明无效".to_string());
                }
                index = end + 1;
                continue;
            }
            if keyword == "media" {
                let (prelude_end, block_start) = find_rule_block_start(bytes, index)?;
                let block_end = matching_brace(bytes, block_start)?;
                if media_query_list_applies_to_epub(&css[index..prelude_end])? {
                    parse_font_rules(&css[block_start + 1..block_end], stylesheet, source_order)?;
                }
                index = block_end + 1;
                continue;
            }
            if keyword != "font-face" {
                return Err(format!("暂不支持 CSS @{keyword}，当前 Rust 解析器暂不支持"));
            }
            if bytes.get(index) != Some(&b'{') {
                return Err("@font-face 缺少声明块".to_string());
            }
            let end = matching_brace(bytes, index)?;
            stylesheet
                .font_faces
                .push(parse_font_face(&css[index + 1..end])?);
            index = end + 1;
            continue;
        }

        let (prelude_end, block_start) = find_rule_block_start(bytes, index)?;
        let selector = css[index..prelude_end].trim();
        if selector.is_empty() {
            return Err("CSS 规则缺少选择器".to_string());
        }
        let block_end = matching_brace(bytes, block_start)?;
        let declarations = parse_font_declarations(&css[block_start + 1..block_end])?;
        if !declarations.is_empty() {
            *source_order += 1;
            for selector in split_top_level(selector, ',')? {
                let selector = selector.trim();
                if selector.is_empty() {
                    return Err("CSS 选择器列表包含空项".to_string());
                }
                stylesheet.rules.push(CssFontRule {
                    selector: selector.to_string(),
                    declarations: declarations.clone(),
                    source_order: *source_order,
                });
            }
        }
        index = block_end + 1;
    }
    Ok(())
}

fn media_query_list_applies_to_epub(value: &str) -> Result<bool, String> {
    let queries = split_top_level(value, ',')?;
    Ok(queries.into_iter().any(media_query_applies_to_epub))
}

fn media_query_applies_to_epub(value: &str) -> bool {
    let mut query = strip_comments(value).unwrap_or_else(|_| value.to_string());
    query = query.trim().to_ascii_lowercase();
    if query.is_empty() {
        return true;
    }
    if let Some(remainder) = query.strip_prefix("only ") {
        query = remainder.trim().to_string();
    }
    let negated = query.strip_prefix("not ").is_some();
    if let Some(remainder) = query.strip_prefix("not ") {
        query = remainder.trim().to_string();
        if query.starts_with('(') {
            return true;
        }
    }
    let media_type = query
        .split(|character: char| !(character.is_ascii_alphanumeric() || character == '-'))
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or("all");
    let applies = matches!(media_type, "all" | "screen");
    if negated {
        !applies
    } else {
        applies
    }
}

/// Parses font-relevant declarations from an XHTML `style` attribute.
pub fn parse_inline_font_declarations(style: &str) -> Result<Vec<CssDeclaration>, String> {
    parse_font_declarations(style)
}

fn parse_font_face(block: &str) -> Result<FontFaceRule, String> {
    let declarations = parse_declarations(block)?;
    let mut family = None;
    let mut sources = Vec::new();
    for declaration in declarations {
        match declaration.name.as_str() {
            "font-family" => match parse_font_value("font-family", &declaration.value)? {
                ParsedFontValue::Candidates(candidates) => family = candidates.into_iter().next(),
                _ => return Err("@font-face 的 font-family 不可静态解析".to_string()),
            },
            "src" => sources.extend(parse_url_sources(&declaration.value)?),
            _ => {}
        }
    }
    let family = family.ok_or_else(|| "@font-face 缺少 font-family".to_string())?;
    if sources.is_empty() {
        return Err("@font-face 缺少本地 font src".to_string());
    }
    Ok(FontFaceRule { family, sources })
}

fn parse_font_declarations(block: &str) -> Result<Vec<CssDeclaration>, String> {
    Ok(parse_declarations(block)?
        .into_iter()
        .filter(|declaration| {
            declaration.name.starts_with("--")
                || matches!(declaration.name.as_str(), "font-family" | "font" | "all")
        })
        .collect())
}

fn parse_declarations(block: &str) -> Result<Vec<CssDeclaration>, String> {
    let mut declarations = Vec::new();
    for item in split_top_level(block, ';')? {
        let item = strip_comments(item)?;
        let item = item.trim();
        if item.is_empty() {
            continue;
        }
        let colon =
            find_top_level(item, ':')?.ok_or_else(|| format!("CSS 声明缺少冒号: {item}"))?;
        let name = item[..colon].trim().to_ascii_lowercase();
        if name.is_empty() {
            return Err("CSS 声明属性名为空".to_string());
        }
        let (value, important) = strip_important(item[colon + 1..].trim())?;
        if value.is_empty() {
            return Err(format!("CSS 声明值为空: {name}"));
        }
        declarations.push(CssDeclaration {
            name,
            value: value.to_string(),
            important,
            declaration_order: declarations.len() + 1,
        });
    }
    Ok(declarations)
}

fn strip_comments(value: &str) -> Result<String, String> {
    let bytes = value.as_bytes();
    let mut result = String::with_capacity(value.len());
    let mut index = 0;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            let end = skip_string(bytes, index, quote)?;
            result.push_str(&value[index..end]);
            index = end;
            continue;
        }
        let character = value[index..]
            .chars()
            .next()
            .ok_or_else(|| "CSS 字符串索引无效".to_string())?;
        result.push(character);
        index += character.len_utf8();
    }
    Ok(result)
}

fn strip_important(value: &str) -> Result<(&str, bool), String> {
    let lower = value.to_ascii_lowercase();
    if let Some(position) = lower.rfind("!important") {
        if lower[position + "!important".len()..].trim().is_empty() {
            return Ok((value[..position].trim(), true));
        }
    }
    if lower.contains("!important") {
        return Err("!important 后存在额外 CSS token".to_string());
    }
    Ok((value, false))
}

fn parse_url_sources(value: &str) -> Result<Vec<String>, String> {
    let bytes = value.as_bytes();
    let mut sources = Vec::new();
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
            && bytes[index..index + 4].eq_ignore_ascii_case(b"url(")
            && (index == 0 || !is_ident_continue(bytes[index - 1] as char))
        {
            let end = matching_paren(bytes, index + 3)?;
            let source = value[index + 4..end].trim().trim_matches(['\'', '"']);
            if !source.is_empty() {
                sources.push(source.to_string());
            }
            index = end + 1;
            continue;
        }
        index += 1;
    }
    Ok(sources)
}

fn find_rule_block_start(bytes: &[u8], start: usize) -> Result<(usize, usize), String> {
    let mut index = start;
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
            b'{' if parens == 0 => return Ok((index, index)),
            b'}' if parens == 0 => return Err("CSS 规则出现多余右花括号".to_string()),
            _ => {}
        }
        index += 1;
    }
    Err("CSS 规则缺少声明块".to_string())
}

fn matching_brace(bytes: &[u8], open: usize) -> Result<usize, String> {
    if bytes.get(open) != Some(&b'{') {
        return Err("CSS 规则缺少左花括号".to_string());
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
            b'{' => depth += 1,
            b'}' => {
                depth = depth.checked_sub(1).ok_or("多余的右花括号")?;
                if depth == 0 {
                    return Ok(index);
                }
            }
            _ => {}
        }
        index += 1;
    }
    Err("CSS 花括号未闭合".to_string())
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
    if parens != 0 {
        return Err("CSS 括号未闭合".to_string());
    }
    result.push(&value[start..]);
    Ok(result)
}

fn find_top_level(value: &str, target: char) -> Result<Option<usize>, String> {
    Ok(split_top_level(value, target)?
        .first()
        .and_then(|first| (first.len() < value.len()).then_some(first.len())))
}

fn skip_ignored(bytes: &[u8], mut index: usize) -> Result<usize, String> {
    while index < bytes.len() {
        if bytes[index].is_ascii_whitespace() {
            index += 1;
        } else if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
        } else {
            break;
        }
    }
    Ok(index)
}

fn scan_ident(bytes: &[u8], mut index: usize) -> usize {
    while index < bytes.len() && is_ident_continue(bytes[index] as char) {
        index += 1;
    }
    index
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
    let mut index = index + 1;
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            index += 2;
        } else if bytes[index] == quote {
            return Ok(index + 1);
        } else {
            index += 1;
        }
    }
    Err("CSS 字符串未闭合".to_string())
}

fn is_ident_continue(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '-' || character == '_'
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

#[cfg(test)]
mod tests {
    use super::{parse_font_stylesheet, CssDeclaration, CssFontRule, FontFaceRule, FontStylesheet};

    #[test]
    fn extracts_the_python_reference_font_stylesheet() {
        let css = r#"@font-face {
  font-family: "Obf";
  src: url("../Fonts/obf.ttf");
}
.obf { font-family: "Obf", serif; }
"#;
        assert_eq!(
            parse_font_stylesheet(css),
            Ok(FontStylesheet {
                font_faces: vec![FontFaceRule {
                    family: "Obf".to_string(),
                    sources: vec!["../Fonts/obf.ttf".to_string()],
                }],
                rules: vec![CssFontRule {
                    selector: ".obf".to_string(),
                    declarations: vec![CssDeclaration {
                        name: "font-family".to_string(),
                        value: "\"Obf\", serif".to_string(),
                        important: false,
                        declaration_order: 1,
                    }],
                    source_order: 1,
                }],
            })
        );
    }

    #[test]
    fn preserves_custom_properties_and_declaration_importance() {
        let css =
            ".target { --font: TargetFont; font-family: var(--font) !important; color: red; }";
        let parsed = parse_font_stylesheet(css).expect("CSS should parse");
        assert_eq!(parsed.rules[0].declarations.len(), 2);
        assert_eq!(parsed.rules[0].declarations[0].name, "--font");
        assert!(parsed.rules[0].declarations[1].important);
    }

    #[test]
    fn accepts_a_top_level_charset_before_font_rules() {
        let parsed = parse_font_stylesheet(
            "@charset \"utf-8\"; @font-face { font-family: Obf; src: url(obf.ttf); }",
        )
        .expect("@charset should not affect font parsing");
        assert_eq!(parsed.font_faces[0].family, "Obf");
    }

    #[test]
    fn ignores_comment_only_declaration_fragments() {
        let parsed = parse_font_stylesheet(
            ".target { /* EPUB generator leaves empty comment declarations */ font-family: Obf; }",
        )
        .expect("comment-only declaration should be ignored");
        assert_eq!(parsed.rules[0].selector, ".target");
    }

    #[test]
    fn keeps_only_media_rules_that_apply_to_epub_readers() {
        let parsed = parse_font_stylesheet(
            "@media print { .print { font-family: Print; } } @media screen { .target { font-family: TargetFont; } }",
        )
        .expect("screen media rules should parse");
        assert_eq!(parsed.rules.len(), 1);
        assert_eq!(parsed.rules[0].selector, ".target");
        assert!(parse_font_stylesheet("@import url(\"font.css\");").is_err());
    }
}
