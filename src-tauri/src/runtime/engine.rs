use std::{path::PathBuf, sync::Arc};

use serde::Serialize;
use crate::task_types::{TaskEvent, TaskResult, TaskSpec};

use super::RuntimeResources;

pub struct ExecutionRequest {
    pub request_id: String,
    pub task: TaskSpec,
    pub log_path: PathBuf,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineStatus {
    pub state: String,
    pub message: String,
    pub last_error: Option<String>,
    pub pid: Option<u32>,
    pub recovery_attempts: u8,
    pub auto_restart_limit: u8,
    pub manual_restart_count: u32,
}

impl Default for EngineStatus {
    fn default() -> Self {
        Self {
            state: "stopped".to_string(),
            message: "Rust 处理引擎尚未启动".to_string(),
            last_error: None,
            pid: None,
            recovery_attempts: 0,
            auto_restart_limit: 2,
            manual_restart_count: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::EngineStatus;

    #[test]
    fn default_status_preserves_worker_recovery_policy() {
        let status = EngineStatus::default();
        assert_eq!(status.state, "stopped");
        assert_eq!(status.auto_restart_limit, 2);
        assert_eq!(status.recovery_attempts, 0);
        assert_eq!(status.manual_restart_count, 0);
    }
}

pub trait EngineRuntime: Send + Sync {
    fn execute(
        &self,
        request: ExecutionRequest,
        emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
    ) -> Result<TaskResult, String>;

    fn status(&self) -> Result<EngineStatus, String>;

    fn set_auto_restart_limit(&self, limit: u8) -> Result<EngineStatus, String>;

    fn restart(&self) -> Result<EngineStatus, String>;

    fn shutdown(&self);
}

#[cfg(not(mobile))]
pub fn create(resources: RuntimeResources) -> Arc<dyn EngineRuntime> {
    Arc::new(desktop::DesktopWorkerRuntime::new(resources))
}

#[cfg(mobile)]
pub fn create(_resources: RuntimeResources) -> Arc<dyn EngineRuntime> {
    Arc::new(mobile::MobileInProcessRuntime::new())
}

#[cfg(not(mobile))]
mod desktop {
    use std::{
        io::{BufRead, BufReader, Write},
        process::{Child, ChildStdin, ChildStdout, Command, Stdio},
        sync::{
            atomic::{AtomicBool, AtomicU64, Ordering},
            Arc, Mutex,
        },
    };

    use serde::{Deserialize, Serialize};
    use crate::task_types::{TaskEvent, TaskResult};

    #[cfg(unix)]
    use std::os::unix::process::CommandExt;

    #[cfg(target_os = "windows")]
    use std::os::windows::process::CommandExt;

    use super::{EngineRuntime, EngineStatus, ExecutionRequest, RuntimeResources};
    use crate::runtime::workspace_root;

    const RUST_TASK_RUNNER_NAME: &str = if cfg!(target_os = "windows") {
        "rust-task-runner.exe"
    } else {
        "rust-task-runner"
    };
    const WORKER_STDERR_MAX_LINES: usize = 100;
    #[cfg(target_os = "windows")]
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    struct RustWorker {
        child: Arc<Mutex<Child>>,
        stdin: ChildStdin,
        stdout: BufReader<ChildStdout>,
        stderr_lines: Arc<Mutex<Vec<String>>>,
    }

    pub struct DesktopWorkerRuntime {
        resources: RuntimeResources,
        worker: Mutex<Option<RustWorker>>,
        active_child: Mutex<Option<Arc<Mutex<Child>>>>,
        manual_restart_requested: AtomicBool,
        recovery_epoch: AtomicU64,
        status: Mutex<EngineStatus>,
    }

    #[derive(Debug, Serialize)]
    #[serde(rename_all = "camelCase")]
    struct RustWorkerRequest<'a> {
        request_id: &'a str,
        request: &'a crate::task_types::TaskSpec,
        log_path: String,
    }

    #[derive(Debug, Deserialize)]
    #[serde(rename_all = "camelCase")]
    struct RustWorkerEnvelope {
        kind: String,
        request_id: String,
        event: Option<TaskEvent>,
        result: Option<TaskResult>,
        error: Option<String>,
    }

    impl DesktopWorkerRuntime {
        pub fn new(resources: RuntimeResources) -> Self {
            Self {
                resources,
                worker: Mutex::new(None),
                active_child: Mutex::new(None),
                manual_restart_requested: AtomicBool::new(false),
                recovery_epoch: AtomicU64::new(0),
                status: Mutex::new(EngineStatus::default()),
            }
        }

        fn rust_runner_path() -> Result<std::path::PathBuf, String> {
            if let Ok(path) = std::env::var("EPUB_TOOL_RUST_TASK_RUNNER") {
                if !path.is_empty() {
                    return Ok(path.into());
                }
            }
            if let Some(root) = workspace_root() {
                let path = root
                    .join("src-tauri")
                    .join("target")
                    .join("debug")
                    .join(RUST_TASK_RUNNER_NAME);
                if path.is_file() {
                    return Ok(path);
                }
            }
            let executable = std::env::current_exe()
                .map_err(|error| format!("无法定位桌面应用可执行文件: {error}"))?;
            let path = executable
                .parent()
                .ok_or_else(|| format!("桌面应用可执行文件没有父目录: {}", executable.display()))?
                .join(RUST_TASK_RUNNER_NAME);
            if path.is_file() {
                return Ok(path);
            }
            Err(format!(
                "未找到 Rust Worker 可执行文件。开发态请先构建 {RUST_TASK_RUNNER_NAME}，打包态请确认它已随应用打包。"
            ))
        }

        fn build_command(&self) -> Result<Command, String> {
            let mut command = Command::new(Self::rust_runner_path()?);
            command
                .arg("serve")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if let Some(directory) = &self.resources.opencc_dir {
                command.env("EPUB_TOOL_OPENCC_RESOURCE_DIR", directory);
            }
            if let Some(directory) = &self.resources.ocr_model_dir {
                command.env("EPUB_TOOL_OCR_ONNX_MODEL_DIR", directory);
            }
            #[cfg(unix)]
            command.process_group(0);
            #[cfg(target_os = "windows")]
            command.creation_flags(CREATE_NO_WINDOW);
            Ok(command)
        }

        fn start_worker(&self) -> Result<RustWorker, String> {
            let mut child = self
                .build_command()?
                .spawn()
                .map_err(|error| format!("启动 Rust Worker 失败: {error}"))?;
            let stdin = child
                .stdin
                .take()
                .ok_or_else(|| "无法读取 Rust Worker stdin".to_string())?;
            let stdout = child
                .stdout
                .take()
                .ok_or_else(|| "无法读取 Rust Worker stdout".to_string())?;
            let stderr = child
                .stderr
                .take()
                .ok_or_else(|| "无法读取 Rust Worker stderr".to_string())?;
            let stderr_lines = Arc::new(Mutex::new(Vec::new()));
            let thread_lines = Arc::clone(&stderr_lines);
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    if let Ok(mut lines) = thread_lines.lock() {
                        lines.push(line);
                        if lines.len() > WORKER_STDERR_MAX_LINES {
                            let overflow = lines.len() - WORKER_STDERR_MAX_LINES;
                            lines.drain(..overflow);
                        }
                    }
                }
            });
            Ok(RustWorker {
                child: Arc::new(Mutex::new(child)),
                stdin,
                stdout: BufReader::new(stdout),
                stderr_lines,
            })
        }

