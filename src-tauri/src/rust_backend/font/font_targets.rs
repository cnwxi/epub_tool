use super::font_style::compute_epub_font_document;
use crate::rust_backend::epub::EpubWorkspace;
use crate::rust_backend::text_encoding::{decode_epub_text, text_kind_for_path};
use std::{collections::BTreeSet, path::Path};

const FONT_EXTENSIONS: [&str; 4] = [".ttf", ".otf", ".woff", ".woff2"];

/// Lists packaged font families from the same document-scoped Stylo results
/// used by font encryption and decryption.
pub fn list_font_targets(input: &Path) -> Result<Vec<String>, String> {
    let workspace = EpubWorkspace::load(input, |_| {})?;
    list_workspace_font_targets(&workspace)
}

fn list_workspace_font_targets(workspace: &EpubWorkspace) -> Result<Vec<String>, String> {
    let mut families = BTreeSet::new();
    for (member, data) in &workspace.members {
        if !is_xhtml(member) {
            continue;
        }
        let source = decode_epub_text(data, text_kind_for_path(member), member)?;
        let document = compute_epub_font_document(&source, member, &workspace.members)?;
        for face in document.faces {
            if face
                .sources
                .iter()
                .any(|source| is_packaged_font(source, workspace))
            {
                families.insert(face.family);
            }
        }
    }
    Ok(families.into_iter().collect())
}

fn is_xhtml(path: &str) -> bool {
    let path = path.to_ascii_lowercase();
    path.ends_with(".xhtml") || path.ends_with(".html") || path.ends_with(".htm")
}

fn is_packaged_font(path: &str, workspace: &EpubWorkspace) -> bool {
    let lower = path.to_ascii_lowercase();
    FONT_EXTENSIONS
        .iter()
        .any(|extension| lower.ends_with(extension))
        && workspace.members.contains_key(path)
}

#[cfg(test)]
mod tests {
    use super::list_workspace_font_targets;
    use crate::rust_backend::epub::EpubWorkspace;
    use std::collections::BTreeMap;

    fn workspace(members: impl IntoIterator<Item = (&'static str, &'static str)>) -> EpubWorkspace {
        EpubWorkspace {
            members: members
                .into_iter()
                .map(|(path, data)| (path.to_string(), data.as_bytes().to_vec()))
                .collect::<BTreeMap<_, _>>(),
            opf_path: "OPS/package.opf".to_string(),
        }
    }

    #[test]
    fn uses_document_scoped_stylo_faces_for_font_targets() {
        let workspace = workspace([
            (
                "OPS/chapter.xhtml",
                r#"<html><head><link rel="stylesheet" href="styles/main.css"/></head><body><p>text</p></body></html>"#,
            ),
            (
                "OPS/styles/main.css",
                r#"@import url("nested.css"); @font-face { font-family: MissingFirst; src: url("missing.ttf"), url("../Fonts/target.woff2"); }"#,
            ),
            (
                "OPS/styles/nested.css",
                r#"@media screen { @font-face { font-family: ActiveNested; src: url("../Fonts/nested.otf"); } }"#,
            ),
            (
                "OPS/styles/unlinked.css",
                r#"@font-face { font-family: Unlinked; src: url("../Fonts/unlinked.ttf"); }"#,
            ),
            ("OPS/Fonts/target.woff2", "font"),
            ("OPS/Fonts/nested.otf", "font"),
            ("OPS/Fonts/unlinked.ttf", "font"),
        ]);

        assert_eq!(
            list_workspace_font_targets(&workspace).unwrap(),
            ["ActiveNested", "MissingFirst"]
        );
    }

    #[test]
    fn includes_inline_font_faces_and_ignores_missing_sources() {
        let workspace = workspace([
            (
                "chapter.xhtml",
                r#"<html><head><style>@font-face { font-family: Inline; src: url("Fonts/inline.ttf"); } @font-face { font-family: Missing; src: url("Fonts/missing.ttf"); }</style></head><body/></html>"#,
            ),
            ("Fonts/inline.ttf", "font"),
        ]);

        assert_eq!(list_workspace_font_targets(&workspace).unwrap(), ["Inline"]);
    }
}
