use super::encrypt_font::FontEncryptionPlan;
use crate::rust_backend::epub::EpubWorkspace;
use std::path::Path;

/// Lists packaged font families from the same document-scoped Stylo results
/// used by font encryption and decryption.
pub fn list_font_targets(input: &Path) -> Result<Vec<String>, String> {
    let workspace = EpubWorkspace::load(input, |_| {})?;
    list_workspace_font_targets(&workspace)
}

fn list_workspace_font_targets(workspace: &EpubWorkspace) -> Result<Vec<String>, String> {
    FontEncryptionPlan::build_for_target_scan(workspace)?
        .used_target_families(workspace)
        .map(|families| families.into_iter().collect())
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
                r#"<html><head><link rel="stylesheet" href="styles/main.css"/></head><body><p class="first">first</p><p class="nested">nested</p></body></html>"#,
            ),
            (
                "OPS/styles/main.css",
                r#"@import url("nested.css"); @font-face { font-family: MissingFirst; src: url("missing.ttf"), url("../Fonts/target.woff2"); } @font-face { font-family: DeclaredUnused; src: url("../Fonts/unused.ttf"); } .first { font-family: MissingFirst; }"#,
            ),
            (
                "OPS/styles/nested.css",
                r#"@media screen { @font-face { font-family: ActiveNested; src: url("../Fonts/nested.otf"); } .nested { font-family: ActiveNested; } }"#,
            ),
            (
                "OPS/styles/unlinked.css",
                r#"@font-face { font-family: Unlinked; src: url("../Fonts/unlinked.ttf"); }"#,
            ),
            ("OPS/Fonts/target.woff2", "font"),
            ("OPS/Fonts/nested.otf", "font"),
            ("OPS/Fonts/unused.ttf", "font"),
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
                r#"<html><head><title>Metadata title</title><style>@font-face { font-family: Inline; src: url("Fonts/inline.ttf"); } @font-face { font-family: Missing; src: url("Fonts/missing.ttf"); }</style></head><body style="font-family: Inline">text</body></html>"#,
            ),
            ("Fonts/inline.ttf", "font"),
        ]);

        assert_eq!(list_workspace_font_targets(&workspace).unwrap(), ["Inline"]);
    }
}