        fn ensure_worker(&self, worker_slot: &mut Option<RustWorker>) -> Result<(), String> {
            if let Some(worker) = worker_slot.as_mut() {
                if worker
                    .child
                    .lock()
                    .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?
                    .try_wait()
                    .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?
                    .is_none()
                {
                    return Ok(());
                }
            }

            let worker = self.start_worker()?;
            let pid = worker_pid(&worker).ok_or_else(|| "无法获取 Rust Worker PID".to_string())?;
            let status = self
                .status
                .lock()
                .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
            let limit = status.auto_restart_limit;
            let manual_count = status.manual_restart_count;
            drop(status);
            *worker_slot = Some(worker);
            self.set_status(ready_status(limit, manual_count, pid));
            Ok(())
        }

        fn set_status(&self, status: EngineStatus) {
            if let Ok(mut current) = self.status.lock() {
                *current = status;
            }
        }

        fn set_active_child(&self, child: Option<Arc<Mutex<Child>>>) {
            if let Ok(mut active) = self.active_child.lock() {
                *active = child;
            }
        }

        fn terminate_active_child(&self) -> Result<(), String> {
            let active = self
                .active_child
                .lock()
                .map_err(|_| "活动 Rust Worker 锁已损坏".to_string())?
                .take();
            if let Some(child) = active {
                let mut child = child
                    .lock()
                    .map_err(|_| "活动 Rust Worker 子进程锁已损坏".to_string())?;
                terminate_process_tree(&mut child)?;
            }
            Ok(())
        }

