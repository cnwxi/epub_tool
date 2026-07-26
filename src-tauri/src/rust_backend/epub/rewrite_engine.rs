//! Shared resource rewrite engine for `encrypt_epub.rs` and `decrypt_epub.rs`.

use super::{
    task_base::{
        basename, md5, md5_hex, replace_tag_block, split_extension, split_slim_href, ManifestItem,
        ParsedBook, ResourceType,
    },
    workspace::{resolve_reference, EpubWorkspace},
};
use regex::{Captures, Regex};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RewriteMode {
    Reformat,
    Encrypt,
    Decrypt,
}

#[derive(Debug, Clone)]
struct Target {
    filename: String,
    resource_type: ResourceType,
}

pub fn is_encrypted_layout(book: &ParsedBook, workspace: &EpubWorkspace) -> bool {
    workspace
        .members
        .keys()
        .any(|path| path.eq_ignore_ascii_case("META-INF/encryption.xml"))
        || book
            .items
            .iter()
            .any(|item| has_unsafe_basename(&item.href))
}

pub fn supports_rewrite(workspace: &EpubWorkspace) -> Result<(), String> {
    let book = ParsedBook::parse(workspace)?;
    book.ensure_all_resources_are_manifested(workspace)?;
    let mut source_paths = BTreeSet::new();
    for item in &book.items {
        if !source_paths.insert(&item.source_path) {
            return Err(format!(
                "manifest 重复引用资源，需使用 Python 兼容实现: {}",
                item.source_path
            ));
        }
        if matches!(item.resource_type, ResourceType::Text | ResourceType::Css) {
            let data = workspace
                .members
                .get(&item.source_path)
                .ok_or_else(|| format!("EPUB 缺少资源: {}", item.source_path))?;
            read_utf8(
                data,
                if item.resource_type == ResourceType::Text {
                    "XHTML"
                } else {
                    "CSS"
                },
                &item.source_path,
            )?;
        }
    }
    Ok(())
}

pub fn rewrite(
    workspace: &mut EpubWorkspace,
    mode: RewriteMode,
    log: &mut dyn FnMut(String),
) -> Result<(), String> {
    let book = ParsedBook::parse(workspace)?;
    book.ensure_all_resources_are_manifested(workspace)?;
    let is_encrypted = is_encrypted_layout(&book, workspace);
    if (mode == RewriteMode::Encrypt && is_encrypted)
        || (mode == RewriteMode::Decrypt && !is_encrypted)
    {
        return Err("当前 EPUB 的加密状态不适合 Rust 重写".to_string());
    }
    let plan = RewritePlan::build(&book, mode, log)?;
    let mut output = BTreeMap::new();
    output.insert("mimetype".to_string(), b"application/epub+zip".to_vec());
    output.insert("META-INF/container.xml".to_string(), book.container.clone());

    for item in &book.items {
        if Some(item.id.as_str()) == book.toc_id.as_deref() {
            let toc = workspace
                .members
                .get(&item.source_path)
                .ok_or_else(|| format!("EPUB 缺少目录文件: {}", item.source_path))?;
            let toc = std::str::from_utf8(toc).map_err(|_| {
                format!(
                    "目录不是 UTF-8，需使用 Python 兼容实现: {}",
                    item.source_path
                )
            })?;
            output.insert(
                "OEBPS/toc.ncx".to_string(),
                rewrite_toc(toc, &item.source_path, &plan).into_bytes(),
            );
            continue;
        }
        let target = plan
            .targets
            .get(&item.source_path)
            .ok_or_else(|| format!("缺少资源改写计划: {}", item.source_path))?;
        let destination = format!(
            "OEBPS/{}/{}",
            target.resource_type.directory(),
            target.filename
        );
        let data = workspace
            .members
            .get(&item.source_path)
            .ok_or_else(|| format!("EPUB 缺少资源: {}", item.source_path))?;
        let data = match item.resource_type {
            ResourceType::Text => rewrite_xhtml(
                read_utf8(data, "XHTML", &item.source_path)?,
                &item.source_path,
                &plan,
            )
            .into_bytes(),
            ResourceType::Css => rewrite_css(
                read_utf8(data, "CSS", &item.source_path)?,
                &item.source_path,
                &plan,
            )
            .into_bytes(),
            _ => data.clone(),
        };
        output.insert(destination, data);
    }
    output.insert(
        "OEBPS/content.opf".to_string(),
        rewrite_opf(&book, &plan)?.into_bytes(),
    );
    workspace.members = output;
    workspace.opf_path = "OEBPS/content.opf".to_string();
    log("已使用 Rust EPUB 重写引擎完成资源路径重构。".to_string());
    Ok(())
}

struct RewritePlan {
    targets: BTreeMap<String, Target>,
    output_ids: BTreeMap<String, String>,
    mode: RewriteMode,
}

