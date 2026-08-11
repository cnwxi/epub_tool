use crate::rust_backend::{epub::EpubWorkspace, EpubTask, TaskOutcome};
use crate::task_types::{TaskOptions, TaskType};
use encoding_rs::Encoding;
use regex::{Captures, Regex};
use std::{
    collections::HashMap,
    fs,
    path::{Path, PathBuf},
    sync::{LazyLock, OnceLock},
};

pub struct ChineseConvertTask;

static OPENCC_RESOURCE_DIR: OnceLock<PathBuf> = OnceLock::new();

pub fn configure_resource_dir(directory: PathBuf) -> Result<(), String> {
    if !directory.join("s2t.json").is_file() || !directory.join("t2s.json").is_file() {
        return Err(format!("OpenCC 资源不完整: {}", directory.display()));
    }
    match OPENCC_RESOURCE_DIR.set(directory.clone()) {
        Ok(()) => Ok(()),
        Err(existing) if existing == directory => Ok(()),
        Err(existing) => Err(format!(
            "OpenCC 已使用不同资源初始化: {}",
            existing.display()
        )),
    }
}

fn resource_dir() -> Option<PathBuf> {
    OPENCC_RESOURCE_DIR.get().cloned().or_else(|| {
        let directory = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("bundle-resources")
            .join("opencc");
        directory.is_dir().then_some(directory)
    })
}

struct OpenccDictionary {
    entries: HashMap<String, String>,
    min_key_length: usize,
    max_key_length: usize,
}

struct OpenccConverter {
    dictionaries: Vec<OpenccDictionary>,
    separators: Regex,
}

fn converter(direction: &str) -> Result<OpenccConverter, String> {
    let directory = resource_dir().ok_or_else(|| "未找到 Rust OpenCC 词典资源".to_string())?;
    let dictionary_names = match direction {
        "s2t" => ["STPhrases.txt", "STCharacters.txt"],
        "t2s" => ["TSPhrases.txt", "TSCharacters.txt"],
        _ => return Err("direction 必须是 s2t 或 t2s".to_string()),
    };
    let dictionaries = dictionary_names
        .iter()
        .map(|name| OpenccDictionary::load(&directory.join(name)))
        .collect::<Result<Vec<_>, _>>()?;
    static SEPARATORS_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r"\s+|-|,|\.|\?|!|\*|　|，|。|、|；|：|？|！|…|“|”|‘|’|『|』|「|」|﹁|﹂|—|－|（|）|《|》|〈|〉|～|．|／|＼|︒|︑|︔|︓|︿|﹀|︹|︺|︙|︐|［|﹇|］|﹈|︕|︖|︰|︳|︴|︽|︾|︵|︶|｛|︷|｝|︸|﹃|﹄|【|︻|】|︼",
        )
        .expect("valid OpenCC separator regex")
    });
    let separators = SEPARATORS_PATTERN.clone();
    Ok(OpenccConverter {
        dictionaries,
        separators,
    })
}

impl OpenccDictionary {
    fn load(path: &Path) -> Result<Self, String> {
        let content = fs::read_to_string(path)
            .map_err(|error| format!("读取 OpenCC 词典失败 {}: {error}", path.display()))?;
        let mut entries = HashMap::new();
        let mut min_key_length = usize::MAX;
        let mut max_key_length = 0;
        for (line_number, line) in content.lines().enumerate() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (key, values) = line.split_once('\t').ok_or_else(|| {
                format!("OpenCC 词典格式无效 {}:{}", path.display(), line_number + 1)
            })?;
            let value = values.split_whitespace().next().ok_or_else(|| {
                format!(
                    "OpenCC 词典缺少映射值 {}:{}",
                    path.display(),
                    line_number + 1
                )
            })?;
            let key_length = key.chars().count();
            if key_length == 0 {
                continue;
            }
            min_key_length = min_key_length.min(key_length);
            max_key_length = max_key_length.max(key_length);
            entries.insert(key.to_string(), value.to_string());
        }
        if entries.is_empty() {
            return Err(format!("OpenCC 词典为空: {}", path.display()));
        }
        Ok(Self {
            entries,
            min_key_length,
            max_key_length,
        })
    }
}

impl OpenccConverter {
    fn convert(&self, text: &str) -> String {
        let mut output = String::with_capacity(text.len());
        let mut cursor = 0;
        for separator in self.separators.find_iter(text) {
            output.push_str(&self.convert_segment(&text[cursor..separator.start()]));
            output.push_str(separator.as_str());
            cursor = separator.end();
        }
        output.push_str(&self.convert_segment(&text[cursor..]));
        output
    }

    fn convert_segment(&self, text: &str) -> String {
        let mut segments = vec![OpenccSegment::unmatched(text)];
        for dictionary in &self.dictionaries {
            let mut next = Vec::new();
            for segment in segments {
                if segment.matched {
                    next.push(segment);
                } else {
                    match_dictionary_segment(&segment.text, dictionary, None, &mut next);
                }
            }
            segments = next;
        }
        segments.into_iter().map(|segment| segment.text).collect()
    }
}

struct OpenccSegment {
    text: String,
    matched: bool,
}

