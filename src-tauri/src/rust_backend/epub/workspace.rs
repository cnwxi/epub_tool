use crate::rust_backend::text_encoding::{decode_epub_text, encode_epub_text, TextKind};
use percent_encoding::{percent_decode_str, utf8_percent_encode, AsciiSet, CONTROLS};
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

const MIMETYPE: &[u8] = b"application/epub+zip";
const MAX_MEMBER_SIZE: u64 = 128 * 1024 * 1024;
const MAX_TOTAL_SIZE: u64 = 768 * 1024 * 1024;
const MAX_COMPRESSION_RATIO: u64 = 1000;
const URL_PATH_ENCODE_SET: &AsciiSet = &CONTROLS
    .add(b' ')
    .add(b'"')
    .add(b'<')
    .add(b'>')
    .add(b'`')
    .add(b'#')
    .add(b'?');

#[derive(Debug)]
pub struct EpubWorkspace {
    pub members: BTreeMap<String, Vec<u8>>,
    pub opf_path: String,
}

impl EpubWorkspace {
    pub fn load(input_path: &Path, mut log: impl FnMut(String)) -> Result<Self, String> {
        let file = fs::File::open(input_path)
            .map_err(|error| format!("无法打开 EPUB {}: {error}", input_path.display()))?;
        let mut archive =
            ZipArchive::new(file).map_err(|error| format!("无效 EPUB ZIP: {error}"))?;
        if archive.is_empty() {
            return Err("EPUB 为空".to_string());
        }

        let mut members = BTreeMap::new();
        let mut total_size = 0_u64;
        let mut first_is_mimetype = false;
        let mut mimetype_stored = false;
        for index in 0..archive.len() {
            let mut entry = archive
                .by_index(index)
                .map_err(|error| format!("读取 EPUB 成员失败: {error}"))?;
            let name = normalize_member_path(entry.name())?;
            if name == "mimetype" {
                first_is_mimetype = index == 0;
                mimetype_stored = entry.compression() == CompressionMethod::Stored;
            }
            if entry.is_dir() {
                continue;
            }
            if members.contains_key(&name) {
                return Err(format!("EPUB 包含重复成员: {name}"));
            }
            if entry.size() > MAX_MEMBER_SIZE {
                return Err(format!("EPUB 成员过大: {name}"));
            }
            total_size = total_size.saturating_add(entry.size());
            if total_size > MAX_TOTAL_SIZE {
                return Err("EPUB 解压后总大小超过安全限制".to_string());
            }
            if entry.size() > 0
                && (entry.compressed_size() == 0
                    || entry.size() / entry.compressed_size() > MAX_COMPRESSION_RATIO)
            {
                return Err(format!("EPUB 成员压缩比异常: {name}"));
            }
            let mut data = Vec::with_capacity(entry.size() as usize);
            entry
                .read_to_end(&mut data)
                .map_err(|error| format!("读取 EPUB 成员 {name} 失败: {error}"))?;
            members.insert(name, data);
        }

        if !first_is_mimetype || !mimetype_stored {
            log("输入 EPUB 的 mimetype 不是未压缩的首个 ZIP 成员；本次允许兼容读取，输出时将自动规范化。".to_string());
        }
        if members.get("mimetype").map(Vec::as_slice) != Some(MIMETYPE) {
            return Err("EPUB mimetype 缺失或内容不正确".to_string());
        }
        let container = members
            .get("META-INF/container.xml")
            .ok_or_else(|| "EPUB 缺少 META-INF/container.xml".to_string())?;
        let opf_path = find_opf_path(container, &members)?;
        Ok(Self { members, opf_path })
    }

