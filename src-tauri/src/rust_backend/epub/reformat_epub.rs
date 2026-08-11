use super::{
    rewrite_engine::{rewrite, supports_rewrite, RewriteMode},
    workspace::EpubWorkspace,
};
use crate::{
    rust_backend::{EpubTask, TaskOutcome, TaskUpdate},
    task_types::{TaskOptions, TaskType},
};
use std::path::Path;

pub struct ReformatEpubTask;

impl EpubTask for ReformatEpubTask {
    fn task_type(&self) -> TaskType {
        TaskType::ReformatEpub
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
        rewrite(workspace, RewriteMode::Reformat, &mut |message| {
            update(TaskUpdate::message(message));
        })?;
        Ok(TaskOutcome::Success)
    }
}
