//! Adapts the stable Protobuf IPC contract to the Rust task engine's internal
//! request and event shapes. The task engine intentionally remains unaware of
//! Tauri and Protobuf generated types.

use crate::{
    engine_protocol::v1::{
        engine_response, task_options, FileIssue, FontTargetResult, ImageCompressOptions,
        ImageConversionOptions, RunTaskRequest, TaskEvent, TaskOptions, TaskResult, TaskSummary,
        TaskType,
    },
    FrontendTaskRequest,
};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

pub fn frontend_task_request(request: &RunTaskRequest) -> Result<FrontendTaskRequest, String> {
    Ok(FrontendTaskRequest {
        taskId: request.task_id.clone(),
        taskType: task_type_name(request.task_type)?.to_string(),
        inputFiles: request.input_files.clone(),
        outputDir: request.output_dir.clone(),
        options: frontend_options(request.options.as_ref())?,
    })
}

pub fn task_result_from_value(value: Value) -> Result<TaskResult, String> {
    let result: BackendTaskResult = serde_json::from_value(value)
        .map_err(|error| format!("Rust 任务结果不符合内部约定: {error}"))?;
    Ok(TaskResult {
        ok: result.ok,
        status: result.status,
        outputs: result.outputs,
        errors: result.errors.into_iter().map(file_issue).collect(),
        skipped: result.skipped.into_iter().map(file_issue).collect(),
        summary: Some(TaskSummary {
            total: to_u32(result.summary.total, "summary.total")?,
            success: to_u32(result.summary.success, "summary.success")?,
            failed: to_u32(result.summary.failed, "summary.failed")?,
            skipped: to_u32(result.summary.skipped, "summary.skipped")?,
        }),
        log_path: result.log_path,
    })
}

pub fn task_event_from_value(value: Value) -> Result<TaskEvent, String> {
    let event: BackendTaskEvent = serde_json::from_value(value)
        .map_err(|error| format!("Rust 任务事件不符合内部约定: {error}"))?;
    Ok(TaskEvent {
        event: event.event,
        task_id: event.task_id,
        status: event.status,
        progress: event.progress,
        message: event.message,
        current_file: event.current_file,
        current_index: event
            .current_index
            .map(|value| to_u32(value, "current_index"))
            .transpose()?,
        total_files: event
            .total_files
            .map(|value| to_u32(value, "total_files"))
            .transpose()?,
        output_path: event.output_path,
        level: event.level.unwrap_or_default(),
        result: event.result.map(task_result_from_backend).transpose()?,
    })
}

pub fn font_target_result(
    input_file: String,
    result: Result<Vec<String>, String>,
) -> FontTargetResult {
    match result {
        Ok(font_families) => FontTargetResult {
            ok: true,
            input_file,
            font_families,
            error: None,
        },
        Err(error) => FontTargetResult {
            ok: false,
            input_file,
            font_families: Vec::new(),
            error: Some(error),
        },
    }
}

pub fn task_result_response(result: TaskResult) -> engine_response::Payload {
    engine_response::Payload::TaskResult(result)
}

fn frontend_options(options: Option<&TaskOptions>) -> Result<Value, String> {
    let options = options.ok_or_else(|| "任务请求缺少 options".to_string())?;
    match options.kind.as_ref() {
        Some(task_options::Kind::Empty(_)) => Ok(json!({})),
        Some(task_options::Kind::Font(font)) => {
            let by_file = font
                .target_font_families_by_file
                .iter()
                .map(|(path, families)| (path.clone(), json!(families.values)))
                .collect::<BTreeMap<_, _>>();
            let mut value = Map::new();
            value.insert("target_font_families_by_file".to_string(), json!(by_file));
            if !font.target_font_families.is_empty() {
                value.insert(
                    "target_font_families".to_string(),
                    json!(font.target_font_families),
                );
            }
            if let Some(policy) = &font.ocr_char_policy {
                value.insert("ocr_char_policy".to_string(), json!(policy));
            }
            if let Some(confidence) = font.min_ocr_confidence {
                value.insert("min_ocr_confidence".to_string(), json!(confidence));
            }
            Ok(Value::Object(value))
        }
        Some(task_options::Kind::ImageCompress(options)) => Ok(image_compress_options(options)),
        Some(task_options::Kind::ImageConversion(options)) => Ok(image_conversion_options(options)),
        Some(task_options::Kind::ChineseConvert(options)) => Ok(json!({
            "direction": options.direction,
        })),
        Some(task_options::Kind::ReplaceCover(options)) => Ok(json!({
            "cover_path_by_file": options.cover_path_by_file,
        })),
        None => Err("任务请求的 options 未指定类型".to_string()),
    }
}

fn image_compress_options(options: &ImageCompressOptions) -> Value {
    let mut value = Map::new();
    if let Some(quality) = options.jpeg_quality {
        value.insert("jpeg_quality".to_string(), json!(quality));
    }
    if let Some(quality) = options.webp_quality {
        value.insert("webp_quality".to_string(), json!(quality));
    }
    if let Some(png_to_jpg) = options.png_to_jpg {
        value.insert("png_to_jpg".to_string(), json!(png_to_jpg));
    }
    if let Some(png_quantize) = options.png_quantize {
        value.insert("png_quantize".to_string(), json!(png_quantize));
    }
    Value::Object(value)
}

