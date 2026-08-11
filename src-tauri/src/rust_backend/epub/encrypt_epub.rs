use super::{
    rewrite_engine::{is_encrypted_layout, rewrite, supports_rewrite, RewriteMode},
    task_base::ParsedBook,
    workspace::EpubWorkspace,
};
use crate::{
    rust_backend::{EpubTask, TaskOutcome, TaskUpdate},
    task_types::{TaskOptions, TaskType},
};
use std::path::Path;

pub struct EncryptEpubTask;

impl EpubTask for EncryptEpubTask {
    fn task_type(&self) -> TaskType {
        TaskType::EncryptEpub
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
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String> {
        let book = ParsedBook::parse(workspace)?;
        if is_encrypted_layout(&book, workspace) {
            update(TaskUpdate::message("警告: 该文件已加密，无需再次处理！"));
            return Ok(TaskOutcome::Skip);
        }
        rewrite(workspace, RewriteMode::Encrypt, &mut |message| {
            update(TaskUpdate::message(message));
        })?;
        Ok(TaskOutcome::Success)
    }
}
