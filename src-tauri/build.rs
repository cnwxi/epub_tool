use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rerun-if-env-changed=EPUB_TOOL_DEFAULT_OCR_MODEL_NAME");
    let protoc = protoc_bin_vendored::protoc_bin_path()?;
    // SAFETY: Cargo invokes this build script before compiling this crate, so
    // changing PROTOC cannot race with application code.
    unsafe { std::env::set_var("PROTOC", protoc) };

    println!("cargo:rerun-if-changed=../proto/epub_tool/v1/engine.proto");
    let descriptor_path = PathBuf::from(std::env::var("OUT_DIR")?).join("engine_descriptor.bin");
    let mut config = prost_build::Config::new();
    config.file_descriptor_set_path(&descriptor_path);
    config.compile_protos(&["../proto/epub_tool/v1/engine.proto"], &["../proto"])?;
    pbjson_build::Builder::new()
        .register_descriptors(&std::fs::read(descriptor_path)?)?
        .build(&[".epub_tool.v1"])?;
    tauri_build::build();
    Ok(())
}
