use super::image_processing::rewrite_references;
use crate::rust_backend::{
    epub::workspace::{media_type_for, resolve_reference, EpubWorkspace},
    text_encoding::{decode_epub_text, encode_epub_text, TextKind},
    EpubTask, TaskOutcome, TaskUpdate,
};
use crate::task_types::{TaskOptions, TaskType};
use image::{ImageFormat, ImageReader};
use regex::Regex;
use std::{fs, io::Cursor, path::Path, sync::LazyLock};

pub struct ReplaceCoverTask;

impl EpubTask for ReplaceCoverTask {
    fn task_type(&self) -> TaskType {
        TaskType::ReplaceCover
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        let Some(options) = options.replace_cover() else {
            return false;
        };
        options
            .cover_path_by_file
            .iter()
            .all(|(input, cover)| !input.is_empty() && Path::new(cover).is_file())
    }

    fn process(
        &self,
        input: &Path,
        workspace: &mut EpubWorkspace,
        options: &TaskOptions,
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String> {
        let Some(cover_path) = cover_path_for(input, options) else {
            return Ok(TaskOutcome::Skip);
        };
        let raw_cover = fs::read(&cover_path)
            .map_err(|error| format!("读取封面文件失败 {}: {error}", cover_path.display()))?;
        let extension = cover_extension(&raw_cover)?;
        let opf_path = workspace.opf_path.clone();
        let original_opf = workspace
            .members
            .get(&opf_path)
            .ok_or_else(|| format!("EPUB 缺少 OPF 文件: {opf_path}"))?;
        let opf = decode_epub_text(original_opf, TextKind::Xml, &opf_path)?;
        let existing_cover = find_cover_item(&opf, &opf_path)?;
        let old_path = existing_cover
            .as_ref()
            .and_then(|item| item.href.as_deref())
            .map(|href| resolve_reference(&opf_path, href))
            .transpose()?
            .flatten();
        let preferred_dir = old_path
            .as_deref()
            .and_then(parent_path)
            .unwrap_or_else(|| {
                let opf_dir = parent_path(&opf_path).unwrap_or_default();
                if opf_dir.is_empty() {
                    "Images".to_string()
                } else {
                    format!("{opf_dir}/Images")
                }
            });
        let preferred_path = format!("{preferred_dir}/cover{extension}");
        let new_path = unique_path(&preferred_path, workspace, old_path.as_deref());
        let (updated_opf, cover_id) = if let Some(item) = existing_cover {
            let cover_id = item.id.clone().unwrap_or_else(|| next_cover_id(&opf));
            (
                replace_item(&opf, &opf_path, &item, &cover_id, &new_path),
                cover_id,
            )
        } else {
            let cover_id = next_cover_id(&opf);
            (
                insert_cover_item(&opf, &opf_path, &cover_id, &new_path)?,
                cover_id,
            )
        };
        let updated_opf = ensure_cover_metadata(&updated_opf, &cover_id)?;
        workspace.members.insert(
            opf_path.clone(),
            encode_epub_text(&updated_opf, TextKind::Xml),
        );
        if let Some(old_path) = old_path.filter(|old_path| old_path != &new_path) {
            rewrite_references(workspace, &[(old_path, new_path.clone())])?;
        }
        workspace.members.insert(new_path.clone(), raw_cover);
        update(TaskUpdate::message(format!("封面已更换为 {new_path}")));
        Ok(TaskOutcome::Success)
    }
}

struct CoverItem {
    start: usize,
    end: usize,
    tag: String,
    id: Option<String>,
    href: Option<String>,
}

fn cover_path_for(input: &Path, options: &TaskOptions) -> Option<std::path::PathBuf> {
    let mapping = &options.replace_cover()?.cover_path_by_file;
    let input_text = input.to_string_lossy();
    mapping
        .get(input_text.as_ref())
        .map(std::path::PathBuf::from)
        .or_else(|| {
            let canonical_input = fs::canonicalize(input).ok()?;
            mapping.iter().find_map(|(source, cover)| {
                (fs::canonicalize(source).ok().as_ref() == Some(&canonical_input))
                    .then(|| std::path::PathBuf::from(cover))
            })
        })
}

fn cover_extension(data: &[u8]) -> Result<&'static str, String> {
    let reader = ImageReader::new(Cursor::new(data))
        .with_guessed_format()
        .map_err(|error| format!("识别封面格式失败: {error}"))?;
    let format = reader
        .format()
        .ok_or_else(|| "封面不是有效图片".to_string())?;
    reader
        .decode()
        .map_err(|error| format!("封面不是有效图片: {error}"))?;
    match format {
        ImageFormat::Jpeg => Ok(".jpg"),
        ImageFormat::Png => Ok(".png"),
        ImageFormat::WebP => Ok(".webp"),
        _ => Err("封面仅支持 JPG、PNG 或 WebP".to_string()),
    }
}

