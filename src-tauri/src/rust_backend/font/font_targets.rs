use crate::rust_backend::epub::EpubWorkspace;
use std::{collections::BTreeSet, path::Path};

const FONT_EXTENSIONS: [&str; 4] = [".ttf", ".otf", ".woff", ".woff2"];

/// Lists packaged font families declared by CSS `@font-face` rules.
///
/// This deliberately accepts only a conservative subset of CSS and returns an
/// explicit error rather than presenting an incomplete list of font families.
pub fn list_font_targets(input: &Path) -> Result<Vec<String>, String> {
    let workspace = EpubWorkspace::load(input, |_| {})?;
    let font_files = workspace
        .members
        .keys()
        .filter_map(|path| font_basename(path))
        .collect::<BTreeSet<_>>();
    if font_files.is_empty() {
        return Ok(Vec::new());
    }

    let mut families = BTreeSet::new();
    for (path, content) in &workspace.members {
        if !path.to_ascii_lowercase().ends_with(".css") {
            continue;
        }
        let css = std::str::from_utf8(content)
            .map_err(|_| format!("CSS 不是 UTF-8，当前 Rust 扫描器暂不支持: {path}"))?;
        scan_css(css, &font_files, &mut families)
            .map_err(|error| format!("当前 Rust 扫描器不支持该 CSS ({path}): {error}"))?;
    }

    Ok(families.into_iter().collect())
}

fn font_basename(path: &str) -> Option<String> {
    let lower = path.to_ascii_lowercase();
    FONT_EXTENSIONS
        .iter()
        .any(|extension| lower.ends_with(extension))
        .then(|| path.rsplit('/').next().unwrap_or(path).to_ascii_lowercase())
}

fn scan_css(
    css: &str,
    font_files: &BTreeSet<String>,
    families: &mut BTreeSet<String>,
) -> Result<(), &'static str> {
    if css.contains('\\') {
        return Err("包含 CSS 转义");
    }
    validate_css_structure(css)?;
    let bytes = css.as_bytes();
    let mut index = 0;
    let mut brace_depth = 0_usize;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if brace_depth == 0 && is_font_face_at_rule(bytes, index) {
            let mut block_start = index + "@font-face".len();
            block_start = skip_whitespace_and_comments(bytes, block_start)?;
            if bytes.get(block_start) != Some(&b'{') {
                return Err("@font-face 缺少声明块");
            }
            let block_end = matching_brace(bytes, block_start)?;
            scan_font_face(&css[block_start + 1..block_end], font_files, families)?;
            index = block_end + 1;
            continue;
        }
        match bytes[index] {
            b'{' => brace_depth += 1,
            b'}' => brace_depth -= 1,
            _ => {}
        }
        index += 1;
    }
    Ok(())
}

fn scan_font_face(
    block: &str,
    font_files: &BTreeSet<String>,
    families: &mut BTreeSet<String>,
) -> Result<(), &'static str> {
    let mut family = None;
    let mut sources = None;
    for declaration in split_top_level(block, b';')? {
        let Some(colon) = find_top_level(declaration, b':')? else {
            continue;
        };
        let name = declaration[..colon].trim().to_ascii_lowercase();
        let value = declaration[colon + 1..].trim();
        match name.as_str() {
            "font-family" => family = parse_font_family(value)?,
            "src" => sources = Some(value),
            _ => {}
        }
    }
    let (Some(family), Some(sources)) = (family, sources) else {
        return Ok(());
    };
    if url_targets_packaged_font(sources, font_files)? {
        families.insert(family);
    }
    Ok(())
}

fn parse_font_family(value: &str) -> Result<Option<String>, &'static str> {
    let value = value.trim();
    let value = value
        .strip_suffix("!important")
        .or_else(|| value.strip_suffix("!IMPORTANT"))
        .unwrap_or(value)
        .trim();
    let bytes = value.as_bytes();
    let mut values = Vec::new();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index].is_ascii_whitespace() || bytes[index] == b',' {
            index += 1;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            let end = string_end(bytes, index, quote)?;
            values.push(value[index + 1..end].to_string());
            index = end + 1;
            continue;
        }
        if !is_ident_start(bytes[index]) {
            return Err("font-family 包含不支持的 token");
        }
        let end = value[index..]
            .char_indices()
            .find_map(|(offset, character)| {
                (!is_ident_continue(character)).then_some(index + offset)
            })
            .unwrap_or(value.len());
        values.push(value[index..end].to_string());
        index = end;
    }
    Ok((!values.is_empty()).then(|| values.join(" ")))
}

fn url_targets_packaged_font(
    value: &str,
    font_files: &BTreeSet<String>,
) -> Result<bool, &'static str> {
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if index + 3 <= bytes.len()
            && bytes[index..index + 3].eq_ignore_ascii_case(b"url")
            && !bytes
                .get(index.wrapping_sub(1))
                .is_some_and(|byte| is_ident_continue(*byte as char))
        {
            let open = skip_whitespace_and_comments(bytes, index + 3)?;
            if bytes.get(open) != Some(&b'(') {
                index += 3;
                continue;
            }
            let close = matching_paren(bytes, open)?;
            let target = value[open + 1..close]
                .trim()
                .trim_matches(['\'', '"'])
                .split('#')
                .next()
                .unwrap_or("")
                .split('?')
                .next()
                .unwrap_or("");
            let basename = target
                .rsplit('/')
                .next()
                .unwrap_or(target)
                .to_ascii_lowercase();
            if font_files.contains(&basename) {
                return Ok(true);
            }
            index = close + 1;
            continue;
        }
        index += 1;
    }
    Ok(false)
}

