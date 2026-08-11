use std::{env, path::Path, process::ExitCode};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let mut arguments = env::args_os().skip(1);
    let model_dir = arguments
        .next()
        .ok_or_else(|| "Usage: verify-ocr-model <model-dir>".to_string())?;
    if arguments.next().is_some() {
        return Err("Usage: verify-ocr-model <model-dir>".to_string());
    }
    epub_tool_newui::rust_backend::font::decrypt_font::verify_ocr_model_dir(Path::new(&model_dir))
        .map(|_| ())
}
