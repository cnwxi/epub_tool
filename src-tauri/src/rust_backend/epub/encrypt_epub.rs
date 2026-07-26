use super::{
    rewrite_engine::{is_encrypted_layout, rewrite, supports_rewrite, RewriteMode},
    task_base::ParsedBook,
    workspace::EpubWorkspace,
};
use crate::rust_backend::{EpubTask, TaskOutcome};
use serde_json::Value;
use std::path::Path;

pub struct EncryptEpubTask;

impl EpubTask for EncryptEpubTask {
    fn task_type(&self) -> &'static str {
        "encrypt_epub"
    }

    fn supports_options(&self, options: &Value) -> bool {
        options.as_object().is_none_or(|values| values.is_empty())
    }

    fn supports_input(&self, input: &Path, _options: &Value) -> bool {
        EpubWorkspace::load(input, |_| {})
            .and_then(|workspace| supports_rewrite(&workspace))
            .is_ok()
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut EpubWorkspace,
        _options: &Value,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        let book = ParsedBook::parse(workspace)?;
        if is_encrypted_layout(&book, workspace) {
            log("警告: 该文件已加密，无需再次处理！".to_string());
            return Ok(TaskOutcome::Skip);
        }
        rewrite(workspace, RewriteMode::Encrypt, log)?;
        Ok(TaskOutcome::Success)
    }
}