fn validate_css_structure(bytes: &str) -> Result<(), &'static str> {
    let bytes = bytes.as_bytes();
    let mut braces = 0_usize;
    let mut index = 0;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
        } else if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
        } else {
            match bytes[index] {
                b'{' => braces += 1,
                b'}' => {
                    braces = braces.checked_sub(1).ok_or("多余的右花括号")?;
                }
                _ => {}
            }
            index += 1;
        }
    }
    (braces == 0).then_some(()).ok_or("花括号未闭合")
}

fn split_top_level(value: &str, separator: u8) -> Result<Vec<&str>, &'static str> {
    let mut result = Vec::new();
    let bytes = value.as_bytes();
    let mut start = 0;
    let mut parens = 0_usize;
    let mut index = 0;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        match bytes[index] {
            b'(' => parens += 1,
            b')' => parens = parens.checked_sub(1).ok_or("多余的右括号")?,
            byte if byte == separator && parens == 0 => {
                result.push(&value[start..index]);
                start = index + 1;
            }
            _ => {}
        }
        index += 1;
    }
    (parens == 0).then_some(()).ok_or("括号未闭合")?;
    result.push(&value[start..]);
    Ok(result)
}

fn find_top_level(value: &str, target: u8) -> Result<Option<usize>, &'static str> {
    Ok(split_top_level(value, target)?
        .first()
        .and_then(|part| (part.len() < value.len()).then_some(part.len())))
}

fn is_font_face_at_rule(bytes: &[u8], index: usize) -> bool {
    let word = b"@font-face";
    index + word.len() <= bytes.len()
        && bytes[index..index + word.len()].eq_ignore_ascii_case(word)
        && !bytes
            .get(index.wrapping_sub(1))
            .is_some_and(|byte| is_ident_continue(*byte as char))
        && !bytes
            .get(index + word.len())
            .is_some_and(|byte| is_ident_continue(*byte as char))
}

fn matching_brace(bytes: &[u8], open: usize) -> Result<usize, &'static str> {
    matching_delimiter(bytes, open, b'{', b'}')
}

fn matching_paren(bytes: &[u8], open: usize) -> Result<usize, &'static str> {
    matching_delimiter(bytes, open, b'(', b')')
}

fn matching_delimiter(
    bytes: &[u8],
    open: usize,
    left: u8,
    right: u8,
) -> Result<usize, &'static str> {
    let mut depth = 0_usize;
    let mut index = open;
    while index < bytes.len() {
        if starts_comment(bytes, index) {
            index = skip_comment(bytes, index)?;
            continue;
        }
        if let Some(quote) = quote_at(bytes, index) {
            index = skip_string(bytes, index, quote)?;
            continue;
        }
        if bytes[index] == left {
            depth += 1;
        } else if bytes[index] == right {
            depth -= 1;
            if depth == 0 {
                return Ok(index);
            }
        }
        index += 1;
    }
    Err("CSS 分隔符未闭合")
}

fn skip_whitespace_and_comments(bytes: &[u8], mut index: usize) -> Result<usize, &'static str> {
    loop {
        while bytes.get(index).is_some_and(u8::is_ascii_whitespace) {
            index += 1;
        }
        if !starts_comment(bytes, index) {
            return Ok(index);
        }
        index = skip_comment(bytes, index)?;
    }
}

fn starts_comment(bytes: &[u8], index: usize) -> bool {
    bytes.get(index..index + 2) == Some(b"/*")
}

fn skip_comment(bytes: &[u8], index: usize) -> Result<usize, &'static str> {
    bytes[index + 2..]
        .windows(2)
        .position(|window| window == b"*/")
        .map(|offset| index + 4 + offset)
        .ok_or("CSS 注释未闭合")
}

fn quote_at(bytes: &[u8], index: usize) -> Option<u8> {
    bytes
        .get(index)
        .copied()
        .filter(|value| matches!(value, b'\'' | b'"'))
}

fn skip_string(bytes: &[u8], index: usize, quote: u8) -> Result<usize, &'static str> {
    string_end(bytes, index, quote).map(|end| end + 1)
}

fn string_end(bytes: &[u8], index: usize, quote: u8) -> Result<usize, &'static str> {
    bytes[index + 1..]
        .iter()
        .position(|value| *value == quote)
        .map(|offset| index + 1 + offset)
        .ok_or("CSS 字符串未闭合")
}

fn is_ident_start(value: u8) -> bool {
    value.is_ascii_alphabetic() || value == b'_' || value == b'-' || value >= 0x80
}

fn is_ident_continue(value: char) -> bool {
    value.is_alphanumeric() || matches!(value, '_' | '-') || !value.is_ascii()
}

#[cfg(test)]
mod tests {
    use super::{scan_css, BTreeSet};

    #[test]
    fn matches_python_by_scanning_only_top_level_font_face_rules() {
        let fonts = BTreeSet::from(["target.ttf".to_string(), "other.woff2".to_string()]);
        let mut families = BTreeSet::new();
        scan_css(
            r#"@media screen { @font-face { font-family: 'Target Font'; src: local('X'), url('../Fonts/target.ttf?#v'); } }
            @font-face { font-family: Other; src: url(../Fonts/other.woff2); }"#,
            &fonts,
            &mut families,
        )
        .unwrap();
        assert_eq!(families.into_iter().collect::<Vec<_>>(), ["Other"]);
    }

    #[test]
    fn rejects_escaped_or_unclosed_css_for_python_fallback() {
        let fonts = BTreeSet::from(["target.ttf".to_string()]);
        let mut families = BTreeSet::new();
        assert!(scan_css(
            r"@font-face { font-family: T\\arget; }",
            &fonts,
            &mut families
        )
        .is_err());
        assert!(scan_css("@font-face { font-family: T;", &fonts, &mut families).is_err());
    }
}
