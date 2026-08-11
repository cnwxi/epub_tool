use epub_tool_newui::{
    rust_backend, task_types::ChineseConversionDirection, TaskEvent, TaskOptions, TaskResult,
    TaskSpec, TaskType,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::atomic::{AtomicU64, Ordering},
    time::{SystemTime, UNIX_EPOCH},
};
use zip::{write::SimpleFileOptions, CompressionMethod, ZipArchive, ZipWriter};

static NEXT_TEMP_ID: AtomicU64 = AtomicU64::new(0);

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn new(label: &str) -> Self {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock")
            .as_nanos();
        let sequence = NEXT_TEMP_ID.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "epub-tool-{label}-{}-{nonce}-{sequence}",
            std::process::id()
        ));
        fs::create_dir_all(&path).expect("create test directory");
        Self(path)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn task_engine_preserves_suffix_skip_events_and_epub_round_trip() {
    let directory = TestDirectory::new("core-regression");
    let input = directory.path().join("book.epub");
    write_test_epub(&input);

    let (encrypted, encrypt_events) = run_task(
        directory.path(),
        "encrypt",
        TaskType::EncryptEpub,
        input.clone(),
        TaskOptions::Empty,
    );
    assert_eq!(encrypted.status, "success");
    assert_eq!(encrypted.summary.success, 1);
    assert_event_contract(&encrypt_events, "encrypt");
    let encrypted_path = directory.path().join("book_encrypt_epub.epub");
    assert_eq!(encrypted.outputs, [encrypted_path.to_string_lossy()]);
    let encrypted_members = archive_members(&encrypted_path);
    assert!(encrypted_members.contains(&"OEBPS/content.opf".to_string()));
    assert!(!encrypted_members.contains(&"OEBPS/Text/chapter.xhtml".to_string()));

    let (decrypted, decrypt_events) = run_task(
        directory.path(),
        "decrypt",
        TaskType::DecryptEpub,
        encrypted_path,
        TaskOptions::Empty,
    );
    assert_eq!(decrypted.status, "success");
    assert_event_contract(&decrypt_events, "decrypt");
    let decrypted_path = directory.path().join("book_encrypt_epub_decrypt_epub.epub");
    let decrypted_members = archive_members(&decrypted_path);
    assert!(decrypted_members.contains(&"OEBPS/Text/chapter.xhtml".to_string()));
    assert!(archive_text(&decrypted_path, "OEBPS/Text/chapter.xhtml").contains("汉语发展"));

    let already_formatted = directory.path().join("sample_reformat_epub.epub");
    fs::copy(&input, &already_formatted).unwrap();
    let (skipped, skip_events) = run_task(
        directory.path(),
        "skip",
        TaskType::ReformatEpub,
        already_formatted,
        TaskOptions::Empty,
    );
    assert!(skipped.ok);
    assert_eq!(skipped.status, "partial");
    assert_eq!(skipped.summary.skipped, 1);
    assert!(skipped.outputs.is_empty());
    assert_eq!(skip_events.last().unwrap().result.as_ref(), Some(&skipped));
}

#[test]
fn chinese_conversion_uses_stable_direction_suffix_and_rust_resources() {
    let directory = TestDirectory::new("chinese-regression");
    let input = directory.path().join("book.epub");
    write_test_epub(&input);
    let (result, events) = run_task(
        directory.path(),
        "chinese",
        TaskType::ChineseConvert,
        input,
        TaskOptions::ChineseConvert {
            direction: Some(ChineseConversionDirection::SimplifiedToTraditional),
        },
    );

    let output = directory.path().join("book_chinese_convert_tc.epub");
    assert_eq!(result.outputs, [output.to_string_lossy()]);
    assert!(archive_text(&output, "OPS/Text/chapter.xhtml").contains("漢語發展"));
    assert_event_contract(&events, "chinese");
}

