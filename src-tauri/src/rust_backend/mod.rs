pub mod epub;
pub mod font;
pub mod image;
pub mod text;
pub(crate) mod text_encoding;
pub mod util;

use crate::task_types::{
    FileIssue, TaskEvent, TaskOptions, TaskResult, TaskSpec, TaskSummary, TaskType,
};
use epub::{DecryptEpubTask, EncryptEpubTask, ReformatEpubTask};
use font::DecryptFontTask;
use font::EncryptFontTask;
use image::{ImageProcessOutcome, ImageTask, ReplaceCoverTask};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Instant,
};
use text::ChineseConvertTask;

#[derive(Debug, Clone, PartialEq)]
pub struct TaskUpdate {
    pub message: String,
    pub file_progress: Option<f64>,
}

impl TaskUpdate {
    pub fn message(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            file_progress: None,
        }
    }

    pub fn progress(message: impl Into<String>, file_progress: f64) -> Self {
        Self {
            message: message.into(),
            file_progress: Some(file_progress),
        }
    }
}

pub trait EpubTask: Send + Sync {
    fn task_type(&self) -> TaskType;
    fn supports_options(&self, options: &TaskOptions) -> bool;
    fn supports_input(&self, _input: &Path, _options: &TaskOptions) -> bool {
        true
    }
    fn output_suffix(&self, _options: &TaskOptions) -> Result<String, String> {
        Ok(format!("_{}.epub", self.task_type().as_str()))
    }
    fn process(
        &self,
        input: &Path,
        workspace: &mut epub::EpubWorkspace,
        options: &TaskOptions,
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String>;
}

pub enum TaskOutcome {
    Success,
    Skip,
}

impl EpubTask for ImageTask {
    fn task_type(&self) -> TaskType {
        self.task_type()
    }

    fn supports_options(&self, options: &TaskOptions) -> bool {
        self.is_supported_options(options)
    }

    fn supports_input(&self, input: &Path, options: &TaskOptions) -> bool {
        self.is_supported_input(input, options)
    }

    fn process(
        &self,
        _input: &Path,
        workspace: &mut epub::EpubWorkspace,
        options: &TaskOptions,
        update: &mut dyn FnMut(TaskUpdate),
    ) -> Result<TaskOutcome, String> {
        match self.process(workspace, options, &mut |message| {
            update(TaskUpdate::message(message));
        })? {
            ImageProcessOutcome::Success => Ok(TaskOutcome::Success),
            ImageProcessOutcome::Skip => Ok(TaskOutcome::Skip),
        }
    }
}

pub fn supports(request: &TaskSpec) -> bool {
    task_for(request.task_type).is_some_and(|task| {
        task.supports_options(&request.options)
            && request
                .input_files
                .iter()
                .all(|input| task.supports_input(input, &request.options))
    })
}

pub fn run(
    request: &TaskSpec,
    log_path: &Path,
    emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
) -> Result<TaskResult, String> {
    let task = task_for(request.task_type)
        .ok_or_else(|| format!("Rust 后端暂不支持任务类型: {}", request.task_type.as_str()))?;
    let total_files = request.input_files.len();
    if let Some(output_dir) = &request.output_dir {
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
        format!("正在加载{} Rust 处理模块…", task_label(request.task_type)),
        None,
        0,
        total_files,
        None,
        None,
    ))?;