impl RewritePlan {
    fn build(
        book: &ParsedBook,
        mode: RewriteMode,
        log: &mut dyn FnMut(String),
    ) -> Result<Self, String> {
        let mut image_id_by_href = BTreeMap::new();
        for item in &book.items {
            let (base, _, slim) = split_slim_href(&item.href);
            if item.resource_type == ResourceType::Image && !slim {
                image_id_by_href.insert(base.to_ascii_lowercase(), item.id.clone());
            }
        }
        let mut output_ids = BTreeMap::new();
        let mut provisional = BTreeMap::new();
        let mut used_ids: BTreeSet<String> =
            book.items.iter().map(|item| item.id.clone()).collect();
        for item in &book.items {
            if Some(item.id.as_str()) == book.toc_id.as_deref() {
                output_ids.insert(item.id.clone(), item.id.clone());
                continue;
            }
            let (base_href, extension, slim) = split_slim_href(&item.href);
            let output_id = if mode == RewriteMode::Decrypt
                && item.resource_type == ResourceType::Image
                && slim
            {
                let base_id = image_id_by_href
                    .get(&base_href.to_ascii_lowercase())
                    .cloned()
                    .unwrap_or_else(|| strip_slim_suffix_from_id(&item.id));
                allocate_slim_id(&base_id, &item.id, &mut used_ids)
            } else {
                item.id.clone()
            };
            output_ids.insert(item.id.clone(), output_id.clone());
            let filename = match mode {
                RewriteMode::Reformat => basename(&item.href).to_string(),
                RewriteMode::Encrypt => {
                    encrypted_filename(item, &image_id_by_href, &base_href, &extension, slim)
                }
                RewriteMode::Decrypt => decrypted_filename(&output_id, &extension, slim),
            };
            log(format!(
                "{} href: {}:{} -> {}",
                match mode {
                    RewriteMode::Reformat => "reformat",
                    RewriteMode::Encrypt => "encrypt",
                    RewriteMode::Decrypt => "decrypt",
                },
                item.id,
                item.href,
                filename
            ));
            provisional.insert(
                item.source_path.clone(),
                Target {
                    filename,
                    resource_type: item.resource_type,
                },
            );
        }
        let mut used_names: BTreeMap<ResourceType, BTreeSet<String>> = BTreeMap::new();
        for target in provisional.values_mut() {
            let used = used_names.entry(target.resource_type).or_default();
            let (stem, extension) = split_extension(&target.filename);
            let mut candidate = target.filename.clone();
            let mut index = 0;
            while used.contains(&candidate) {
                index += 1;
                candidate = format!("{stem}_{index}{extension}");
            }
            used.insert(candidate.clone());
            target.filename = candidate;
        }
        Ok(Self {
            targets: provisional,
            output_ids,
            mode,
        })
    }
}

fn encrypted_filename(
    item: &ManifestItem,
    image_id_by_href: &BTreeMap<String, String>,
    base_href: &str,
    extension: &str,
    slim: bool,
) -> String {
    let id_name = if item.resource_type == ResourceType::Image && slim {
        image_id_by_href
            .get(&base_href.to_ascii_lowercase())
            .cloned()
            .unwrap_or_else(|| item.id.clone())
    } else {
        item.id.clone()
    };
    let id_name = id_name.split('.').next().unwrap_or(&id_name);
    let hash = md5(id_name.as_bytes());
    let number = u128::from_be_bytes(hash);
    let binary = format!("{number:b}");
    let obfuscated: String = binary
        .chars()
        .map(|character| if character == '1' { '*' } else { ':' })
        .collect();
    let slim_suffix = (item.resource_type == ResourceType::Image && slim)
        .then_some("~slim")
        .unwrap_or("");
    format!(
        "_{obfuscated}{slim_suffix}{}",
        extension.to_ascii_lowercase()
    )
}

fn decrypted_filename(output_id: &str, extension: &str, slim: bool) -> String {
    let (stem, _) = split_extension(output_id);
    let stem = if has_unsafe_basename(output_id) {
        md5_hex(stem.as_bytes())
    } else {
        stem.to_string()
    };
    let stem = if slim && stem.to_ascii_lowercase().ends_with("slim") {
        strip_slim_suffix(&stem).to_string()
    } else {
        stem
    };
    format!(
        "{stem}{}{}",
        if slim { "~slim" } else { "" },
        extension.to_ascii_lowercase()
    )
}

fn allocate_slim_id(base_id: &str, old_id: &str, used_ids: &mut BTreeSet<String>) -> String {
    let (stem, extension) = split_extension(base_id);
    let mut candidate = format!("{stem}~slim{extension}");
    let mut sequence = 1;
    while candidate != old_id && used_ids.contains(&candidate) {
        sequence += 1;
        candidate = format!("{stem}_{sequence}~slim{extension}");
    }
    used_ids.remove(old_id);
    used_ids.insert(candidate.clone());
    candidate
}

