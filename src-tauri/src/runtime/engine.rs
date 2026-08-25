use std::{
    path::PathBuf,
    sync::{Arc, Mutex},
};

use crate::task_types::{TaskEvent, TaskResult, TaskSpec};

pub struct ExecutionRequest {
    pub task: TaskSpec,
    pub log_path: PathBuf,
}

pub trait EngineRuntime: Send + Sync {
    fn execute(
        &self,
        request: ExecutionRequest,
        emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
    ) -> Result<TaskResult, String>;
}

pub fn create() -> Arc<dyn EngineRuntime> {
    Arc::new(InProcessRuntime::new())
}

struct InProcessRuntime {
    execution: Mutex<()>,
}

impl InProcessRuntime {
    fn new() -> Self {
        Self {
            execution: Mutex::new(()),
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
        crate::rust_backend::run(&request.task, &request.log_path, emit)
    }
}