    let mut outputs = Vec::new();
    let mut errors = Vec::new();
    let mut skipped = Vec::new();
    for (position, input) in request.input_files.iter().enumerate() {
        let index = position + 1;
        let normalized = input.to_string_lossy().to_string();
        let output_suffix = task.output_suffix(&request.options)?;
        let output = output_path(input, request.output_dir.as_deref(), &output_suffix)?;
        let output_text = output.to_string_lossy().to_string();
        emit(event(
            "task.file.started",
            request,
            "running",
            progress(position, total_files),
            format!("开始处理 {}", display_name(input)),
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
            input,
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
                skipped.push(FileIssue {
                    input_file: normalized,
                    message: message.clone(),
                });
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
                errors.push(FileIssue {
                    input_file: normalized,
                    message: message.clone(),
                });
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
    let failed = errors.len();
    let skipped_count = skipped.len();
    let result = TaskResult {
        ok: errors.is_empty(),
        status: status.to_string(),
        outputs,
        errors,
        skipped,
        summary: TaskSummary {
            total: total_files,
            success,
            failed,
            skipped: skipped_count,
        },
        log_path: Some(log_path.to_string_lossy().into_owned()),
    };
    emit(TaskEvent {
        event: "task.finished".to_string(),
        task_id: request.task_id.clone(),
        status: status.to_string(),
        progress: 100.0,
        message: "任务执行完成".to_string(),
        current_file: None,
        current_index: None,
        total_files: Some(total_files),
        output_path: None,
        level: None,
        result: Some(result.clone()),
    })?;
    Ok(result)
}

#[allow(clippy::too_many_arguments)]
fn run_file(
    task: &dyn EpubTask,
    input: &Path,
    output: &Path,
    output_suffix: &str,
    options: &TaskOptions,
    log_path: &Path,
    request: &TaskSpec,
    index: usize,
    total_files: usize,
    emit: &mut dyn FnMut(TaskEvent) -> Result<(), String>,
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
    let last_file_progress = std::cell::Cell::new(0.0);
    let update_error = std::cell::RefCell::new(None);
    let mut update = |update: TaskUpdate| {
        if update_error.borrow().is_some() {
            return;
        }
        let file_progress = match update.file_progress {
            Some(candidate) => match monotonic_file_progress(last_file_progress.get(), candidate) {
                Ok(progress) => progress,
                Err(error) => {
                    *update_error.borrow_mut() = Some(error);
                    return;
                }
            },
            None => last_file_progress.get(),
        };
        last_file_progress.set(file_progress);
        let result = append_log(log_path, &update.message).and_then(|()| {
            emit(event(
                "task.log",
                request,
                "running",
                task_progress_for_file(index, total_files, file_progress),
                update.message,
                Some(input.to_string_lossy().to_string()),
                index,
                total_files,
                Some(output.to_string_lossy().to_string()),
                Some("info"),
            ))
        });
        if let Err(error) = result {
            *update_error.borrow_mut() = Some(error);
        }
    };
    let mut workspace = epub::EpubWorkspace::load(input, |message| {
        update(TaskUpdate::message(message));
    })?;
    take_update_error(&update_error)?;
    let outcome = task.process(input, &mut workspace, options, &mut update)?;
    take_update_error(&update_error)?;
    if matches!(outcome, TaskOutcome::Skip) {
        return Ok(TaskOutcome::Skip);
    }
    workspace.mark_generated_by_tool()?;
    workspace.write(output, |message| {
        update(TaskUpdate::message(message));
    })?;
    take_update_error(&update_error)?;
    Ok(TaskOutcome::Success)
}

fn monotonic_file_progress(previous: f64, candidate: f64) -> Result<f64, String> {
    if !candidate.is_finite() || !(0.0..=100.0).contains(&candidate) {
        return Err(format!(
            "文件处理进度必须是 0 到 100 的有限数值: {candidate}"
        ));
    }
    Ok(previous.max(candidate))
}

fn task_progress_for_file(index: usize, total_files: usize, file_progress: f64) -> f64 {
    if total_files == 0 {
        return 0.0;
    }
    let completed_files = index.saturating_sub(1) as f64;
    (completed_files + file_progress / 100.0) * 100.0 / total_files as f64
}

fn take_update_error(error: &std::cell::RefCell<Option<String>>) -> Result<(), String> {
    match error.borrow_mut().take() {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

fn task_for(task_type: TaskType) -> Option<Box<dyn EpubTask>> {
    match task_type {
        TaskType::ReformatEpub => Some(Box::new(ReformatEpubTask)),
        TaskType::DecryptEpub => Some(Box::new(DecryptEpubTask)),
        TaskType::EncryptEpub => Some(Box::new(EncryptEpubTask)),
        TaskType::EncryptFont => Some(Box::new(EncryptFontTask)),
        TaskType::DecryptFont => Some(Box::new(DecryptFontTask)),
        TaskType::ImageCompress => Some(Box::new(image::image_compress::task())),
        TaskType::ImageToWebp => Some(Box::new(image::image_to_webp::task())),
        TaskType::WebpToImg => Some(Box::new(image::webp_to_img::task())),
        TaskType::ReplaceCover => Some(Box::new(ReplaceCoverTask)),
        TaskType::ChineseConvert => Some(Box::new(ChineseConvertTask)),
    }
}

fn output_path(input: &Path, output_dir: Option<&Path>, suffix: &str) -> Result<PathBuf, String> {
    let parent = output_dir
        .map(Path::to_path_buf)
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

fn task_label(task_type: TaskType) -> String {
    match task_type {
        TaskType::ImageCompress => "图片压缩".to_string(),
        TaskType::ImageToWebp => "图片转 WebP".to_string(),
        TaskType::WebpToImg => "WebP 转图片".to_string(),
        TaskType::ReplaceCover => "更换封面".to_string(),
        TaskType::ChineseConvert => "简繁转换".to_string(),
        _ => task_type.as_str().to_string(),
    }
}

#[allow(clippy::too_many_arguments)]
fn event(
    event: &str,
    request: &TaskSpec,
    status: &str,
    progress: f64,
    message: String,
    current_file: Option<String>,
    current_index: usize,
    total_files: usize,
    output_path: Option<String>,
    level: Option<&str>,
) -> TaskEvent {
    TaskEvent {
        event: event.to_string(),
        task_id: request.task_id.clone(),
        status: status.to_string(),
        progress,
        message,
        current_file,
        current_index: Some(current_index),
        total_files: Some(total_files),
        output_path,
        level: level.map(str::to_string),
        result: None,
    }
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
    use super::{
        monotonic_file_progress, run, run_file, supports, task_progress_for_file, EpubTask,
        TaskOutcome, TaskUpdate,
    };
    use crate::task_types::{FontTaskOptions, ImageTaskOptions, TaskOptions, TaskSpec, TaskType};
    use image::{DynamicImage, ImageFormat, Rgb, RgbImage};
    use std::{
        fs,
        io::{Cursor, Write},
        path::Path,
        time::{SystemTime, UNIX_EPOCH},
    };
    use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

    #[test]
    fn file_progress_is_monotonic_and_maps_into_batch_progress() {
        assert_eq!(monotonic_file_progress(40.0, 25.0).unwrap(), 40.0);
        assert_eq!(monotonic_file_progress(40.0, 75.0).unwrap(), 75.0);
        assert!(monotonic_file_progress(0.0, f64::NAN).is_err());
        assert!(monotonic_file_progress(0.0, 100.1).is_err());

        assert_eq!(task_progress_for_file(1, 1, 25.0), 25.0);
        assert_eq!(task_progress_for_file(2, 4, 0.0), 25.0);
        assert_eq!(task_progress_for_file(2, 4, 50.0), 37.5);
        assert_eq!(task_progress_for_file(2, 4, 100.0), 50.0);
    }

    struct ProgressTask;

    impl EpubTask for ProgressTask {
        fn task_type(&self) -> TaskType {
            TaskType::ReformatEpub
        }

        fn supports_options(&self, options: &TaskOptions) -> bool {
            matches!(options, TaskOptions::Empty)
        }

        fn process(
            &self,
            _input: &Path,
            _workspace: &mut super::epub::EpubWorkspace,
            _options: &TaskOptions,
            update: &mut dyn FnMut(TaskUpdate),
        ) -> Result<TaskOutcome, String> {
            update(TaskUpdate::progress("first", 30.0));
            update(TaskUpdate::progress("regression", 20.0));
            update(TaskUpdate::progress("last", 99.0));
            Ok(TaskOutcome::Success)
        }
    }

    #[test]
    fn typed_file_updates_emit_monotonic_overall_task_progress() {
        let directory = std::env::temp_dir().join(format!(
            "epub-tool-progress-test-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let input = directory.join("book.epub");
        let output = directory.join("book_progress.epub");
        let log_path = directory.join("progress.log");
        write_image_epub(&input);
        fs::write(&log_path, "").unwrap();
        let request = TaskSpec {
            task_id: "progress-test".to_string(),
            task_type: TaskType::ReformatEpub,
            input_files: vec![input.clone(); 4],
            output_dir: Some(directory.clone()),
            options: TaskOptions::Empty,
        };
        let mut events = Vec::new();

        let outcome = run_file(
            &ProgressTask,
            &input,
            &output,
            "_progress.epub",
            &TaskOptions::Empty,
            &log_path,
            &request,
            2,
            4,
            &mut |event| {
                events.push(event);
                Ok(())
            },
        )
        .unwrap();

        assert!(matches!(outcome, TaskOutcome::Success));
        assert_eq!(
            events
                .iter()
                .map(|event| event.progress)
                .collect::<Vec<_>>(),
            [32.5, 32.5, 49.75]
        );
        assert!(events.iter().all(|event| event.event == "task.log"));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn font_capability_probe_leaves_epub_validation_to_each_file() {
        let request = TaskSpec {
            task_id: "font-capability".to_string(),
            task_type: TaskType::EncryptFont,
            input_files: vec!["missing.epub".into()],
            output_dir: None,
            options: TaskOptions::Font(FontTaskOptions::default()),
        };

        assert!(supports(&request));
    }

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
        let request = TaskSpec {
            task_id: "native-image-test".to_string(),
            task_type: TaskType::ImageToWebp,
            input_files: vec![input.clone()],
            output_dir: Some(directory.clone()),
            options: TaskOptions::Image(ImageTaskOptions {
                quality: Some(75),
                ..ImageTaskOptions::default()
            }),
        };
        let mut events = Vec::new();

        let result = run(&request, &directory.join("log.txt"), &mut |event| {
            events.push(event);
            Ok(())
        })
        .unwrap();

        assert_eq!(result.status, "success");
        assert_eq!(result.summary.total, 1);
        assert_eq!(result.summary.success, 1);
        assert_eq!(events.first().unwrap().event, "task.started");
        assert_eq!(events.last().unwrap().event, "task.finished");
        for event in &events {
            assert_eq!(event.task_id, "native-image-test");
            assert!(!event.status.is_empty());
            assert!(!event.message.is_empty());
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
