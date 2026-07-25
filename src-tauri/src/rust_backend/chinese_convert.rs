use super::{epub::EpubWorkspace, EpubTask, TaskOutcome};
use encoding_rs::Encoding;
use ferrous_opencc::{config::BuiltinConfig, OpenCC};
use regex::{Captures, Regex};
use serde_json::Value;
use std::path::Path;

pub struct ChineseConvertTask;

impl EpubTask for ChineseConvertTask {
    fn task_type(&self) -> &'static str {
        "chinese_convert"
    }

    fn supports_options(&self, options: &Value) -> bool {
        // The production Python OpenCC dictionaries differ on real books
        // (for example 游/遊 and 才/纔). Keep sidecar behavior authoritative
        // until a Rust dictionary with approved parity is selected.
        let _ = options;
        false
    }

    fn output_suffix(&self, options: &Value) -> Result<String, String> {
        match options.get("direction").and_then(Value::as_str) {
            Some("s2t") => Ok("_chinese_convert_tc.epub".to_string()),
            Some("t2s") => Ok("_chinese_convert_sc.epub".to_string()),
            _ => Err("direction 必须是 s2t 或 t2s".to_string()),
        }
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut EpubWorkspace,
        options: &Value,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        let direction = options
            .get("direction")
            .and_then(Value::as_str)
            .ok_or_else(|| "direction 必须是 s2t 或 t2s".to_string())?;
        let converter = OpenCC::from_config(match direction {
            "s2t" => BuiltinConfig::S2t,
            "t2s" => BuiltinConfig::T2s,
            _ => return Err("direction 必须是 s2t 或 t2s".to_string()),
        })
        .map_err(|error| format!("初始化 OpenCC 词典失败: {error}"))?;
        let member_names: Vec<String> = workspace.members.keys().cloned().collect();
        let mut changed_files = 0;
        for name in member_names {
            if !matches!(
                extension_of(&name).as_str(),
                "xhtml" | "html" | "htm" | "opf" | "ncx"
            ) {
                continue;
            }
            let data = workspace
                .members
                .get(&name)
                .ok_or_else(|| format!("EPUB 文本成员丢失: {name}"))?;
            let converted = convert_xml(data, &converter)?;
            if converted != *data {
                workspace.members.insert(name, converted);
                changed_files += 1;
            }
        }
        log(format!("简繁转换完成：更新 {changed_files} 个文本文件"));
        Ok(TaskOutcome::Success)
    }
}

fn convert_xml(data: &[u8], converter: &OpenCC) -> Result<Vec<u8>, String> {
    let text = decode_xml(data)?;
    let token =
        Regex::new(r"(?s)<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[^>]+>").expect("valid XML token regex");
    let visible_attribute = Regex::new(r#"(?i)(\b(?:alt|title)\s*=\s*[\"'])([^\"']*)([\"'])"#)
        .expect("valid visible attribute regex");
    let mut output = String::with_capacity(text.len());
    let mut cursor = 0;
    let mut blocked_depth = 0_u32;
    for matched in token.find_iter(&text) {
        let body = &text[cursor..matched.start()];
        if blocked_depth == 0 {
            output.push_str(&converter.convert(body));
        } else {
            output.push_str(body);
        }
        let mut tag = matched.as_str().to_string();
        let lowered = tag.trim_start().to_ascii_lowercase();
        if lowered.starts_with("<script") || lowered.starts_with("<style") {
            blocked_depth += 1;
        }
        if blocked_depth == 0 && !lowered.starts_with("<!--") && !lowered.starts_with("<![cdata[") {
            tag = visible_attribute
                .replace_all(&tag, |captures: &Captures<'_>| {
                    format!(
                        "{}{}{}",
                        &captures[1],
                        converter.convert(&captures[2]),
                        &captures[3]
                    )
                })
                .into_owned();
        }
        output.push_str(&tag);
        if lowered.starts_with("</script") || lowered.starts_with("</style") {
            blocked_depth = blocked_depth.saturating_sub(1);
        }
        cursor = matched.end();
    }
    let remaining = &text[cursor..];
    if blocked_depth == 0 {
        output.push_str(&converter.convert(remaining));
    } else {
        output.push_str(remaining);
    }
    Ok(as_utf8_xml(&output).into_bytes())
}

