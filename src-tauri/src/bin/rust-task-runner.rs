use epub_tool_newui::{rust_backend, FrontendTaskRequest};
use rand::{rngs::StdRng, SeedableRng};
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
    let mut obfuscate_font_path = None;
    let mut font_text = None;
    let mut rng_seed = None;
    let mut ocr_image_path = None;
    let mut ocr_recognize_image_path = None;
    let mut ocr_image_shape = None;
    let mut ocr_image_mode = None;
    let mut ocr_max_image_width = None;
    let mut ocr_model_path = None;
    let mut ocr_model_dir = None;
    let mut glyph_font_path = None;
    let mut glyph_character = None;
    let mut glyph_output_path = None;
    let mut ocr_tensor_shape = None;
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
            "--obfuscate-font" => obfuscate_font_path = arguments.next(),
            "--font-text" => font_text = arguments.next(),
            "--rng-seed" => rng_seed = arguments.next(),
            "--preprocess-ocr-image" => ocr_image_path = arguments.next(),
            "--recognize-ocr-image" => ocr_recognize_image_path = arguments.next(),
            "--ocr-image-shape" => ocr_image_shape = arguments.next(),
            "--ocr-image-mode" => ocr_image_mode = arguments.next(),
            "--ocr-max-image-width" => ocr_max_image_width = arguments.next(),
            "--infer-ocr-model" => ocr_model_path = arguments.next(),
            "--ocr-model-dir" => ocr_model_dir = arguments.next(),
            "--render-font-glyph" => glyph_font_path = arguments.next(),
            "--glyph" => glyph_character = arguments.next(),
            "--glyph-output" => glyph_output_path = arguments.next(),
            "--ocr-tensor-shape" => ocr_tensor_shape = arguments.next(),
            "--help" | "-h" => {
                println!(
                    "Usage: rust-task-runner --request-json <TaskRequest JSON> [--log-path <path>]\n       rust-task-runner --list-font-targets <book.epub>\n       rust-task-runner --read-font-cmap <font-file>\n       rust-task-runner --rewrite-font-cmap <font-file> --font-output <font-file> --cmap-replacements <JSON object> --remove-cmap-codepoints <JSON array>\n       rust-task-runner --obfuscate-font <font-file> --font-output <font-file> --font-text <text> --rng-seed <u64>\n       rust-task-runner --render-font-glyph <font.ttf> --glyph <character> --glyph-output <glyph.png>\n       rust-task-runner --preprocess-ocr-image <image> --ocr-image-shape <channels,height,width> --ocr-image-mode <RGB|BGR> --ocr-max-image-width <width> [--infer-ocr-model <model.onnx>]\n       rust-task-runner --recognize-ocr-image <image> --ocr-model-dir <model-dir>\n       rust-task-runner --infer-ocr-model <model.onnx> --ocr-tensor-shape <channels,height,width>"
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
            rust_backend::font::font_targets::list_font_targets(&PathBuf::from(&input_file))?;
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
        let cmap = rust_backend::font::font_cmap::unicode_cmap(&data)?;
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
        let rewritten = rust_backend::font::font_cmap::rewrite_unicode_cmap(
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
    if let Some(input_file) = obfuscate_font_path {
        if request_json.is_some() || font_cmap_path.is_some() || rewrite_font_cmap_path.is_some() {
            return Err(
                "--obfuscate-font 不能与 --request-json 或其他字体操作同时使用".to_string(),
            );
        }
        let output_file = rewrite_font_output
            .ok_or_else(|| "--obfuscate-font 需要同时提供 --font-output".to_string())?;
        let text =
            font_text.ok_or_else(|| "--obfuscate-font 需要同时提供 --font-text".to_string())?;
        let seed = rng_seed
            .ok_or_else(|| "--obfuscate-font 需要同时提供 --rng-seed".to_string())?
            .parse::<u64>()
            .map_err(|error| format!("--rng-seed 必须是 u64: {error}"))?;
        let data = std::fs::read(&input_file)
            .map_err(|error| format!("读取字体失败 {input_file}: {error}"))?;
        let mut rng = StdRng::seed_from_u64(seed);
        let result = rust_backend::font::encrypt_font::obfuscate_font_data(&data, &text, &mut rng)?;
        std::fs::write(&output_file, result.data)
            .map_err(|error| format!("写入字体失败 {output_file}: {error}"))?;
        let replacements: Vec<_> = result
            .html_replacements
            .into_iter()
            .map(|(source, entity)| json!({"source": source.to_string(), "entity": entity}))
            .collect();
        println!(
            "{}",
            serde_json::to_string(&json!({
                "ok": true,
                "input_file": input_file,
                "output_file": output_file,
                "obfuscated_text": result.obfuscated_text,
                "passthrough_text": result.passthrough_text,
                "replacements": replacements,
            }))
            .map_err(|error| format!("序列化字体混淆结果失败: {error}"))?
        );
        return Ok(());
    }
    if let Some(font_path) = glyph_font_path {
        let output_path = glyph_output_path
            .ok_or_else(|| "--render-font-glyph 需要同时提供 --glyph-output".to_string())?;
        let glyph = glyph_character
            .ok_or_else(|| "--render-font-glyph 需要同时提供 --glyph".to_string())?;
        let mut characters = glyph.chars();
        let character = characters
            .next()
            .filter(|_| characters.next().is_none())
            .ok_or_else(|| "--glyph 必须恰好为一个 Unicode 字符".to_string())?;
        let font_data = std::fs::read(&font_path)
            .map_err(|error| format!("读取字体失败 {font_path}: {error}"))?;
        let renderer = rust_backend::font::decrypt_font::FontGlyphRenderer::new(&font_data)?;
        let image = renderer.render(character)?;
        image
            .save(&output_path)
            .map_err(|error| format!("写入字形图像失败 {output_path}: {error}"))?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "output_file": output_path,
                "width": image.width(),
                "height": image.height(),
                "period_like": rust_backend::font::decrypt_font::is_period_like_image(&image),
            }))
            .map_err(|error| format!("序列化字形图像结果失败: {error}"))?
        );
        return Ok(());
    }
    if let Some(input_file) = ocr_recognize_image_path {
        if request_json.is_some()
            || font_target_path.is_some()
            || font_cmap_path.is_some()
            || rewrite_font_cmap_path.is_some()
            || obfuscate_font_path.is_some()
            || ocr_image_path.is_some()
            || ocr_model_path.is_some()
        {
            return Err(
                "--recognize-ocr-image 不能与任务、字体操作或原始推理参数同时使用".to_string(),
            );
        }
        let model_dir = ocr_model_dir
            .ok_or_else(|| "--recognize-ocr-image 需要同时提供 --ocr-model-dir".to_string())?;
        let image = image::open(&input_file)
            .map_err(|error| format!("读取 OCR 图像失败 {input_file}: {error}"))?;
        let mut backend = rust_backend::font::decrypt_font::OnnxGlyphOcrBackend::from_model_dir(
            &PathBuf::from(model_dir),
            3200,
        )?;
        let result = backend.recognize_image(&image)?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "text": result.text,
                "confidence": result.confidence,
                "image_shape": backend.config.image_shape,
                "image_mode": backend.config.image_mode,
                "character_count": backend.config.characters.len(),
            }))
            .map_err(|error| format!("序列化 OCR 识别结果失败: {error}"))?
        );
        return Ok(());
    }
    if let Some(input_file) = ocr_image_path {
        if request_json.is_some()
            || font_target_path.is_some()
            || font_cmap_path.is_some()
            || rewrite_font_cmap_path.is_some()
            || obfuscate_font_path.is_some()
        {
            return Err("--preprocess-ocr-image 不能与任务或字体操作同时使用".to_string());
        }
        let shape_text = ocr_image_shape
            .ok_or_else(|| "--preprocess-ocr-image 需要同时提供 --ocr-image-shape".to_string())?;
        let shape_values: Vec<usize> = shape_text
            .split(',')
            .map(str::trim)
            .map(str::parse)
            .collect::<Result<_, _>>()
            .map_err(|error| format!("--ocr-image-shape 必须为 channels,height,width: {error}"))?;
        let [channels, height, width]: [usize; 3] = shape_values
            .try_into()
            .map_err(|_| "--ocr-image-shape 必须恰好包含 3 个整数".to_string())?;
        let image_mode = ocr_image_mode
            .ok_or_else(|| "--preprocess-ocr-image 需要同时提供 --ocr-image-mode".to_string())?;
        let max_image_width = ocr_max_image_width
            .ok_or_else(|| "--preprocess-ocr-image 需要同时提供 --ocr-max-image-width".to_string())?
            .parse::<usize>()
            .map_err(|error| format!("--ocr-max-image-width 必须是整数: {error}"))?;
        let image = image::open(&input_file)
            .map_err(|error| format!("读取 OCR 图像失败 {input_file}: {error}"))?;
        let tensor = rust_backend::font::decrypt_font::preprocess_ocr_image(
            &image,
            [channels, height, width],
            &image_mode,
            max_image_width,
        )?;
        if let Some(model_path) = ocr_model_path {
            let prediction = rust_backend::font::decrypt_font::infer_onnx_ctc(
                &PathBuf::from(model_path),
                &tensor,
            )?;
            println!(
                "{}",
                serde_json::to_string(&json!({
                    "shape": prediction.shape,
                    "token_ids": prediction.token_ids,
                    "scores": prediction.scores,
                }))
                .map_err(|error| format!("序列化 ONNX OCR 输出失败: {error}"))?
            );
            return Ok(());
        }
        println!(
            "{}",
            serde_json::to_string(&json!({
                "shape": [tensor.channels, tensor.height, tensor.width],
                "data": tensor.data,
            }))
            .map_err(|error| format!("序列化 OCR 预处理结果失败: {error}"))?
        );
        return Ok(());
    }
    if let Some(model_path) = ocr_model_path {
        if request_json.is_some() {
            return Err("--infer-ocr-model 不能与 --request-json 同时使用".to_string());
        }
        let shape_text = ocr_tensor_shape
            .ok_or_else(|| "--infer-ocr-model 需要同时提供 --ocr-tensor-shape".to_string())?;
        let shape_values: Vec<usize> = shape_text
            .split(',')
            .map(str::trim)
            .map(str::parse)
            .collect::<Result<_, _>>()
            .map_err(|error| format!("--ocr-tensor-shape 必须为 channels,height,width: {error}"))?;
        let [channels, height, width]: [usize; 3] = shape_values
            .try_into()
            .map_err(|_| "--ocr-tensor-shape 必须恰好包含 3 个整数".to_string())?;
        let tensor = rust_backend::font::decrypt_font::OcrImageTensor {
            data: vec![0.0; channels * height * width],
            channels,
            height,
            width,
        };
        let prediction =
            rust_backend::font::decrypt_font::infer_onnx_ctc(&PathBuf::from(model_path), &tensor)?;
        println!(
            "{}",
            serde_json::to_string(&json!({
                "shape": prediction.shape,
                "token_ids": prediction.token_ids,
                "scores": prediction.scores,
            }))
            .map_err(|error| format!("序列化 ONNX OCR 输出失败: {error}"))?
        );
        return Ok(());
    }
    let request_json = request_json.ok_or_else(|| "缺少 --request-json".to_string())?;
    let request: FrontendTaskRequest = serde_json::from_str(&request_json)
        .map_err(|error| format!("TaskRequest JSON 无效: {error}"))?;
    if !rust_backend::supports(&request) {
        return Err(format!(
            "Rust 后端暂不支持此任务或选项: {}",
            request.taskType
        ));
    }
    let log_path = log_path.map(PathBuf::from).unwrap_or_else(|| {
        request
            .outputDir
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