fn image_conversion_options(options: &ImageConversionOptions) -> Value {
    let mut value = Map::new();
    if let Some(quality) = options.quality {
        value.insert("quality".to_string(), json!(quality));
    }
    if let Some(png_quantize) = options.png_quantize {
        value.insert("png_quantize".to_string(), json!(png_quantize));
    }
    Value::Object(value)
}

fn task_type_name(value: i32) -> Result<&'static str, String> {
    match TaskType::try_from(value).ok() {
        Some(TaskType::ReformatEpub) => Ok("reformat_epub"),
        Some(TaskType::DecryptEpub) => Ok("decrypt_epub"),
        Some(TaskType::EncryptEpub) => Ok("encrypt_epub"),
        Some(TaskType::EncryptFont) => Ok("encrypt_font"),
        Some(TaskType::DecryptFont) => Ok("decrypt_font"),
        Some(TaskType::WebpToImg) => Ok("webp_to_img"),
        Some(TaskType::ImageCompress) => Ok("image_compress"),
        Some(TaskType::ImageToWebp) => Ok("image_to_webp"),
        Some(TaskType::ChineseConvert) => Ok("chinese_convert"),
        Some(TaskType::ReplaceCover) => Ok("replace_cover"),
        _ => Err("任务请求使用了不支持的 taskType".to_string()),
    }
}

fn task_result_from_backend(result: BackendTaskResult) -> Result<TaskResult, String> {
    task_result_from_value(
        serde_json::to_value(result)
            .map_err(|error| format!("序列化 Rust 任务结果失败: {error}"))?,
    )
}

fn file_issue(issue: BackendFileIssue) -> FileIssue {
    FileIssue {
        input_file: issue.input_file,
        message: issue.message,
    }
}

fn to_u32(value: usize, field: &str) -> Result<u32, String> {
    u32::try_from(value).map_err(|_| format!("{field} 超出 Protobuf uint32 范围"))
}

#[derive(Deserialize, serde::Serialize)]
struct BackendTaskResult {
    ok: bool,
    status: String,
    outputs: Vec<String>,
    errors: Vec<BackendFileIssue>,
    skipped: Vec<BackendFileIssue>,
    summary: BackendTaskSummary,
    #[serde(default)]
    log_path: Option<String>,
}

#[derive(Deserialize, serde::Serialize)]
struct BackendFileIssue {
    input_file: String,
    message: String,
}

#[derive(Deserialize, serde::Serialize)]
struct BackendTaskSummary {
    total: usize,
    success: usize,
    failed: usize,
    skipped: usize,
}

#[derive(Deserialize)]
struct BackendTaskEvent {
    event: String,
    task_id: String,
    status: String,
    progress: f64,
    message: String,
    #[serde(default)]
    current_file: Option<String>,
    #[serde(default)]
    current_index: Option<usize>,
    #[serde(default)]
    total_files: Option<usize>,
    #[serde(default)]
    output_path: Option<String>,
    #[serde(default)]
    level: Option<String>,
    #[serde(default)]
    result: Option<BackendTaskResult>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine_protocol::v1::{
        task_options, EmptyOptions, FontFamilies, FontOptions, TaskOptions, TaskType,
    };

    #[test]
    fn converts_wire_request_options_to_rust_engine_shape() {
        let request = RunTaskRequest {
            task_id: "task-1".to_string(),
            task_type: TaskType::EncryptFont as i32,
            input_files: vec!["book.epub".to_string()],
            output_dir: Some("output".to_string()),
            options: Some(TaskOptions {
                kind: Some(task_options::Kind::Font(FontOptions {
                    target_font_families_by_file: [(
                        "book.epub".to_string(),
                        FontFamilies {
                            values: vec!["Example Font".to_string()],
                        },
                    )]
                    .into_iter()
                    .collect(),
                    target_font_families: Vec::new(),
                    ocr_char_policy: None,
                    min_ocr_confidence: None,
                })),
            }),
        };

        let converted = frontend_task_request(&request).expect("request should convert");
        assert_eq!(converted.taskType, "encrypt_font");
        assert_eq!(
            converted.options["target_font_families_by_file"]["book.epub"],
            json!(["Example Font"])
        );
    }

    #[test]
    fn converts_rust_event_to_wire_event() {
        let event = task_event_from_value(json!({
            "event": "task.finished",
            "task_id": "task-1",
            "status": "success",
            "progress": 1.0,
            "message": "完成",
            "current_file": null,
            "current_index": 1,
            "total_files": 1,
            "output_path": null,
            "level": "info",
            "result": {
                "ok": true,
                "status": "success",
                "outputs": ["output.epub"],
                "errors": [],
                "skipped": [],
                "summary": {"total": 1, "success": 1, "failed": 0, "skipped": 0},
                "log_path": "log.txt"
            }
        }))
        .expect("event should convert");

        assert_eq!(event.task_id, "task-1");
        assert_eq!(
            event
                .result
                .expect("finished event result")
                .summary
                .expect("summary")
                .success,
            1
        );
    }

    #[test]
    fn rejects_missing_options_kind() {
        let request = RunTaskRequest {
            task_id: "task-1".to_string(),
            task_type: TaskType::ReformatEpub as i32,
            input_files: Vec::new(),
            output_dir: None,
            options: Some(TaskOptions { kind: None }),
        };
        assert!(frontend_task_request(&request).is_err());

        let empty = TaskOptions {
            kind: Some(task_options::Kind::Empty(EmptyOptions {})),
        };
        assert_eq!(
            frontend_options(Some(&empty)).expect("empty options"),
            json!({})
        );
    }
}