fn find_cover_item(opf: &str, opf_path: &str) -> Result<Option<CoverItem>, String> {
    static COVER_ITEM: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<item\b[^>]*>"#).expect("valid item regex"));
    let cover_id = find_cover_id(opf);
    for item in COVER_ITEM.find_iter(opf) {
        let tag = item.as_str();
        let properties = attribute(tag, "properties").unwrap_or_default();
        let id = attribute(tag, "id");
        if properties
            .split_whitespace()
            .any(|value| value == "cover-image")
            || cover_id
                .as_deref()
                .is_some_and(|cover_id| id.as_deref() == Some(cover_id))
        {
            let href = attribute(tag, "href");
            if let Some(href) = &href {
                resolve_reference(opf_path, href)?;
            }
            return Ok(Some(CoverItem {
                start: item.start(),
                end: item.end(),
                tag: tag.to_string(),
                id,
                href,
            }));
        }
    }
    Ok(None)
}

fn find_cover_id(opf: &str) -> Option<String> {
    static COVER_META: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<meta\b[^>]*>"#).expect("valid meta regex"));
    let cover_id = COVER_META.find_iter(opf).find_map(|item| {
        (attribute(item.as_str(), "name").as_deref() == Some("cover"))
            .then(|| attribute(item.as_str(), "content"))
            .flatten()
    });
    cover_id
}

fn replace_item(
    opf: &str,
    opf_path: &str,
    item: &CoverItem,
    cover_id: &str,
    new_path: &str,
) -> String {
    let properties = attribute(&item.tag, "properties").unwrap_or_default();
    let mut properties: Vec<&str> = properties.split_whitespace().collect();
    if !properties.contains(&"cover-image") {
        properties.push("cover-image");
    }
    properties.sort_unstable();
    properties.dedup();
    let tag = set_attribute(&item.tag, "id", cover_id);
    let tag = set_attribute(&tag, "href", &relative_from_opf(opf_path, new_path));
    let tag = set_attribute(&tag, "media-type", media_type_for(new_path));
    let tag = set_attribute(&tag, "properties", &properties.join(" "));
    format!("{}{}{}", &opf[..item.start], tag, &opf[item.end..])
}

fn insert_cover_item(
    opf: &str,
    opf_path: &str,
    cover_id: &str,
    new_path: &str,
) -> Result<String, String> {
    static MANIFEST_OPEN: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<manifest\b[^>]*>"#).expect("valid manifest regex"));
    let manifest = MANIFEST_OPEN
        .find(opf)
        .ok_or_else(|| "OPF 缺少 manifest".to_string())?;
    let item = format!(
        "\n    <item id=\"{cover_id}\" href=\"{}\" media-type=\"{}\" properties=\"cover-image\"/>",
        relative_from_opf(opf_path, new_path),
        media_type_for(new_path),
    );
    Ok(format!(
        "{}{}{}",
        &opf[..manifest.end()],
        item,
        &opf[manifest.end()..]
    ))
}