    pub fn write(&self, output_path: &Path, mut log: impl FnMut(String)) -> Result<(), String> {
        let parent = output_path
            .parent()
            .ok_or_else(|| format!("输出路径没有父目录: {}", output_path.display()))?;
        fs::create_dir_all(parent)
            .map_err(|error| format!("创建输出目录 {} 失败: {error}", parent.display()))?;
        if output_path.exists() {
            if !output_path.is_file() {
                return Err(format!(
                    "输出路径已存在且不是文件: {}",
                    output_path.display()
                ));
            }
            fs::remove_file(output_path).map_err(|error| {
                format!("删除同名输出文件失败 {}: {error}", output_path.display())
            })?;
            log(format!("已删除同名输出文件: {}", output_path.display()));
        }
        let temporary = temporary_output_path(output_path);
        let result = (|| -> Result<(), String> {
            let file = fs::File::create(&temporary)
                .map_err(|error| format!("创建临时 EPUB 失败 {}: {error}", temporary.display()))?;
            let mut archive = ZipWriter::new(file);
            archive
                .start_file::<_, ()>(
                    "mimetype",
                    SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
                )
                .map_err(|error| format!("写入 EPUB mimetype 失败: {error}"))?;
            archive
                .write_all(MIMETYPE)
                .map_err(|error| format!("写入 EPUB mimetype 内容失败: {error}"))?;
            for (name, data) in &self.members {
                if name == "mimetype" {
                    continue;
                }
                archive
                    .start_file::<_, ()>(
                        name,
                        SimpleFileOptions::default()
                            .compression_method(CompressionMethod::Deflated),
                    )
                    .map_err(|error| format!("写入 EPUB 成员 {name} 失败: {error}"))?;
                archive
                    .write_all(data)
                    .map_err(|error| format!("写入 EPUB 成员 {name} 内容失败: {error}"))?;
            }
            archive
                .finish()
                .map_err(|error| format!("完成 EPUB 写入失败: {error}"))?;
            fs::rename(&temporary, output_path).map_err(|error| {
                format!("替换 EPUB 输出文件失败 {}: {error}", output_path.display())
            })?;
            Ok(())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        result
    }

    pub fn mark_generated_by_tool(&mut self) -> Result<(), String> {
        let opf = self
            .members
            .get(&self.opf_path)
            .ok_or_else(|| format!("EPUB 缺少 OPF 文件: {}", self.opf_path))?;
        let text = decode_epub_text(opf, TextKind::Xml, &self.opf_path)?;
        let updated = add_tool_metadata(&text)?;
        self.members.insert(
            self.opf_path.clone(),
            encode_epub_text(&updated, TextKind::Xml),
        );
        Ok(())
    }
}

pub fn normalize_member_path(value: &str) -> Result<String, String> {
    let path = value.replace('\\', "/");
    if path.is_empty() || path.starts_with('/') {
        return Err(format!("EPUB 包含不安全路径: {value}"));
    }
    let mut normalized = Vec::new();
    for part in path.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            if normalized.pop().is_none() {
                return Err(format!("EPUB 包含不安全路径: {value}"));
            }
            continue;
        }
        normalized.push(part);
    }
    if normalized.is_empty() {
        return Err(format!("EPUB 包含不安全路径: {value}"));
    }
    Ok(normalized.join("/"))
}

pub fn resolve_reference(document_path: &str, reference: &str) -> Result<Option<String>, String> {
    let (path, _) = split_reference_suffix(reference);
    if path.is_empty() || is_external_reference(path) {
        return Ok(None);
    }
    let decoded = percent_decode_str(path)
        .decode_utf8()
        .map_err(|_| format!("EPUB 引用不是有效 UTF-8: {reference}"))?;
    let base = document_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
    let combined = if base.is_empty() {
        decoded.into_owned()
    } else {
        format!("{base}/{decoded}")
    };
    normalize_member_path(&combined).map(Some)
}

pub fn rewrite_reference(reference: &str, document_path: &str, target_path: &str) -> String {
    let (_, suffix) = split_reference_suffix(reference);
    let base = document_path
        .rsplit_once('/')
        .map_or("", |(directory, _)| directory);
    let relative = relative_member_path(base, target_path);
    format!(
        "{}{}",
        utf8_percent_encode(&relative, URL_PATH_ENCODE_SET),
        suffix
    )
}

pub fn media_type_for(path: &str) -> &'static str {
    match path
        .rsplit_once('.')
        .map(|(_, extension)| extension.to_ascii_lowercase())
    {
        Some(extension) if extension == "jpg" || extension == "jpeg" => "image/jpeg",
        Some(extension) if extension == "png" => "image/png",
        Some(extension) if extension == "webp" => "image/webp",
        Some(extension) if extension == "bmp" => "image/bmp",
        Some(extension) if extension == "gif" => "image/gif",
        Some(extension) if extension == "svg" => "image/svg+xml",
        _ => "application/octet-stream",
    }
}