#[test]
fn desktop_worker_serve_streams_typed_events_and_result() {
    let directory = TestDirectory::new("worker-regression");
    let input = directory.path().join("book.epub");
    write_test_epub(&input);
    let request = TaskSpec {
        task_id: "worker-task".to_string(),
        task_type: TaskType::ReformatEpub,
        input_files: vec![input],
        output_dir: Some(directory.path().to_path_buf()),
        options: TaskOptions::Empty,
    };
    let worker_request = WorkerRequest {
        request_id: "worker-request",
        request: &request,
        log_path: directory
            .path()
            .join("worker.log")
            .to_string_lossy()
            .into_owned(),
    };
    let mut child = Command::new(env!("CARGO_BIN_EXE_rust-task-runner"))
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn Rust worker");
    {
        let mut stdin = child.stdin.take().expect("worker stdin");
        serde_json::to_writer(&mut stdin, &worker_request).unwrap();
        stdin.write_all(b"\n").unwrap();
        stdin.flush().unwrap();
    }

    let stdout = child.stdout.take().expect("worker stdout");
    let mut events = Vec::new();
    let mut result = None;
    for line in BufReader::new(stdout).lines() {
        let envelope: WorkerEnvelope =
            serde_json::from_str(&line.expect("worker output line")).unwrap();
        assert_eq!(envelope.request_id, "worker-request");
        match envelope.kind.as_str() {
            "event" => events.push(envelope.event.expect("worker event")),
            "result" => {
                result = envelope.result;
                break;
            }
            "error" => panic!("worker error: {}", envelope.error.unwrap_or_default()),
            kind => panic!("unexpected worker envelope: {kind}"),
        }
    }
    let status = child.wait().expect("wait for Rust worker");
    assert!(status.success());
    let result = result.expect("worker result");
    assert_eq!(result.status, "success");
    assert_event_contract(&events, "worker-task");
    assert!(directory.path().join("book_reformat_epub.epub").is_file());
}

fn run_task(
    directory: &Path,
    task_id: &str,
    task_type: TaskType,
    input: PathBuf,
    options: TaskOptions,
) -> (TaskResult, Vec<TaskEvent>) {
    let request = TaskSpec {
        task_id: task_id.to_string(),
        task_type,
        input_files: vec![input],
        output_dir: Some(directory.to_path_buf()),
        options,
    };
    assert!(rust_backend::supports(&request));
    let mut events = Vec::new();
    let result = rust_backend::run(
        &request,
        &directory.join(format!("{task_id}.log")),
        &mut |event| {
            events.push(event);
            Ok(())
        },
    )
    .unwrap();
    (result, events)
}

fn assert_event_contract(events: &[TaskEvent], task_id: &str) {
    assert_eq!(events.first().unwrap().event, "task.started");
    assert_eq!(events.last().unwrap().event, "task.finished");
    assert_eq!(events.last().unwrap().progress, 100.0);
    assert!(events.last().unwrap().result.is_some());
    assert!(events.iter().all(|event| event.task_id == task_id));
    assert!(events
        .iter()
        .all(|event| !event.status.is_empty() && !event.message.is_empty()));
}

fn archive_text(path: &Path, member: &str) -> String {
    let mut archive = ZipArchive::new(fs::File::open(path).unwrap()).unwrap();
    let mut text = String::new();
    archive
        .by_name(member)
        .unwrap()
        .read_to_string(&mut text)
        .unwrap();
    text
}

fn archive_members(path: &Path) -> Vec<String> {
    let mut archive = ZipArchive::new(fs::File::open(path).unwrap()).unwrap();
    (0..archive.len())
        .map(|index| archive.by_index(index).unwrap().name().to_string())
        .collect()
}

fn write_test_epub(path: &Path) {
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
            r#"<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>"#,
        ),
        (
            "OPS/package.opf",
            r#"<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">fixture</dc:identifier><dc:title>Fixture</dc:title><dc:language>zh</dc:language></metadata><manifest><item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/><item id="style" href="Styles/book.css" media-type="text/css"/><item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="toc"><itemref idref="chapter"/></spine></package>"#,
        ),
        (
            "OPS/Text/chapter.xhtml",
            r#"<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>汉语</title><link rel="stylesheet" href="../Styles/book.css"/></head><body><p class="body">汉语发展</p></body></html>"#,
        ),
        (
            "OPS/Styles/book.css",
            ".body { color: #222; font-family: serif; }",
        ),
        (
            "OPS/toc.ncx",
            r#"<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint id="chapter"><navLabel><text>汉语</text></navLabel><content src="Text/chapter.xhtml"/></navPoint></navMap></ncx>"#,
        ),
    ] {
        archive
            .start_file::<_, ()>(
                name,
                SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
            )
            .unwrap();
        archive.write_all(content.as_bytes()).unwrap();
    }
    archive.finish().unwrap();
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct WorkerRequest<'a> {
    request_id: &'a str,
    request: &'a TaskSpec,
    log_path: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct WorkerEnvelope {
    kind: String,
    request_id: String,
    event: Option<TaskEvent>,
    result: Option<TaskResult>,
    error: Option<String>,
}