        fn recover(&self, error: &str) {
            let recovery_epoch = self.recovery_epoch.fetch_add(1, Ordering::AcqRel) + 1;
            let (attempt, limit, manual_count) = match self.status.lock() {
                Ok(status) => (
                    status.recovery_attempts.saturating_add(1),
                    status.auto_restart_limit,
                    status.manual_restart_count,
                ),
                Err(_) => return,
            };

            let mut worker_slot = match self.worker.lock() {
                Ok(worker_slot) => worker_slot,
                Err(_) => return,
            };
            if attempt > limit {
                if let Some(worker) = worker_slot.as_mut() {
                    let _ = stop_worker(worker);
                }
                *worker_slot = None;
                self.set_status(EngineStatus {
                    state: "unavailable".to_string(),
                    message: "Rust Worker 自动恢复次数已耗尽".to_string(),
                    last_error: Some(error.to_string()),
                    pid: None,
                    recovery_attempts: limit,
                    auto_restart_limit: limit,
                    manual_restart_count: manual_count,
                });
                return;
            }

            if let Some(worker) = worker_slot.as_mut() {
                let _ = stop_worker(worker);
            }
            *worker_slot = None;
            if self.recovery_epoch.load(Ordering::Acquire) != recovery_epoch {
                return;
            }
            match self.ensure_worker(&mut worker_slot) {
                Ok(()) => {
                    if let Ok(mut status) = self.status.lock() {
                        status.message = "Rust Worker 已自动恢复".to_string();
                        status.recovery_attempts = attempt;
                    }
                }
                Err(restart_error) => self.set_status(EngineStatus {
                    state: "unavailable".to_string(),
                    message: "Rust Worker 自动恢复失败".to_string(),
                    last_error: Some(format!("{error}; {restart_error}")),
                    pid: None,
                    recovery_attempts: attempt,
                    auto_restart_limit: limit,
                    manual_restart_count: manual_count,
                }),
            }
        }
    }

    impl EngineRuntime for DesktopWorkerRuntime {
        fn execute(
            &self,
            request: ExecutionRequest,
            emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
        ) -> Result<TaskResult, String> {
            let worker_request = RustWorkerRequest {
                request_id: &request.request_id,
                request: &request.task,
                log_path: request.log_path.to_string_lossy().to_string(),
            };
            let request_line = serde_json::to_string(&worker_request)
                .map_err(|error| format!("序列化 Rust Worker 请求失败: {error}"))?;
            let mut worker_slot = self
                .worker
                .lock()
                .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
            self.ensure_worker(&mut worker_slot)?;
            let active_child = worker_slot
                .as_ref()
                .map(|worker| Arc::clone(&worker.child))
                .ok_or_else(|| "Rust Worker 未初始化".to_string())?;
            self.set_active_child(Some(active_child));
            if let Ok(mut status) = self.status.lock() {
                status.state = "busy".to_string();
                status.message = "Rust Worker 正在执行请求".to_string();
            }

            let result = execute_worker_request(
                worker_slot
                    .as_mut()
                    .ok_or_else(|| "Rust Worker 未初始化".to_string())?,
                &request.request_id,
                &request_line,
                emit,
            );
            self.set_active_child(None);
            match result {
                Ok(value) => {
                    if let Ok(mut status) = self.status.lock() {
                        status.state = "ready".to_string();
                        status.message = "Rust Worker 已就绪".to_string();
                        status.last_error = None;
                        status.recovery_attempts = 0;
                        status.pid = worker_slot.as_ref().and_then(worker_pid);
                    }
                    Ok(value)
                }
                Err(error) => {
                    drop(worker_slot);
                    if !self.manual_restart_requested.load(Ordering::Acquire) {
                        self.recover(&error);
                    }
                    Err(error)
                }
            }
        }

        fn status(&self) -> Result<EngineStatus, String> {
            let mut worker_slot = self
                .worker
                .lock()
                .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
            let worker_exited = match worker_slot.as_mut() {
                Some(worker) => worker
                    .child
                    .lock()
                    .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?
                    .try_wait()
                    .map(|status| status.is_some())
                    .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?,
                None => false,
            };
            let exhausted = self
                .status
                .lock()
                .map(|status| {
                    status.state == "unavailable"
                        && status.recovery_attempts >= status.auto_restart_limit
                })
                .unwrap_or(false);
            if worker_exited {
                drop(worker_slot);
                if !exhausted {
                    self.recover("Rust Worker 在空闲时意外退出");
                }
            } else if worker_slot.is_none() && !exhausted {
                if let Err(error) = self.ensure_worker(&mut worker_slot) {
                    let mut status = self
                        .status
                        .lock()
                        .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
                    status.state = "unavailable".to_string();
                    status.message = "启动 Rust Worker 失败".to_string();
                    status.last_error = Some(error);
                    status.pid = None;
                }
                drop(worker_slot);
            } else {
                drop(worker_slot);
            }
            self.status
                .lock()
                .map(|status| status.clone())
                .map_err(|_| "Rust Worker 状态锁已损坏".to_string())
        }

        fn set_auto_restart_limit(&self, limit: u8) -> Result<EngineStatus, String> {
            let mut status = self
                .status
                .lock()
                .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
            status.auto_restart_limit = limit.min(5);
            Ok(status.clone())
        }

        fn restart(&self) -> Result<EngineStatus, String> {
            self.manual_restart_requested.store(true, Ordering::Release);
            self.recovery_epoch.fetch_add(1, Ordering::AcqRel);
            let result = (|| {
                self.terminate_active_child()?;
                let mut worker_slot = self
                    .worker
                    .lock()
                    .map_err(|_| "Rust Worker 锁已损坏".to_string())?;
                if let Some(worker) = worker_slot.as_mut() {
                    stop_worker(worker)?;
                }
                *worker_slot = None;
                self.ensure_worker(&mut worker_slot)?;
                let mut status = self
                    .status
                    .lock()
                    .map_err(|_| "Rust Worker 状态锁已损坏".to_string())?;
                status.message = "Rust Worker 已手动重启".to_string();
                status.manual_restart_count = status.manual_restart_count.saturating_add(1);
                Ok(status.clone())
            })();
            self.manual_restart_requested
                .store(false, Ordering::Release);
            result
        }

        fn shutdown(&self) {
            let _ = self.terminate_active_child();
            if let Ok(mut worker_slot) = self.worker.lock() {
                if let Some(worker) = worker_slot.as_mut() {
                    let _ = stop_worker(worker);
                }
                *worker_slot = None;
            }
        }
    }

    fn execute_worker_request(
        worker: &mut RustWorker,
        request_id: &str,
        request_line: &str,
        emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
    ) -> Result<TaskResult, String> {
        worker
            .stdin
            .write_all(request_line.as_bytes())
            .and_then(|_| worker.stdin.write_all(b"\n"))
            .and_then(|_| worker.stdin.flush())
            .map_err(|error| format!("发送 Rust Worker 请求失败: {error}"))?;
        loop {
            let mut line = String::new();
            let bytes_read = worker
                .stdout
                .read_line(&mut line)
                .map_err(|error| format!("读取 Rust Worker 输出失败: {error}"))?;
            if bytes_read == 0 {
                return Err(format!(
                    "Rust Worker 意外退出。{}",
                    worker_stderr_tail(worker)
                ));
            }
            let envelope: RustWorkerEnvelope = serde_json::from_str(line.trim_end())
                .map_err(|error| format!("解析 Rust Worker 响应失败: {error}"))?;
            if envelope.request_id != request_id {
                return Err(format!(
                    "Rust Worker 响应 ID 不匹配，期望 {request_id}，收到 {}",
                    envelope.request_id
                ));
            }
            match envelope.kind.as_str() {
                "event" => emit(
                    envelope
                        .event
                        .ok_or_else(|| "Rust Worker 事件缺少内容".to_string())?,
                )?,
                "result" => {
                    return envelope
                        .result
                        .ok_or_else(|| "Rust Worker 响应缺少任务结果".to_string());
                }
                "error" => {
                    return Err(envelope
                        .error
                        .unwrap_or_else(|| "Rust Worker 返回未知错误".to_string()));
                }
                _ => return Err(format!("Rust Worker 返回未知响应类型: {}", envelope.kind)),
            }
        }
    }

    fn ready_status(limit: u8, manual_restart_count: u32, pid: u32) -> EngineStatus {
        EngineStatus {
            state: "ready".to_string(),
            message: "Rust Worker 已就绪".to_string(),
            last_error: None,
            pid: Some(pid),
            recovery_attempts: 0,
            auto_restart_limit: limit,
            manual_restart_count,
        }
    }

    fn worker_pid(worker: &RustWorker) -> Option<u32> {
        worker.child.lock().ok().map(|child| child.id())
    }

    fn worker_stderr_tail(worker: &RustWorker) -> String {
        worker
            .stderr_lines
            .lock()
            .ok()
            .filter(|lines| !lines.is_empty())
            .map(|lines| format!(" Worker stderr: {}", lines.join(" | ")))
            .unwrap_or_default()
    }

    fn stop_worker(worker: &mut RustWorker) -> Result<(), String> {
        let mut child = worker
            .child
            .lock()
            .map_err(|_| "Rust Worker 子进程锁已损坏".to_string())?;
        terminate_process_tree(&mut child)
    }

    fn terminate_process_tree(child: &mut Child) -> Result<(), String> {
        if child
            .try_wait()
            .map_err(|error| format!("检查 Rust Worker 状态失败: {error}"))?
            .is_some()
        {
            return Ok(());
        }
        #[cfg(unix)]
        {
            let process_group = -(child.id() as i32);
            let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
            if result == 0 {
                return Ok(());
            }
        }
        child
            .kill()
            .map_err(|error| format!("终止 Rust Worker 失败: {error}"))
    }
}
#[cfg(mobile)]
mod mobile {
    use std::sync::Mutex;

