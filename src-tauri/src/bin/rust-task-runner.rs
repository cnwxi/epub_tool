use epub_tool_newui::{rust_backend, FrontendTaskRequest};
use serde_json::{json, Value};
use std::{collections::BTreeMap, env, path::PathBuf, process};

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
    let mut font_cmap_path = None;
    let mut rewrite_font_cmap_path = None;
    let mut rewrite_font_output = None;
    let mut cmap_replacements = None;
    let mut cmap_removed_codepoints = None;
    let mut allow_experimental = false;
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--request-json" => request_json = arguments.next(),
            "--log-path" => log_path = arguments.next(),
            "--list-font-targets" => font_target_path = arguments.next(),
            "--read-font-cmap" => font_cmap_path = arguments.next(),
            "--rewrite-font-cmap" => rewrite_font_cmap_path = arguments.next(),
            "--font-output" => rewrite_font_output = arguments.next(),
            "--cmap-replacements" => cmap_replacements = arguments.next(),
            "--remove-cmap-codepoints" => cmap_removed_codepoints = arguments.next(),
            "--allow-experimental" => allow_experimental = true,
            "--help" | "-h" => {
                println!(
                    "Usage: rust-task-runner --request-json <TaskRequest JSON> [--log-path <path>] [--allow-experimental]\n       rust-task-runner --list-font-targets <book.epub>\n       rust-task-runner --read-font-cmap <font-file>\n       rust-task-runner --rewrite-font-cmap <font-file> --font-output <font-file> --cmap-replacements <JSON object> --remove-cmap-codepoints <JSON array>"
                );
                return Ok(());
            }
            _ => return Err(format!("不支持的参数: {argument}")),
        }
    }
    if let Some(input_file) = font_target_path {
        if request_json.is_some() || font_cmap_path.is_some() || rewrite_font_cmap_path.is_some() {
            return Err("字体检查参数不能与 --request-json 或其他字体检查同时使用".to_string());
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
    if let Some(input_file) = font_cmap_path {
        if request_json.is_some() || rewrite_font_cmap_path.is_some() {
            return Err("--read-font-cmap 不能与 --request-json 同时使用".to_string());
        }
        let data = std::fs::read(&input_file)
            .map_err(|error| format!("读取字体失败 {input_file}: {error}"))?;
        let cmap = rust_backend::font_cmap::unicode_cmap(&data)?;
        let entries: Vec<_> = cmap
            .into_iter()
            .map(|(codepoint, glyph_id)| json!({"codepoint": codepoint, "glyph_id": glyph_id}))
            .collect();
        println!(
            "{}",
            serde_json::to_string(&json!({"cmap": entries}))
                .map_err(|error| format!("序列化 cmap 结果失败: {error}"))?
        );
        return Ok(());
    }
    if let Some(input_file) = rewrite_font_cmap_path {
        if request_json.is_some() {
            return Err("--rewrite-font-cmap 不能与 --request-json 同时使用".to_string());
        }
        let output_file = rewrite_font_output
            .ok_or_else(|| "--rewrite-font-cmap 需要同时提供 --font-output".to_string())?;
        let replacements_json = cmap_replacements
            .ok_or_else(|| "--rewrite-font-cmap 需要同时提供 --cmap-replacements".to_string())?;
        let removed_json = cmap_removed_codepoints.ok_or_else(|| {
            "--rewrite-font-cmap 需要同时提供 --remove-cmap-codepoints".to_string()
        })?;
        let replacements: BTreeMap<u32, u16> = serde_json::from_str(&replacements_json)
            .map_err(|error| format!("--cmap-replacements JSON 无效: {error}"))?;
        let removed_codepoints: Vec<u32> = serde_json::from_str(&removed_json)
            .map_err(|error| format!("--remove-cmap-codepoints JSON 无效: {error}"))?;
        let data = std::fs::read(&input_file)
            .map_err(|error| format!("读取字体失败 {input_file}: {error}"))?;
        let rewritten = rust_backend::font_cmap::rewrite_unicode_cmap(
            &data,
            &replacements,
            &removed_codepoints,
        )?;
        std::fs::write(&output_file, rewritten)
            .map_err(|error| format!("写入字体失败 {output_file}: {error}"))?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "ok": true,
                "input_file": input_file,
                "output_file": output_file,
            }))
            .map_err(|error| format!("序列化 cmap 重写结果失败: {error}"))?
        );
        return Ok(());
    }
    let request_json = request_json.ok_or_else(|| "缺少 --request-json".to_string())?;
    let request: FrontendTaskRequest = serde_json::from_str(&request_json)
        .map_err(|error| format!("TaskRequest JSON 无效: {error}"))?;
    if !allow_experimental && !rust_backend::supports(&request) {
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
