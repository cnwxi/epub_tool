use epub_tool_newui::{
    rust_backend,
    task_types::{ChineseConversionDirection, ImageTaskOptions, ReplaceCoverOptions},
    TaskEvent, TaskOptions, TaskResult, TaskSpec, TaskType,
};
use image::{DynamicImage, ImageFormat, Rgb, RgbImage};
use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Write},
    path::{Path, PathBuf},
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
fn remaining_epub_tasks_run_through_the_unified_core() {
    let directory = TestDirectory::new("all-task-regression");
    let input = directory.path().join("book.epub");
    write_test_epub(&input);

    let (reformatted, reformat_events) = run_task(
        directory.path(),
        "reformat",
        TaskType::ReformatEpub,
        input.clone(),
        TaskOptions::Empty,
    );
    assert_eq!(reformatted.summary.success, 1);
    assert_event_contract(&reformat_events, "reformat");
    assert!(
        archive_members(&directory.path().join("book_reformat_epub.epub"))
            .contains(&"OEBPS/content.opf".to_string())
    );

    let image_options = TaskOptions::Image(ImageTaskOptions {
        quality: Some(75),
        jpeg_quality: Some(75),
        webp_quality: Some(75),
        png_to_jpg: Some(false),
        png_quantize: Some(false),
    });
    let (to_webp, to_webp_events) = run_task(
        directory.path(),
        "to-webp",
        TaskType::ImageToWebp,
        input.clone(),
        image_options.clone(),
    );
    assert_eq!(to_webp.summary.success, 1);
    assert_event_contract(&to_webp_events, "to-webp");
    let webp_path = directory.path().join("book_image_to_webp.epub");
    assert!(archive_members(&webp_path).contains(&"OPS/Images/sample.webp".to_string()));
    assert!(archive_text(&webp_path, "OPS/Text/chapter.xhtml").contains("sample.webp"));
    assert!(archive_text(&webp_path, "OPS/package.opf").contains("image/webp"));

    let (from_webp, from_webp_events) = run_task(
        directory.path(),
        "from-webp",
        TaskType::WebpToImg,
        webp_path,
        image_options.clone(),
    );
    assert_eq!(from_webp.summary.success, 1);
    assert_event_contract(&from_webp_events, "from-webp");
    let image_path = directory.path().join("book_image_to_webp_webp_to_img.epub");
    assert!(archive_members(&image_path).contains(&"OPS/Images/sample.jpg".to_string()));
    assert!(archive_text(&image_path, "OPS/Text/chapter.xhtml").contains("sample.jpg"));

    let (compressed, compress_events) = run_task(
        directory.path(),
        "compress",
        TaskType::ImageCompress,
        input.clone(),
        image_options,
    );
    assert_eq!(compressed.summary.success, 1);
    assert_event_contract(&compress_events, "compress");
    assert!(directory.path().join("book_image_compress.epub").is_file());

    let cover_path = directory.path().join("replacement.png");
    let cover_data = fixture_png(48, 64);
    fs::write(&cover_path, &cover_data).unwrap();
    let (replaced, replace_events) = run_task(
        directory.path(),
        "replace-cover",
        TaskType::ReplaceCover,
        input.clone(),
        TaskOptions::ReplaceCover(ReplaceCoverOptions {
            cover_path_by_file: BTreeMap::from([(
                input.to_string_lossy().into_owned(),
                cover_path.to_string_lossy().into_owned(),
            )]),
        }),
    );
    assert_eq!(replaced.summary.success, 1);
    assert_event_contract(&replace_events, "replace-cover");
    let replaced_path = directory.path().join("book_replace_cover.epub");
    assert_eq!(
        archive_bytes(&replaced_path, "OPS/Images/cover.png"),
        cover_data
    );
    let replaced_opf = archive_text(&replaced_path, "OPS/package.opf");
    assert!(replaced_opf.contains("cover-image"));
    assert!(replaced_opf.contains("Images/cover.png"));
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

fn archive_bytes(path: &Path, member: &str) -> Vec<u8> {
    let mut archive = ZipArchive::new(fs::File::open(path).unwrap()).unwrap();
    let mut bytes = Vec::new();
    archive
        .by_name(member)
        .unwrap()
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
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
            r#"<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">fixture</dc:identifier><dc:title>Fixture</dc:title><dc:language>zh</dc:language></metadata><manifest><item id="chapter" href="Text/chapter.xhtml" media-type="application/xhtml+xml"/><item id="style" href="Styles/book.css" media-type="text/css"/><item id="image" href="Images/sample.png" media-type="image/png"/><item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="toc"><itemref idref="chapter"/></spine></package>"#,
        ),
        (
            "OPS/Text/chapter.xhtml",
            r#"<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>汉语</title><link rel="stylesheet" href="../Styles/book.css"/></head><body><p class="body">汉语发展</p><img src="../Images/sample.png" alt="fixture"/></body></html>"#,
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
    archive
        .start_file::<_, ()>(
            "OPS/Images/sample.png",
            SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
        )
        .unwrap();
    archive.write_all(&fixture_png(32, 32)).unwrap();
    archive.finish().unwrap();
}

fn fixture_png(width: u32, height: u32) -> Vec<u8> {
    let image = RgbImage::from_fn(width, height, |x, y| {
        Rgb([
            ((x * 17 + y * 3) % 255) as u8,
            ((x * 5 + y * 19) % 255) as u8,
            ((x * 11 + y * 7) % 255) as u8,
        ])
    });
    let mut output = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(image)
        .write_to(&mut output, ImageFormat::Png)
        .unwrap();
    output.into_inner()
}
