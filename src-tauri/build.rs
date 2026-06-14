fn main() {
    println!("cargo:rerun-if-env-changed=EPUB_TOOL_DEFAULT_OCR_MODEL_NAME");
    tauri_build::build()
}
