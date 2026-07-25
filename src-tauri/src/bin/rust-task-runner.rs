use epub_tool_newui::{rust_backend, FrontendTaskRequest};
use serde_json::{json, Value};
use std::{env, path::PathBuf, process};

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let mut request_json = None;
    let mut log_path = None;
    let mut font_target_path = None;
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--request-json" => request_json = arguments.next(),
            "--log-path" => log_path = arguments.next(),
            "--list-font-targets" => font_target_path = arguments.next(),
            "--help" | "-h" => {
                println!(
                    "Usage: rust-task-runner --request-json <TaskRequest JSON> [--log-path <path>]\n       rust-task-runner --list-font-targets <book.epub>"
                );
                return Ok(());
            }
            _ => return Err(format!("不支持的参数: {argument}")),
        }
    }
    if let Some(input_file) = font_target_path {
        if request_json.is_some() {
            return Err("--list-font-targets 不能与 --request-json 同时使用".to_string());
        }
        let font_families =
            rust_backend::font_targets::list_font_targets(&PathBuf::from(&input_file))?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "ok": true,
                "input_file": input_file,
                "font_families": font_families,
            }))
            .map_err(|error| format!("序列化字体扫描结果失败: {error}"))?
        );
        return Ok(());
    }
    let request_json = request_json.ok_or_else(|| "缺少 --request-json".to_string())?;
    let request: FrontendTaskRequest = serde_json::from_str(&request_json)
        .map_err(|error| format!("TaskRequest JSON 无效: {error}"))?;
    if !rust_backend::supports(&request) {
        return Err(format!(
            "Rust 后端暂不支持此任务或选项: {}",
            request.task_type
        ));
    }
    let log_path = log_path.map(PathBuf::from).unwrap_or_else(|| {
        request
            .output_dir
            .as_deref()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("."))
            .join("rust-task-runner.log")
    });
    let result = rust_backend::run(&request, &log_path, &mut emit_json_line)?;
    if result.get("ok").and_then(Value::as_bool) == Some(true) {
        Ok(())
    } else {
        Err("Rust 任务执行失败".to_string())
    }
}

fn emit_json_line(event: Value) -> Result<(), String> {
    println!(
        "{}",
        serde_json::to_string(&event).map_err(|error| format!("序列化任务事件失败: {error}"))?
    );
    Ok(())
}