impl OpenccSegment {
    fn unmatched(text: &str) -> Self {
        Self {
            text: text.to_string(),
            matched: false,
        }
    }

    fn matched(text: String) -> Self {
        Self {
            text,
            matched: true,
        }
    }
}

fn match_dictionary_segment(
    text: &str,
    dictionary: &OpenccDictionary,
    maximum_length: Option<usize>,
    output: &mut Vec<OpenccSegment>,
) {
    let boundaries: Vec<_> = text
        .char_indices()
        .map(|(index, _)| index)
        .chain(std::iter::once(text.len()))
        .collect();
    let char_count = boundaries.len().saturating_sub(1);
    let mut length = dictionary
        .max_key_length
        .min(maximum_length.unwrap_or(char_count))
        .min(char_count);
    while length >= dictionary.min_key_length {
        for start in 0..=char_count - length {
            let end = start + length;
            let key = &text[boundaries[start]..boundaries[end]];
            if let Some(value) = dictionary.entries.get(key) {
                match_dictionary_segment(
                    &text[..boundaries[start]],
                    dictionary,
                    Some(length),
                    output,
                );
                output.push(OpenccSegment::matched(value.clone()));
                match_dictionary_segment(
                    &text[boundaries[end]..],
                    dictionary,
                    Some(length),
                    output,
                );
                return;
            }
        }
        length -= 1;
    }
    output.push(OpenccSegment::unmatched(text));
}

impl EpubTask for ChineseConvertTask {
    fn task_type(&self) -> TaskType {
        TaskType::ChineseConvert
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        matches!(options.chinese_direction(), Some("s2t" | "t2s"))
            && resource_dir().is_some_and(|directory| {
            directory.join("s2t.json").is_file() && directory.join("t2s.json").is_file()
        })
    }

    fn output_suffix(&self, options: &TaskOptions) -> Result<String, String> {
        match options.chinese_direction() {
            Some("s2t") => Ok("_chinese_convert_tc.epub".to_string()),
            Some("t2s") => Ok("_chinese_convert_sc.epub".to_string()),
            _ => Err("direction 必须是 s2t 或 t2s".to_string()),
        }
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut EpubWorkspace,
        options: &TaskOptions,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        let direction = options
            .chinese_direction()
            .ok_or_else(|| "direction 必须是 s2t 或 t2s".to_string())?;
        let converter = converter(direction)?;
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

fn convert_xml(data: &[u8], converter: &OpenccConverter) -> Result<Vec<u8>, String> {
    let text = decode_xml(data)?;
    static XML_TOKEN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"(?s)<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[^>]+>").expect("valid XML token regex")
    });
    static VISIBLE_ATTRIBUTE: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)(\b(?:alt|title)\s*=\s*[\"'])([^\"']*)([\"'])"#)
            .expect("valid visible attribute regex")
    });
    let mut output = String::with_capacity(text.len());
    let mut cursor = 0;
    let mut blocked_depth = 0_u32;
    for matched in XML_TOKEN.find_iter(&text) {
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
            tag = VISIBLE_ATTRIBUTE
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
    static ENCODING_DECLARATION: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?i)<\?xml\b[^>]*\bencoding\s*=\s*[\"']([^\"']+)[\"']"#)
            .expect("valid XML encoding regex")
    });
    let encoding = ENCODING_DECLARATION
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
    static ENCODING_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r#"(?is)(<\?xml\b[^>]*\bencoding\s*=\s*[\"'])[^\"']*([\"'])"#)
            .expect("valid XML encoding regex")
    });
    ENCODING_PATTERN.replace(text, "${1}UTF-8${2}").into_owned()
}

fn extension_of(path: &str) -> String {
    path.rsplit_once('.')
        .map_or_else(String::new, |(_, extension)| extension.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::{convert_xml, converter, ChineseConvertTask};
    use crate::rust_backend::EpubTask;
    use crate::task_types::TaskOptions;

    fn options(direction: &str) -> TaskOptions {
        TaskOptions::ChineseConvert {
            direction: Some(direction.to_string()),
        }
    }

    #[test]
    fn converts_visible_text_and_attributes_without_touching_script_or_css() {
        let converter = converter("s2t").unwrap();
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
        assert!(ChineseConvertTask.supports_options(&options("s2t")));
    }

    #[test]
    fn bundled_dictionary_matches_python_opencc_s2t_phrase_vectors() {
        let converter = converter("s2t").unwrap();
        assert_eq!(
            converter.convert("游移不定 却才华洋溢 反取憀栗 其中很多只能 看成一出面对"),
            "遊移不定 卻才華洋溢 反取憀慄 其中很多隻能 看成一出面對"
        );
        assert!(ChineseConvertTask.supports_options(&options("s2t")));
    }

    #[test]
    fn bundled_dictionary_matches_python_opencc_t2s_phrase_vectors() {
        let converter = converter("t2s").unwrap();
        assert_eq!(converter.convert("射覆"), "射复");
        assert_eq!(converter.convert("於戲曲 乾隆御用"), "於戏曲 乾隆御用");
        assert!(ChineseConvertTask.supports_options(&options("t2s")));
    }
}