fn strip_slim_suffix_from_id(item_id: &str) -> String {
    let (stem, extension) = split_extension(item_id);
    format!("{}{}", strip_slim_suffix(stem), extension)
}

fn strip_slim_suffix(value: &str) -> &str {
    let lower = value.to_ascii_lowercase();
    if !lower.ends_with("slim") {
        return value;
    }
    let mut end = value.len() - "slim".len();
    if value[..end].ends_with(['~', '_', '-']) {
        end -= 1;
    }
    &value[..end]
}

fn has_unsafe_basename(value: &str) -> bool {
    basename(value).bytes().any(|byte| {
        matches!(
            byte,
            b'\\' | b'/' | b':' | b'*' | b'?' | b'"' | b'<' | b'>' | b'|'
        )
    })
}

fn read_utf8<'a>(data: &'a [u8], label: &str, path: &str) -> Result<&'a str, String> {
    std::str::from_utf8(data)
        .map_err(|_| format!("{label}资源无法按 UTF-8 读取，需使用 Python 兼容实现: {path}"))
}

fn split_reference(reference: &str) -> (&str, &str) {
    reference
        .find('#')
        .map_or((reference, ""), |index| reference.split_at(index))
}

fn target_for_reference<'a>(
    source_path: &str,
    reference: &str,
    plan: &'a RewritePlan,
) -> Option<&'a Target> {
    let (reference, _) = split_reference(reference);
    resolve_reference(source_path, reference)
        .ok()
        .flatten()
        .and_then(|path| plan.targets.get(&path))
}

fn rewrite_xhtml(source: &str, source_path: &str, plan: &RewritePlan) -> String {
    let mut text = source.to_string();
    if !text.starts_with("<?xml") {
        text = format!("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n{text}");
    }
    if !text.contains("<!DOCTYPE html") {
        text = Regex::new(r"(?s)(<\?xml.*?\?>)\n*")
            .expect("valid xml declaration regex")
            .replacen(&text, 1, "$1\n<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.1//EN\"\n  \"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd\">\n")
            .into_owned();
    }
    text = rewrite_attribute(&text, "href", |reference| {
        let (path, fragment) = split_reference(reference);
        let target = target_for_reference(source_path, path, plan)?;
        match target.resource_type {
            ResourceType::Image => Some(format!("../Images/{}{}", target.filename, fragment)),
            ResourceType::Css => Some(format!("../Styles/{}{}", target.filename, fragment)),
            ResourceType::Text => Some(format!("{}{}", target.filename, fragment)),
            _ => None,
        }
    });
    for attribute in [
        "src",
        "poster",
        "placeholder",
        "activestate",
        "zy-cover-pic",
    ] {
        text = rewrite_attribute(&text, attribute, |reference| {
            let (path, fragment) = split_reference(reference);
            let target = target_for_reference(source_path, path, plan)?;
            let directory = match target.resource_type {
                ResourceType::Image => "Images",
                ResourceType::Audio => "Audio",
                ResourceType::Video => "Video",
                ResourceType::Other => "Misc",
                _ => return None,
            };
            Some(format!("../{directory}/{}{}", target.filename, fragment))
        });
    }
    rewrite_urls(&text, source_path, plan)
}

fn rewrite_css(source: &str, source_path: &str, plan: &RewritePlan) -> String {
    let import =
        Regex::new(r#"(?is)@import\s+(?:([\"'])([^\"']*)[\"']|url\(\s*[\"']?([^\"')]*).*?\))"#)
            .expect("valid css import regex");
    let rewritten = import.replace_all(source, |captures: &Captures<'_>| {
        let raw = captures
            .get(2)
            .or_else(|| captures.get(3))
            .map_or("", |value| value.as_str());
        let (path, fragment) = split_reference(raw);
        let Some(target) = target_for_reference(source_path, path, plan) else {
            return captures[0].to_string();
        };
        if target.resource_type != ResourceType::Css {
            return captures[0].to_string();
        }
        format!("@import \"{}{}\"", target.filename, fragment)
    });
    rewrite_urls(&rewritten, source_path, plan)
}

fn rewrite_urls(source: &str, source_path: &str, plan: &RewritePlan) -> String {
    let url =
        Regex::new(r#"(?is)(url\(\s*[\"']?)([^\"')]*)([\"']?\s*\))"#).expect("valid url regex");
    url.replace_all(source, |captures: &Captures<'_>| {
        let raw = captures.get(2).map_or("", |value| value.as_str());
        let (path, fragment) = split_reference(raw);
        let Some(target) = target_for_reference(source_path, path, plan) else {
            return captures[0].to_string();
        };
        let directory = match target.resource_type {
            ResourceType::Image => "Images",
            ResourceType::Font => "Fonts",
            _ => return captures[0].to_string(),
        };
        format!(
            "{}../{directory}/{}{}{}",
            &captures[1], target.filename, fragment, &captures[3]
        )
    })
    .into_owned()
}