    use crate::task_types::{TaskEvent, TaskResult};

    use super::{EngineRuntime, EngineStatus, ExecutionRequest};

    pub struct MobileInProcessRuntime {
        status: Mutex<EngineStatus>,
    }

    impl MobileInProcessRuntime {
        pub fn new() -> Self {
            let status = EngineStatus {
                state: "ready".to_string(),
                message: "移动端 Rust 处理引擎已就绪".to_string(),
                ..EngineStatus::default()
            };
            Self {
                status: Mutex::new(status),
            }
        }
    }

    impl EngineRuntime for MobileInProcessRuntime {
        fn execute(
            &self,
            request: ExecutionRequest,
            emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
        ) -> Result<TaskResult, String> {
            if let Ok(mut status) = self.status.lock() {
                status.state = "busy".to_string();
                status.message = "Rust 处理引擎正在执行请求".to_string();
                status.last_error = None;
            }
            let result = crate::rust_backend::run(&request.task, &request.log_path, emit);
            if let Ok(mut status) = self.status.lock() {
                status.state = "ready".to_string();
                status.message = "移动端 Rust 处理引擎已就绪".to_string();
                status.last_error = result.as_ref().err().cloned();
            }
            result
        }

        fn status(&self) -> Result<EngineStatus, String> {
            self.status
                .lock()
                .map(|status| status.clone())
                .map_err(|_| "移动端 Rust 引擎状态锁已损坏".to_string())
        }

        fn set_auto_restart_limit(&self, limit: u8) -> Result<EngineStatus, String> {
            let mut status = self
                .status
                .lock()
                .map_err(|_| "移动端 Rust 引擎状态锁已损坏".to_string())?;
            status.auto_restart_limit = limit.min(5);
            Ok(status.clone())
        }

        fn restart(&self) -> Result<EngineStatus, String> {
            let mut status = self
                .status
                .lock()
                .map_err(|_| "移动端 Rust 引擎状态锁已损坏".to_string())?;
            status.state = "ready".to_string();
            status.message = "移动端 Rust 处理引擎运行于应用进程，无需重启 Worker".to_string();
            status.last_error = None;
            Ok(status.clone())
        }

        fn shutdown(&self) {}
    }
}
