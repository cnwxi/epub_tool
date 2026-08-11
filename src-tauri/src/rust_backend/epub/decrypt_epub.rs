use super::{
    rewrite_engine::{is_encrypted_layout, rewrite, supports_rewrite, RewriteMode},
    task_base::ParsedBook,
    workspace::EpubWorkspace,
};
use crate::{
    rust_backend::{EpubTask, TaskOutcome},
    task_types::{TaskOptions, TaskType},
};
use std::path::Path;

pub struct DecryptEpubTask;

impl EpubTask for DecryptEpubTask {
    fn task_type(&self) -> TaskType {
        TaskType::DecryptEpub
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        matches!(options, TaskOptions::Empty)
    }

    fn supports_input(&self, input: &Path, _options: &TaskOptions) -> bool {
        EpubWorkspace::load(input, |_| {})
            .and_then(|workspace| supports_rewrite(&workspace))
            .is_ok()
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut EpubWorkspace,
        _options: &TaskOptions,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        let book = ParsedBook::parse(workspace)?;
        if !is_encrypted_layout(&book, workspace) {
            log("警告: 该文件未加密，无需处理！".to_string());
            return Ok(TaskOutcome::Skip);
        }
        rewrite(workspace, RewriteMode::Decrypt, log)?;
        Ok(TaskOutcome::Success)
    }
}
