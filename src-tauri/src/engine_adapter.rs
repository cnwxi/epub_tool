//! Explicit conversions between the versioned Protobuf wire contract and the
//! platform-independent Rust task contract.

use crate::{
    engine_protocol::v1::{
        engine_response, task_options, FileIssue, FontTargetResult, RunTaskRequest,
        TaskEvent as WireTaskEvent, TaskOptions as WireTaskOptions, TaskResult as WireTaskResult,
        TaskSummary as WireTaskSummary, TaskType as WireTaskType,
    },
    task_types::{
        ChineseConversionDirection, FileIssue as CoreFileIssue, FontTaskOptions, ImageTaskOptions,
        OcrCharPolicy, ReplaceCoverOptions, TaskEvent, TaskOptions, TaskResult, TaskSpec, TaskType,
    },
};
use std::{collections::BTreeMap, path::PathBuf};

pub fn task_spec(request: &RunTaskRequest) -> Result<TaskSpec, String> {
    let task_type = task_type(request.task_type)?;
    Ok(TaskSpec {
        task_id: request.task_id.clone(),
        task_type,
        input_files: request.input_files.iter().map(PathBuf::from).collect(),
        output_dir: request.output_dir.as_ref().map(PathBuf::from),
        options: task_options(task_type, request.options.as_ref())?,
    })
}

pub fn task_result(result: TaskResult) -> Result<WireTaskResult, String> {
    Ok(WireTaskResult {
        ok: result.ok,
        status: result.status,
        outputs: result.outputs,
        errors: result.errors.into_iter().map(file_issue).collect(),
        skipped: result.skipped.into_iter().map(file_issue).collect(),
        summary: Some(WireTaskSummary {
            total: to_u32(result.summary.total, "summary.total")?,
            success: to_u32(result.summary.success, "summary.success")?,
            failed: to_u32(result.summary.failed, "summary.failed")?,
            skipped: to_u32(result.summary.skipped, "summary.skipped")?,
        }),
        log_path: result.log_path,
    })
}

pub fn task_event(event: TaskEvent) -> Result<WireTaskEvent, String> {
    Ok(WireTaskEvent {
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
        result: event.result.map(task_result).transpose()?,
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

pub fn task_result_response(result: TaskResult) -> Result<engine_response::Payload, String> {
    Ok(engine_response::Payload::TaskResult(task_result(result)?))
}

fn task_options(
    task_type: TaskType,
    options: Option<&WireTaskOptions>,
) -> Result<TaskOptions, String> {
    let kind = options
        .and_then(|options| options.kind.as_ref())
        .ok_or_else(|| "任务请求的 options 未指定类型".to_string())?;
    match (task_type, kind) {
        (
            TaskType::ReformatEpub | TaskType::DecryptEpub | TaskType::EncryptEpub,
            task_options::Kind::Empty(_),
        ) => Ok(TaskOptions::Empty),
        (TaskType::EncryptFont | TaskType::DecryptFont, task_options::Kind::Font(options)) => {
            Ok(TaskOptions::Font(FontTaskOptions {
                target_font_families_by_file: options
                    .target_font_families_by_file
                    .iter()
                    .map(|(path, families)| (path.clone(), families.values.clone()))
                    .collect::<BTreeMap<_, _>>(),
                target_font_families: options.target_font_families.clone(),
                ocr_char_policy: options
                    .ocr_char_policy
                    .as_deref()
                    .map(parse_ocr_char_policy)
                    .transpose()?,
                min_ocr_confidence: options.min_ocr_confidence,
            }))
        }
        (TaskType::ImageCompress, task_options::Kind::ImageCompress(options)) => {
            Ok(TaskOptions::Image(ImageTaskOptions {
                quality: None,
                jpeg_quality: optional_quality(options.jpeg_quality, "jpegQuality")?,
                webp_quality: optional_quality(options.webp_quality, "webpQuality")?,
                png_to_jpg: options.png_to_jpg,
                png_quantize: options.png_quantize,
            }))
        }
        (
            TaskType::ImageToWebp | TaskType::WebpToImg,
            task_options::Kind::ImageConversion(options),
        ) => Ok(TaskOptions::Image(ImageTaskOptions {
            quality: optional_quality(options.quality, "quality")?,
            jpeg_quality: None,
            webp_quality: None,
            png_to_jpg: None,
            png_quantize: options.png_quantize,
        })),
        (TaskType::ChineseConvert, task_options::Kind::ChineseConvert(options)) => {
            Ok(TaskOptions::ChineseConvert {
                direction: options
                    .direction
                    .as_deref()
                    .map(parse_chinese_direction)
                    .transpose()?,
            })
        }
        (TaskType::ReplaceCover, task_options::Kind::ReplaceCover(options)) => {
            Ok(TaskOptions::ReplaceCover(ReplaceCoverOptions {
                cover_path_by_file: options.cover_path_by_file.clone().into_iter().collect(),
            }))
        }
        _ => Err(format!("任务 {} 与 options 类型不匹配", task_type.as_str())),
    }
}

fn optional_quality(value: Option<u32>, field: &str) -> Result<Option<u8>, String> {
    value
        .map(|value| {
            if !(1..=100).contains(&value) {
                return Err(format!("{field} 必须是 1 到 100 的整数"));
            }
            u8::try_from(value).map_err(|_| format!("{field} 超出 uint8 范围"))
        })
        .transpose()
}

fn parse_ocr_char_policy(value: &str) -> Result<OcrCharPolicy, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "strict" => Ok(OcrCharPolicy::Strict),
        "compatible" | "external" => Ok(OcrCharPolicy::Compatible),
        _ => Err(format!("不支持的 OCR 字符筛选策略: {value}")),
    }
}

fn parse_chinese_direction(value: &str) -> Result<ChineseConversionDirection, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "s2t" => Ok(ChineseConversionDirection::SimplifiedToTraditional),
        "t2s" => Ok(ChineseConversionDirection::TraditionalToSimplified),
        _ => Err("direction 必须是 s2t 或 t2s".to_string()),
    }
}