fn find_opf_path(container: &[u8], members: &BTreeMap<String, Vec<u8>>) -> Result<String, String> {
    let container = decode_epub_text(container, TextKind::Xml, "META-INF/container.xml")?;
    let marker = "full-path";
    let value_start = container
        .find(marker)
        .and_then(|index| {
            container[index + marker.len()..]
                .find('=')
                .map(|offset| index + marker.len() + offset + 1)
        })
        .ok_or_else(|| "container.xml 缺少 OPF rootfile".to_string())?;
    let after_equals = container[value_start..].trim_start();
    let quote = after_equals
        .chars()
        .next()
        .filter(|value| *value == '\'' || *value == '"')
        .ok_or_else(|| "container.xml 的 OPF 路径无效".to_string())?;
    let rest = &after_equals[quote.len_utf8()..];
    let end = rest
        .find(quote)
        .ok_or_else(|| "container.xml 的 OPF 路径无效".to_string())?;
    let opf_path = normalize_member_path(&rest[..end])?;
    if !members.contains_key(&opf_path) {
        return Err(format!("EPUB 缺少 OPF 文件: {opf_path}"));
    }
    Ok(opf_path)
}

fn add_tool_metadata(opf: &str) -> Result<String, String> {
    if opf.contains("name=\"generator\" content=\"Epub Tool\"")
        || opf.contains("name='generator' content='Epub Tool'")
    {
        return Ok(opf.to_string());
    }
    let meta = "<meta name=\"generator\" content=\"Epub Tool\" />";
    if let Some(index) = opf.find("</metadata>") {
        let prefix = opf[..index].trim_end();
        let closing_indent = opf[..index]
            .rsplit_once('\n')
            .map(|(_, line)| line)
            .filter(|line| {
                line.chars()
                    .all(|character| matches!(character, ' ' | '\t'))
            })
            .unwrap_or("");
        return Ok(format!(
            "{prefix}\n{closing_indent}  {meta}\n{closing_indent}{}",
            &opf[index..]
        ));
    }
    if let Some(index) = opf.find("</package>") {
        return Ok(format!(
            "{}  <metadata>\n    {meta}\n  </metadata>\n{}",
            &opf[..index],
            &opf[index..]
        ));
    }
    Err("content.opf 缺少 package 节点，无法写入工具元数据".to_string())
}

fn split_reference_suffix(reference: &str) -> (&str, &str) {
    let suffix_index = reference
        .char_indices()
        .find_map(|(index, character)| matches!(character, '?' | '#').then_some(index));
    suffix_index.map_or((reference, ""), |index| reference.split_at(index))
}

fn is_external_reference(path: &str) -> bool {
    path.starts_with('/')
        || path.starts_with("//")
        || path.contains("://")
        || path.starts_with("data:")
}

pub(crate) fn relative_member_path(from_directory: &str, target_path: &str) -> String {
    let from: Vec<&str> = from_directory
        .split('/')
        .filter(|part| !part.is_empty())
        .collect();
    let target: Vec<&str> = target_path
        .split('/')
        .filter(|part| !part.is_empty())
        .collect();
    let common = from
        .iter()
        .zip(&target)
        .take_while(|(left, right)| left == right)
        .count();
    let mut result = vec![".."; from.len().saturating_sub(common)];
    result.extend_from_slice(&target[common..]);
    if result.is_empty() {
        ".".to_string()
    } else {
        result.join("/")
    }
}

fn temporary_output_path(output_path: &Path) -> PathBuf {
    let file_name = output_path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("output.epub");
    output_path.with_file_name(format!(".{file_name}.tmp"))
}

#[cfg(test)]
mod tests {
    use super::{
        add_tool_metadata, normalize_member_path, relative_member_path, resolve_reference,
    };

    #[test]
    fn member_paths_reject_parent_traversal() {
        assert!(normalize_member_path("../escape").is_err());
        assert!(normalize_member_path("/escape").is_err());
        assert_eq!(
            normalize_member_path("OPS/./chapter.xhtml").unwrap(),
            "OPS/chapter.xhtml"
        );
    }

    #[test]
    fn resolves_and_rewrites_book_relative_paths() {
        assert_eq!(
            resolve_reference("OPS/Text/chapter.xhtml", "../Images/a.webp").unwrap(),
            Some("OPS/Images/a.webp".to_string())
        );
        assert_eq!(
            relative_member_path("OPS/Text", "OPS/Images/a.png"),
            "../Images/a.png"
        );
    }

    #[test]
    fn writes_generator_metadata_without_disturbing_surrounding_whitespace() {
        let opf = "<metadata>\n    <meta name=\"cover\" content=\"cover.jpg\"/>\n  </metadata>";
        assert_eq!(
            add_tool_metadata(opf).unwrap(),
            "<metadata>\n    <meta name=\"cover\" content=\"cover.jpg\"/>\n    <meta name=\"generator\" content=\"Epub Tool\" />\n  </metadata>"
        );
    }
}