fn decode_xml(data: &[u8]) -> Result<String, String> {
    if let Some(text) = decode_with_bom(data)? {
        return Ok(text);
    }
    let declaration = String::from_utf8_lossy(&data[..data.len().min(1024)]);
    let encoding_pattern = Regex::new(r#"(?i)<\?xml\b[^>]*\bencoding\s*=\s*[\"']([^\"']+)[\"']"#)
        .expect("valid XML encoding regex");
    let encoding = encoding_pattern
        .captures(&declaration)
        .map(|captures| captures[1].to_ascii_lowercase())
        .unwrap_or_else(|| "utf-8".to_string());
    if encoding.starts_with("utf-32") {
        return decode_utf32(data, encoding.ends_with("le"));
    }
    let encoding = Encoding::for_label(encoding.as_bytes())
        .ok_or_else(|| format!("XML 声明了不支持的编码: {encoding}"))?;
    let (text, _, had_errors) = encoding.decode(data);
    if had_errors {
        return Err(format!("无法按 {:?} 解码 XML", encoding.name()));
    }
    Ok(text.into_owned())
}

fn decode_with_bom(data: &[u8]) -> Result<Option<String>, String> {
    if let Some(rest) = data.strip_prefix(b"\xEF\xBB\xBF") {
        return String::from_utf8(rest.to_vec())
            .map(Some)
            .map_err(|error| format!("无法按 UTF-8 解码 XML: {error}"));
    }
    if data.starts_with(b"\x00\x00\xFE\xFF") {
        return decode_utf32(&data[4..], false).map(Some);
    }
    if data.starts_with(b"\xFF\xFE\x00\x00") {
        return decode_utf32(&data[4..], true).map(Some);
    }
    if data.starts_with(b"\xFE\xFF") {
        return decode_utf16(&data[2..], false).map(Some);
    }
    if data.starts_with(b"\xFF\xFE") {
        return decode_utf16(&data[2..], true).map(Some);
    }
    Ok(None)
}

fn decode_utf16(data: &[u8], little_endian: bool) -> Result<String, String> {
    if data.len() % 2 != 0 {
        return Err("UTF-16 XML 字节长度无效".to_string());
    }
    let values = data.chunks_exact(2).map(|chunk| {
        if little_endian {
            u16::from_le_bytes([chunk[0], chunk[1]])
        } else {
            u16::from_be_bytes([chunk[0], chunk[1]])
        }
    });
    char::decode_utf16(values)
        .collect::<Result<String, _>>()
        .map_err(|error| format!("无法按 UTF-16 解码 XML: {error}"))
}

fn decode_utf32(data: &[u8], little_endian: bool) -> Result<String, String> {
    if data.len() % 4 != 0 {
        return Err("UTF-32 XML 字节长度无效".to_string());
    }
    data.chunks_exact(4)
        .map(|chunk| {
            let value = if little_endian {
                u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            } else {
                u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]])
            };
            char::from_u32(value).ok_or_else(|| format!("UTF-32 包含无效码点: {value:#X}"))
        })
        .collect()
}

fn as_utf8_xml(text: &str) -> String {
    let encoding_pattern =
        Regex::new(r#"(?is)(<\?xml\b[^>]*\bencoding\s*=\s*[\"'])[^\"']*([\"'])"#)
            .expect("valid XML encoding regex");
    encoding_pattern.replace(text, "${1}UTF-8${2}").into_owned()
}

fn extension_of(path: &str) -> String {
    path.rsplit_once('.')
        .map_or_else(String::new, |(_, extension)| extension.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::{convert_xml, ChineseConvertTask};
    use crate::rust_backend::EpubTask;
    use ferrous_opencc::{config::BuiltinConfig, OpenCC};
    use serde_json::json;

    #[test]
    fn converts_visible_text_and_attributes_without_touching_script_or_css() {
        let converter = OpenCC::from_config(BuiltinConfig::S2t).unwrap();
        let converted = convert_xml(
            r#"<?xml version="1.0" encoding="UTF-8"?><html><head><style>.简体{}</style></head><body id="简体"><p title="汉语">汉语发展</p><script>const text = '汉语';</script></body></html>"#.as_bytes(),
            &converter,
        )
        .unwrap();
        let converted = String::from_utf8(converted).unwrap();
        assert!(converted.contains("title=\"漢語\""));
        assert!(converted.contains("漢語發展"));
        assert!(converted.contains("id=\"简体\""));
        assert!(converted.contains(".简体"));
        assert!(converted.contains("const text = '汉语'"));
        assert!(!ChineseConvertTask.supports_options(&json!({"direction": "s2t"})));
    }

    #[test]
    fn builtin_dictionary_is_not_python_opencc_s2t_compatible() {
        // `opencc-python-reimplemented` is the current production reference.
        // Its s2t output for this phrase set is
        // `遊移不定 却才華洋溢 反取憀慄 其中很多只能 看成一出面對`;
        // ferrous-opencc's bundled
        // dictionary produces the value below. Keep this explicit regression
        // gate so an accidental `supports_options = true` cannot silently
        // change book text before an approved dictionary is bundled.
        let converter = OpenCC::from_config(BuiltinConfig::S2t).unwrap();
        assert_eq!(
            converter.convert("游移不定 却才华洋溢 反取憀栗 其中很多只能 看成一出面对"),
            "游移不定 卻纔華洋溢 反取憀栗 其中很多隻能 看成一齣面對"
        );
        assert!(!ChineseConvertTask.supports_options(&json!({"direction": "s2t"})));
    }

    #[test]
    fn builtin_dictionary_is_not_python_opencc_t2s_compatible() {
        // Python's production t2s dictionary converts `射覆` to `射复`,
        // while the bundled Rust dictionary preserves `覆`. This was found by
        // exercising every source key from Python's TS dictionaries.
        let converter = OpenCC::from_config(BuiltinConfig::T2s).unwrap();
        assert_eq!(converter.convert("射覆"), "射覆");
        assert!(!ChineseConvertTask.supports_options(&json!({"direction": "t2s"})));
    }
}
