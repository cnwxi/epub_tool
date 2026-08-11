mod app;
mod commands;
mod runtime;
pub mod task_types;

pub mod engine_adapter;
pub mod engine_protocol;
pub mod rust_backend;

pub use task_types::{TaskEvent, TaskOptions, TaskResult, TaskSpec, TaskType};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    app::run();
}
