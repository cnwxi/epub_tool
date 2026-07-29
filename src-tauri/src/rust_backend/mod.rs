pub mod epub;
pub mod font;
pub mod image;
pub mod text;

use crate::FrontendTaskRequest;
use epub::{DecryptEpubTask, EncryptEpubTask, ReformatEpubTask};
use font::{DecryptFontTask, EncryptFontTask};
use image::{ImageProcessOutcome, ImageTask, ReplaceCoverTask};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};
use text::ChineseConvertTask;

pub trait EpubTask: Send + Sync {
    fn task_type(&self) -> &'static str;
    fn supports_options(&self, options: &Value) -> bool;
    fn supports_input(&self, _input: &Path, _options: &Value) -> bool {
        true
    }
    fn output_suffix(&self, _options: &Value) -> Result<String, String> {
        Ok(format!("_{}.epub", self.task_type()))
    }
    fn process(
        &self,
        input: &Path,
        workspace: &mut epub::EpubWorkspace,
        options: &Value,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String>;
}

pub enum TaskOutcome {
    Success,
    Skip,
}

impl EpubTask for ImageTask {
    fn task_type(&self) -> &'static str {
        self.task_type()
    }

    fn supports_options(&self, options: &Value) -> bool {
        self.is_supported_options(options)
    }

    fn supports_input(&self, input: &Path, options: &Value) -> bool {
        self.is_supported_input(input, options)
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut epub::EpubWorkspace,
        options: &Value,
        log: &mut dyn FnMut(String),
    ) -> Result<TaskOutcome, String> {
        match self.process(workspace, options, log)? {
            ImageProcessOutcome::Success => Ok(TaskOutcome::Success),
            ImageProcessOutcome::Skip => Ok(TaskOutcome::Skip),
        }
    }
}

pub fn supports(request: &FrontendTaskRequest) -> bool {
    task_for(&request.taskType).is_some_and(|task| {
        task.supports_options(&request.options)
            && request
                .inputFiles
                .iter()
                .all(|input| task.supports_input(Path::new(input), &request.options))
    })
}

pub fn run(
    request: &FrontendTaskRequest,
    log_path: &Path,
    emit: &mut dyn FnMut(Value) -> Result<(), String>,
) -> Result<Value, String> {
    let task = task_for(&request.taskType)
        .ok_or_else(|| format!("Rust 后端暂不支持任务类型: {}", request.taskType))?;
    let total_files = request.inputFiles.len();
    if let Some(output_dir) = &request.outputDir {
        let output_dir = Path::new(output_dir);
        if output_dir.exists() && !output_dir.is_dir() {
            return Err(format!("输出路径不是目录: {}", output_dir.display()));
        }
        fs::create_dir_all(output_dir)
            .map_err(|error| format!("创建输出目录 {} 失败: {error}", output_dir.display()))?;
    }
    initialize_log(log_path)?;
    emit(event(
        "task.started",
        request,
        "started",
        0.0,
        format!("正在加载{} Rust 处理模块…", task_label(&request.taskType)),
        None,
        0,
        total_files,
        None,
        None,
    ))?;

    let mut outputs = Vec::new();
    let mut errors = Vec::new();
    let mut skipped = Vec::new();
    for (position, input_file) in request.inputFiles.iter().enumerate() {
        let index = position + 1;
        let input = PathBuf::from(input_file);
        let normalized = input.to_string_lossy().to_string();
        let output_suffix = task.output_suffix(&request.options)?;
        let output = output_path(&input, request.outputDir.as_deref(), &output_suffix)?;
        let output_text = output.to_string_lossy().to_string();
        emit(event(
            "task.file.started",
            request,
            "running",
            progress(position, total_files),
            format!("开始处理 {}", display_name(&input)),
            Some(normalized.clone()),
            index,
            total_files,
            Some(output_text.clone()),
            None,
        ))?;
        let started = Instant::now();
        let output_existed_before = output.exists();
        let result = run_file(
            task.as_ref(),
            &input,
            &output,
            &output_suffix,
            &request.options,
            log_path,
            request,
            index,
            total_files,
            emit,
        );
        let elapsed = started.elapsed().as_millis();
        match result {
            Ok(TaskOutcome::Success) => {
                outputs.push(output_text.clone());
                emit(event(
                    "task.file.finished",
                    request,
                    "success",
                    progress(index, total_files),
                    format!("处理成功，用时 {elapsed}ms"),
                    Some(normalized),
                    index,
                    total_files,
                    Some(output_text),
                    None,
                ))?;
            }
            Ok(TaskOutcome::Skip) => {
                let message = "该文件在当前模式下无需处理，或未选择字体目标。".to_string();
                skipped.push(json!({"input_file": normalized, "message": message}));
                emit(event(
                    "task.file.finished",
                    request,
                    "skip",
                    progress(index, total_files),
                    message,
                    Some(input.to_string_lossy().to_string()),
                    index,
                    total_files,
                    Some(output_text),
                    Some("warning"),
                ))?;
            }
            Err(message) => {
                if !output_existed_before {
                    let _ = fs::remove_file(&output);
                }
                errors.push(json!({"input_file": normalized, "message": message}));
                emit(event(
                    "task.file.finished",
                    request,
                    "error",
                    progress(index, total_files),
                    message,
                    Some(input.to_string_lossy().to_string()),
                    index,
                    total_files,
                    Some(output_text),
                    Some("error"),
                ))?;
            }
        }
    }
    let success = outputs.len();
    let status = if errors.is_empty() && skipped.is_empty() {
        "success"
    } else if errors.is_empty() || success > 0 || !skipped.is_empty() {
        "partial"
    } else {
        "error"
    };
    let result = json!({
        "ok": errors.is_empty(),
        "status": status,
        "outputs": outputs,
        "errors": errors,
        "skipped": skipped,
        "summary": {
            "total": total_files,
            "success": success,
            "failed": errors.len(),
            "skipped": skipped.len(),
        },
        "log_path": log_path,
    });
    emit(json!({
        "event": "task.finished",
        "task_id": request.taskId,
        "status": status,
        "progress": 100,
        "message": "任务执行完成",
        "total_files": total_files,
        "result": result,
    }))?;
    Ok(result)
}

