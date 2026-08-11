use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
};

use crate::task_types::{TaskEvent, TaskResult, TaskSpec};
use serde::Serialize;

pub struct ExecutionRequest {
    pub task: TaskSpec,
    pub log_path: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineStatus {
    pub state: String,
    pub message: String,
    pub last_error: Option<String>,
}

impl Default for EngineStatus {
    fn default() -> Self {
        Self {
            state: "ready".to_string(),
            message: "进程内 Rust 处理引擎已就绪".to_string(),
            last_error: None,
        }
    }
}

pub trait EngineRuntime: Send + Sync {
    fn execute(
        &self,
        request: ExecutionRequest,
        emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
    ) -> Result<TaskResult, String>;

    fn status(&self) -> Result<EngineStatus, String>;
}

pub fn create() -> Arc<dyn EngineRuntime> {
    Arc::new(InProcessRuntime::new())
}

struct InProcessRuntime {
    execution: Mutex<()>,
    status: Mutex<EngineStatus>,
}

impl InProcessRuntime {
    fn new() -> Self {
        Self {
            execution: Mutex::new(()),
            status: Mutex::new(EngineStatus::default()),
        }
    }

    fn update_status(&self, state: &str, message: &str, last_error: Option<String>) {
        if let Ok(mut status) = self.status.lock() {
            status.state = state.to_string();
            status.message = message.to_string();
            status.last_error = last_error;
        }
    }
}

impl EngineRuntime for InProcessRuntime {
    fn execute(
        &self,
        request: ExecutionRequest,
        emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
    ) -> Result<TaskResult, String> {
        let _execution = self
            .execution
            .lock()
            .map_err(|_| "进程内 Rust 引擎执行锁已损坏".to_string())?;
        self.update_status("busy", "进程内 Rust 处理引擎正在执行请求", None);
        let result = crate::rust_backend::run(&request.task, &request.log_path, emit);
        self.update_status(
            "ready",
            "进程内 Rust 处理引擎已就绪",
            result.as_ref().err().cloned(),
        );
        result
    }

    fn status(&self) -> Result<EngineStatus, String> {
        self.status
            .lock()
            .map(|status| status.clone())
            .map_err(|_| "进程内 Rust 引擎状态锁已损坏".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::{create, EngineStatus};

    #[test]
    fn default_status_is_ready_in_process() {
        let status = EngineStatus::default();
        assert_eq!(status.state, "ready");
        assert!(status.message.contains("进程内"));
        assert!(status.last_error.is_none());
    }

    #[test]
    fn created_runtime_is_ready_without_starting_a_process() {
        let status = create().status().unwrap();
        assert_eq!(status.state, "ready");
    }
}
