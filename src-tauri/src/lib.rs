mod app;
mod commands;
mod runtime;

pub mod engine_adapter;
pub mod engine_protocol;
pub mod rust_backend;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(non_snake_case)]
pub struct FrontendTaskRequest {
    pub taskId: String,
    pub taskType: String,
    pub inputFiles: Vec<String>,
    pub outputDir: Option<String>,
    #[serde(default)]
    pub options: Value,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    app::run();
}

#[cfg(test)]
mod tests {
    use super::FrontendTaskRequest;

    #[test]
    fn task_request_accepts_only_camel_case_fields() {
        let request: FrontendTaskRequest = serde_json::from_str(
            r#"{
                "taskId":"task-1",
                "taskType":"reformat_epub",
                "inputFiles":["book.epub"],
                "outputDir":"out",
                "options":{}
            }"#,
        )
        .unwrap();

        assert_eq!(request.taskId, "task-1");
        assert_eq!(request.taskType, "reformat_epub");
        assert!(serde_json::from_str::<FrontendTaskRequest>(
            r#"{"task_id":"task-1","task_type":"reformat_epub","input_files":[]}"#
        )
        .is_err());
    }
}