fn ensure_cover_metadata(opf: &str, cover_id: &str) -> Result<String, String> {
    static COVER_META: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<meta\b[^>]*>"#).expect("valid meta regex"));
    if let Some(meta) = COVER_META
        .find_iter(opf)
        .find(|item| attribute(item.as_str(), "name").as_deref() == Some("cover"))
    {
        let updated = set_attribute(meta.as_str(), "content", cover_id);
        return Ok(format!(
            "{}{}{}",
            &opf[..meta.start()],
            updated,
            &opf[meta.end()..]
        ));
    }
    if let Some(index) = opf.find("</metadata>") {
        return Ok(format!(
            "{}    <meta name=\"cover\" content=\"{cover_id}\"/>\n{}",
            &opf[..index],
            &opf[index..]
        ));
    }
    static PACKAGE_OPEN: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<package\b[^>]*>"#).expect("valid package regex"));
    let package = PACKAGE_OPEN
        .find(opf)
        .ok_or_else(|| "OPF 缺少 package 节点".to_string())?;
    Ok(format!(
        "{}\n  <metadata>\n    <meta name=\"cover\" content=\"{cover_id}\"/>\n  </metadata>{}",
        &opf[..package.end()],
        &opf[package.end()..]
    ))
}

fn next_cover_id(opf: &str) -> String {
    static COVER_ITEM: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r#"(?is)<item\b[^>]*>"#).expect("valid item regex"));
    let ids: Vec<String> = COVER_ITEM
        .find_iter(opf)
        .filter_map(|item| attribute(item.as_str(), "id"))
        .collect();
    let mut candidate = "cover-image".to_string();
    let mut index = 2;
    while ids.contains(&candidate) {
        candidate = format!("cover-image-{index}");
        index += 1;
    }
    candidate
}

fn attribute(tag: &str, name: &str) -> Option<String> {
    let pattern = crate::rust_backend::util::cached_regex(&format!(
        r#"(?is)\b{}\s*=\s*[\"']([^\"']*)[\"']"#,
        regex::escape(name)
    ))
    .expect("valid attribute regex");
    pattern
        .captures(tag)
        .map(|captures| captures[1].to_string())
}

fn set_attribute(tag: &str, name: &str, value: &str) -> String {
    let pattern = crate::rust_backend::util::cached_regex(&format!(
        r#"(?is)(\b{}\s*=\s*[\"'])([^\"']*)([\"'])"#,
        regex::escape(name)
    ))
    .expect("valid attribute regex");
    if pattern.is_match(tag) {
        return pattern
            .replace(tag, format!("${{1}}{value}${{3}}"))
            .into_owned();
    }
    let close = tag
        .rfind("/>")
        .or_else(|| tag.rfind('>'))
        .expect("item tag closes");
    format!("{} {}=\"{}\"{}", &tag[..close], name, value, &tag[close..])
}

fn parent_path(path: &str) -> Option<String> {
    path.rsplit_once('/').map(|(parent, _)| parent.to_string())
}

fn relative_from_opf(opf_path: &str, target: &str) -> String {
    let opf_dir = parent_path(opf_path).unwrap_or_default();
    relative_path(&opf_dir, target)
}

fn relative_path(from_directory: &str, target: &str) -> String {
    let from: Vec<&str> = from_directory
        .split('/')
        .filter(|part| !part.is_empty())
        .collect();
    let target: Vec<&str> = target.split('/').filter(|part| !part.is_empty()).collect();
    let common = from
        .iter()
        .zip(&target)
        .take_while(|(left, right)| left == right)
        .count();
    let mut relative = vec![".."; from.len().saturating_sub(common)];
    relative.extend_from_slice(&target[common..]);
    relative.join("/")
}

fn unique_path(preferred: &str, workspace: &EpubWorkspace, current: Option<&str>) -> String {
    if current == Some(preferred) || !workspace.members.contains_key(preferred) {
        return preferred.to_string();
    }
    let (base, extension) = preferred.rsplit_once('.').unwrap_or((preferred, ""));
    let mut index = 2;
    loop {
        let candidate = format!("{base}-{index}.{extension}");
        if !workspace.members.contains_key(&candidate) {
            return candidate;
        }
        index += 1;
    }
}