fn run_file(
    task: &dyn EpubTask,
    input: &Path,
    output: &Path,
    output_suffix: &str,
    options: &Value,
    log_path: &Path,
    request: &FrontendTaskRequest,
    index: usize,
    total_files: usize,
    emit: &mut dyn FnMut(Value) -> Result<(), String>,
) -> Result<TaskOutcome, String> {
    if input
        .extension()
        .and_then(|extension| extension.to_str())
        .is_none_or(|extension| !extension.eq_ignore_ascii_case("epub"))
    {
        return Err("当前只支持 .epub 文件".to_string());
    }
    if !input.is_file() {
        return Err(format!("EPUB文件不存在: {}", input.display()));
    }
    if input_has_output_suffix(input, output_suffix) {
        return Ok(TaskOutcome::Skip);
    }
    let mut log = |message: String| -> Result<(), String> {
        append_log(log_path, &message)?;
        emit(event(
            "task.log",
            request,
            "running",
            progress(index.saturating_sub(1), total_files),
            message,
            Some(input.to_string_lossy().to_string()),
            index,
            total_files,
            Some(output.to_string_lossy().to_string()),
            Some("info"),
        ))
    };
    let mut workspace = epub::EpubWorkspace::load(input, |message| {
        let _ = log(message);
    })?;
    let outcome = task.process(input, &mut workspace, options, &mut |message| {
        let _ = log(message);
    })?;
    if matches!(outcome, TaskOutcome::Skip) {
        return Ok(TaskOutcome::Skip);
    }
    workspace.mark_generated_by_tool()?;
    workspace.write(output, |message| {
        let _ = log(message);
    })?;
    Ok(TaskOutcome::Success)
}

fn task_for(task_type: &str) -> Option<Box<dyn EpubTask>> {
    match task_type {
        "reformat_epub" => Some(Box::new(ReformatEpubTask)),
        "decrypt_epub" => Some(Box::new(DecryptEpubTask)),
        "encrypt_epub" => Some(Box::new(EncryptEpubTask)),
        "encrypt_font" => Some(Box::new(EncryptFontTask)),
        "decrypt_font" => Some(Box::new(DecryptFontTask)),
        "image_compress" => Some(Box::new(image::image_compress::task())),
        "image_to_webp" => Some(Box::new(image::image_to_webp::task())),
        "webp_to_img" => Some(Box::new(image::webp_to_img::task())),
        "replace_cover" => Some(Box::new(ReplaceCoverTask)),
        "chinese_convert" => Some(Box::new(ChineseConvertTask)),
        _ => None,
    }
}

fn output_path(input: &Path, output_dir: Option<&str>, suffix: &str) -> Result<PathBuf, String> {
    let parent = output_dir
        .map(PathBuf::from)
        .or_else(|| input.parent().map(Path::to_path_buf))
        .ok_or_else(|| format!("无法确定输出目录: {}", input.display()))?;
    let stem = input
        .file_stem()
        .and_then(|stem| stem.to_str())
        .ok_or_else(|| format!("无效 EPUB 文件名: {}", input.display()))?;
    Ok(parent.join(format!("{stem}{suffix}")))
}

fn input_has_output_suffix(input: &Path, suffix: &str) -> bool {
    input
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.ends_with(suffix))
}

fn task_label(task_type: &str) -> String {
    match task_type {
        "image_compress" => "图片压缩".to_string(),
        "image_to_webp" => "图片转 WebP".to_string(),
        "webp_to_img" => "WebP 转图片".to_string(),
        "replace_cover" => "更换封面".to_string(),
        "chinese_convert" => "简繁转换".to_string(),
        _ => task_type.to_string(),
    }
}

