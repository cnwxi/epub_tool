use super::{
    rewrite_engine::{rewrite, supports_rewrite, RewriteMode},
    workspace::EpubWorkspace,
};
use crate::rust_backend::{EpubTask, TaskOutcome};
use serde_json::Value;
use std::path::Path;

pub struct ReformatEpubTask;

impl EpubTask for ReformatEpubTask {
    fn task_type(&self) -> &'static str {
        "reformat_epub"
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
        rewrite(workspace, RewriteMode::Reformat, log)?;
        Ok(TaskOutcome::Success)
    }
}
