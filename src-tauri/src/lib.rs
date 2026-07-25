pub mod rust_backend;

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FrontendTaskRequest {
    pub task_id: String,
    pub task_type: String,
    pub input_files: Vec<String>,
    pub output_dir: Option<String>,
    #[serde(default)]
    pub options: Value,
}