fn event(
    event: &str,
    request: &FrontendTaskRequest,
    status: &str,
    progress: f64,
    message: String,
    current_file: Option<String>,
    current_index: usize,
    total_files: usize,
    output_path: Option<String>,
    level: Option<&str>,
) -> Value {
    let mut value = json!({
        "event": event,
        "task_id": request.taskId,
        "status": status,
        "progress": progress,
        "message": message,
        "current_file": current_file,
        "current_index": current_index,
        "total_files": total_files,
        "output_path": output_path,
    });
    if let Some(level) = level {
        value["level"] = Value::String(level.to_string());
    }
    value
}

fn progress(index: usize, total: usize) -> f64 {
    if total == 0 {
        0.0
    } else {
        index as f64 * 100.0 / total as f64
    }
}

fn display_name(path: &Path) -> String {
    path.file_name()
        .and_then(|name| name.to_str())
        .map_or_else(|| path.to_string_lossy().into_owned(), ToString::to_string)
}

fn initialize_log(log_path: &Path) -> Result<(), String> {
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent).map_err(|error| format!("创建日志目录失败: {error}"))?;
    }
    fs::write(
        log_path,
        format!("time: {:?}\n", std::time::SystemTime::now()),
    )
    .map_err(|error| format!("初始化日志失败: {error}"))
}

fn append_log(log_path: &Path, message: &str) -> Result<(), String> {
    use std::io::Write;
    let mut file = fs::OpenOptions::new()
        .append(true)
        .open(log_path)
        .map_err(|error| format!("写入日志失败: {error}"))?;
    writeln!(file, "{message}").map_err(|error| format!("写入日志失败: {error}"))
}

#[cfg(test)]
mod tests {
    use super::run;
    use crate::FrontendTaskRequest;
    use image::{DynamicImage, ImageFormat, Rgb, RgbImage};
    use serde_json::json;
    use std::{
        fs,
        io::{Cursor, Write},
        time::{SystemTime, UNIX_EPOCH},
    };
    use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

    #[test]
    fn native_image_task_preserves_frontend_result_and_event_schema() {
        let directory = std::env::temp_dir().join(format!(
            "epub-tool-rust-task-test-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let input = directory.join("book.epub");
        write_image_epub(&input);
        let request = FrontendTaskRequest {
            taskId: "native-image-test".to_string(),
            taskType: "image_to_webp".to_string(),
            inputFiles: vec![input.to_string_lossy().to_string()],
            outputDir: Some(directory.to_string_lossy().to_string()),
            options: json!({"quality": 75}),
        };
        let mut events = Vec::new();

        let result = run(&request, &directory.join("log.txt"), &mut |event| {
            events.push(event);
            Ok(())
        })
        .unwrap();

        assert_eq!(result["status"], "success");
        assert_eq!(
            result["summary"],
            json!({"total": 1, "success": 1, "failed": 0, "skipped": 0})
        );
        assert_eq!(events.first().unwrap()["event"], "task.started");
        assert_eq!(events.last().unwrap()["event"], "task.finished");
        for event in &events {
            assert_eq!(event["task_id"], "native-image-test");
            assert!(event.get("status").is_some());
            assert!(event.get("progress").is_some());
            assert!(event.get("message").is_some());
        }
        let output = directory.join("book_image_to_webp.epub");
        let mut archive = ZipArchive::new(fs::File::open(&output).unwrap()).unwrap();
        assert!(archive.by_name("OPS/Images/picture.webp").is_ok());
        let opf = {
            let mut entry = archive.by_name("OPS/package.opf").unwrap();
            let mut content = String::new();
            std::io::Read::read_to_string(&mut entry, &mut content).unwrap();
            content
        };
        assert!(opf.contains("name=\"generator\" content=\"Epub Tool\""));
        fs::remove_dir_all(directory).unwrap();
    }

    fn write_image_epub(path: &std::path::Path) {
        let image = DynamicImage::ImageRgb8(RgbImage::from_pixel(2, 2, Rgb([220, 20, 20])));
        let mut image_bytes = Cursor::new(Vec::new());
        image.write_to(&mut image_bytes, ImageFormat::Png).unwrap();
        let file = fs::File::create(path).unwrap();
        let mut archive = ZipWriter::new(file);
        archive
            .start_file::<_, ()>(
                "mimetype",
                SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
            )
            .unwrap();
        archive.write_all(b"application/epub+zip").unwrap();
        for (name, content) in [
            (
                "META-INF/container.xml",
                br#"<container><rootfiles><rootfile full-path="OPS/package.opf"/></rootfiles></container>"#.as_slice(),
            ),
            (
                "OPS/package.opf",
                br#"<package><metadata/><manifest><item id="image" href="Images/picture.png" media-type="image/png"/></manifest></package>"#.as_slice(),
            ),
            (
                "OPS/chapter.xhtml",
                br#"<html><body><img src="Images/picture.png"/></body></html>"#.as_slice(),
            ),
        ] {
            archive
                .start_file::<_, ()>(
                    name,
                    SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
                )
                .unwrap();
            archive.write_all(content).unwrap();
        }
        archive
            .start_file::<_, ()>(
                "OPS/Images/picture.png",
                SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
            )
            .unwrap();
        archive.write_all(&image_bytes.into_inner()).unwrap();
        archive.finish().unwrap();
    }
}