fn task_type(value: i32) -> Result<TaskType, String> {
    match WireTaskType::try_from(value).ok() {
        Some(WireTaskType::ReformatEpub) => Ok(TaskType::ReformatEpub),
        Some(WireTaskType::DecryptEpub) => Ok(TaskType::DecryptEpub),
        Some(WireTaskType::EncryptEpub) => Ok(TaskType::EncryptEpub),
        Some(WireTaskType::EncryptFont) => Ok(TaskType::EncryptFont),
        Some(WireTaskType::DecryptFont) => Ok(TaskType::DecryptFont),
        Some(WireTaskType::WebpToImg) => Ok(TaskType::WebpToImg),
        Some(WireTaskType::ImageCompress) => Ok(TaskType::ImageCompress),
        Some(WireTaskType::ImageToWebp) => Ok(TaskType::ImageToWebp),
        Some(WireTaskType::ChineseConvert) => Ok(TaskType::ChineseConvert),
        Some(WireTaskType::ReplaceCover) => Ok(TaskType::ReplaceCover),
        _ => Err("任务请求使用了不支持的 taskType".to_string()),
    }
}

fn file_issue(issue: CoreFileIssue) -> FileIssue {
    FileIssue {
        input_file: issue.input_file,
        message: issue.message,
    }
}

fn to_u32(value: usize, field: &str) -> Result<u32, String> {
    u32::try_from(value).map_err(|_| format!("{field} 超出 Protobuf uint32 范围"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine_protocol::v1::{
        task_options, EmptyOptions, FontFamilies, FontOptions, TaskOptions as WireTaskOptions,
    };

    #[test]
    fn converts_wire_request_to_typed_task_spec() {
        let request = RunTaskRequest {
            task_id: "task-1".to_string(),
            task_type: WireTaskType::DecryptFont as i32,
            input_files: vec!["book.epub".to_string()],
            output_dir: Some("output".to_string()),
            options: Some(WireTaskOptions {
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
                    ocr_char_policy: Some("external".to_string()),
                    min_ocr_confidence: None,
                })),
            }),
        };

        let converted = task_spec(&request).expect("request should convert");
        assert_eq!(converted.task_type, TaskType::DecryptFont);
        assert_eq!(
            converted
                .options
                .font()
                .unwrap()
                .target_font_families_by_file["book.epub"],
            ["Example Font"]
        );
        assert_eq!(
            converted.options.font().unwrap().ocr_char_policy,
            Some(OcrCharPolicy::Compatible)
        );
    }

    #[test]
    fn rejects_mismatched_option_kind() {
        let request = RunTaskRequest {
            task_id: "task-1".to_string(),
            task_type: WireTaskType::ReformatEpub as i32,
            input_files: Vec::new(),
            output_dir: None,
            options: Some(WireTaskOptions {
                kind: Some(task_options::Kind::Font(FontOptions::default())),
            }),
        };
        assert!(task_spec(&request).is_err());

        let mut valid = request;
        valid.options = Some(WireTaskOptions {
            kind: Some(task_options::Kind::Empty(EmptyOptions {})),
        });
        assert!(task_spec(&valid).is_ok());
    }
}