fn rewrite_toc(source: &str, source_path: &str, plan: &RewritePlan) -> String {
    rewrite_attribute(source, "src", |reference| {
        let (path, fragment) = split_reference(reference);
        let target = target_for_reference(source_path, path, plan)?;
        (target.resource_type == ResourceType::Text)
            .then(|| format!("Text/{}{}", target.filename, fragment))
    })
}

fn rewrite_attribute(
    source: &str,
    name: &str,
    mut replacement: impl FnMut(&str) -> Option<String>,
) -> String {
    let pattern = Regex::new(&format!(
        r#"(?is)(<[^>]*\b{name}\s*=\s*[\"'])([^\"']*)([\"'][^>]*>)"#
    ))
    .expect("valid attribute regex");
    pattern
        .replace_all(source, |captures: &Captures<'_>| {
            replacement(&captures[2]).map_or_else(
                || captures[0].to_string(),
                |value| format!("{}{}{}", &captures[1], value, &captures[3]),
            )
        })
        .into_owned()
}

fn rewrite_opf(book: &ParsedBook, plan: &RewritePlan) -> Result<String, String> {
    let mut manifest = String::from("<manifest>");
    for item in &book.items {
        let output_id = plan.output_ids.get(&item.id).unwrap_or(&item.id);
        if Some(item.id.as_str()) == book.toc_id.as_deref() {
            manifest.push_str(&format!("\n    <item id=\"{output_id}\" href=\"toc.ncx\" media-type=\"application/x-dtbncx+xml\"/>"));
            continue;
        }
        let target = plan
            .targets
            .get(&item.source_path)
            .ok_or_else(|| format!("缺少 manifest 资源: {}", item.source_path))?;
        let properties = (!item.properties.is_empty())
            .then(|| format!(" properties=\"{}\"", item.properties))
            .unwrap_or_default();
        manifest.push_str(&format!(
            "\n    <item id=\"{output_id}\" href=\"{}/{}\" media-type=\"{}\"{properties}/>",
            target.resource_type.directory(),
            target.filename,
            item.media_type
        ));
    }
    manifest.push_str("\n  </manifest>");
    let mut opf = replace_tag_block(&book.opf, "manifest", &manifest)?;
    for attribute in [
        "idref",
        "fallback",
        "fallback-style",
        "media-overlay",
        "handler",
        "toc",
    ] {
        opf = rewrite_attribute(&opf, attribute, |value| plan.output_ids.get(value).cloned());
    }
    opf = rewrite_attribute(&opf, "refines", |value| {
        value
            .strip_prefix('#')
            .and_then(|id| plan.output_ids.get(id))
            .map(|id| format!("#{id}"))
    });
    let reference =
        Regex::new(r#"(?is)(<reference\b[^>]*\bhref\s*=\s*[\"'])([^\"']*)([\"'][^>]*/>)"#)
            .expect("valid guide reference regex");
    Ok(reference
        .replace_all(&opf, |captures: &Captures<'_>| {
            let (path, fragment) = split_reference(&captures[2]);
            let target = target_for_reference(&book.opf_path, path, plan);
            target
                .filter(|value| value.resource_type == ResourceType::Text)
                .map_or_else(
                    || captures[0].to_string(),
                    |value| {
                        let prefix = if plan.mode == RewriteMode::Reformat {
                            "../Text/"
                        } else {
                            "Text/"
                        };
                        format!(
                            "{}{prefix}{}{}{}",
                            &captures[1], value.filename, fragment, &captures[3]
                        )
                    },
                )
        })
        .into_owned())
}

#[cfg(test)]
mod tests {
    use super::{decrypted_filename, encrypted_filename};
    use crate::rust_backend::epub::task_base::ManifestItem;
    use std::collections::BTreeMap;

    #[test]
    fn keeps_python_filename_algorithms() {
        let item = ManifestItem {
            id: "f2".to_string(),
            href: "Images/a.jpg".to_string(),
            media_type: "image/jpeg".to_string(),
            properties: String::new(),
            source_path: String::new(),
            resource_type: crate::rust_backend::epub::task_base::ResourceType::Image,
        };
        assert!(
            encrypted_filename(&item, &BTreeMap::new(), "Images/a.jpg", ".jpg", false)
                .ends_with(".jpg")
        );
        assert_eq!(decrypted_filename("f2~slim", ".jpg", true), "f2~slim.jpg");
    }
}
