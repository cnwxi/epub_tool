use sha2::{Digest, Sha256};
use std::{
    env,
    fs::{self, File},
    io::{self, BufReader, Read},
    path::{Path, PathBuf},
    process::{Command, ExitCode},
};
use zip::ZipArchive;

const ORT_VERSION: &str = "1.24.3";
const ANDROID_URL: &str = "https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.24.3/onnxruntime-android-1.24.3.aar";
const ANDROID_SHA256: &str = "67397e4a970e75617f765d2015ceaf911917e1d822276cfb5792744e8085cbce";
const IOS_URL: &str = "https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.24.3.zip";
const IOS_SHA256: &str = "b7eedc45932bac758ffd057cac0feb3f682269e47750b159e4c865145cbf0a8e";
const DEFAULT_OCR_MODEL: &str = "PP-OCRv6_small_rec";
const ANDROID_NDK_VERSION: &str = "29.0.13846066";

fn main() -> ExitCode {
    match run(env::args().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run(arguments: Vec<String>) -> Result<(), String> {
    let Some(command) = arguments.first().map(String::as_str) else {
        return Err(usage());
    };
    match command {
        "verify-ocr-model" => verify_ocr_model(arguments.get(1).map(String::as_str)),
        "prepare-mobile-ort" => {
            let platform = arguments.get(1).ok_or_else(usage)?;
            let target = arguments.get(2).map(String::as_str);
            prepare_mobile_ort(platform, target).map(|_| ())
        }
        "desktop-build" => desktop_build(&arguments[1..]),
        "mobile-build" => mobile_build(&arguments[1..]),
        "mobile-dev" => mobile_dev(&arguments[1..]),
        "update-homebrew-cask" => update_homebrew_cask(&arguments[1..]),
        _ => Err(usage()),
    }
}

fn usage() -> String {
    "Usage:\n  cargo run --locked --manifest-path xtask/Cargo.toml -- verify-ocr-model [model-name]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- prepare-mobile-ort android <aarch64|armv7|i686|x86_64>\n  cargo run --locked --manifest-path xtask/Cargo.toml -- prepare-mobile-ort ios\n  cargo run --locked --manifest-path xtask/Cargo.toml -- desktop-build [Tauri build options]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- mobile-build android <target> [Tauri build options]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- mobile-build ios <target> [Tauri build options]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- mobile-dev android <target> [Tauri dev options/device]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- mobile-dev ios <target> [Tauri dev options/device]\n  cargo run --locked --manifest-path xtask/Cargo.toml -- update-homebrew-cask <formula> <version> <arm64-sha256> <x64-sha256> <url>".to_string()
}

fn repo_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "无法定位仓库根目录".to_string())
}

fn verify_ocr_model(model_name: Option<&str>) -> Result<(), String> {
    let model_name = model_name
        .map(str::to_string)
        .or_else(|| env::var("EPUB_TOOL_OCR_MODEL_NAME").ok())
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_OCR_MODEL.to_string());
    if !matches!(
        model_name.as_str(),
        "PP-OCRv6_small_rec" | "PP-OCRv6_medium_rec"
    ) {
        return Err(format!("不支持的 OCR 模型: {model_name}"));
    }
    let model_dir = repo_root()?
        .join("src-tauri/bundle-resources/ocr-models")
        .join(format!("{model_name}_onnx"));
    let mut command = Command::new("cargo");
    command.current_dir(repo_root()?).args([
        "run",
        "--locked",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "--bin",
        "verify-ocr-model",
        "--",
    ]);
    if cfg!(target_os = "macos") {
        command.env("ORT_LIB_PATH", prepare_macos_ort()?);
    }
    let status = command
        .arg(&model_dir)
        .status()
        .map_err(|error| format!("启动 Rust OCR 模型校验失败: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("Rust OCR 模型校验失败: {status}"))
    }
}

fn desktop_build(arguments: &[String]) -> Result<(), String> {
    let mut command = npm_command();
    command
        .current_dir(repo_root()?)
        .args(["run", "tauri", "--", "build"])
        .args(arguments);
    if cfg!(target_os = "macos") {
        command.env("ORT_LIB_PATH", prepare_macos_ort()?);
    }
    let status = command
        .status()
        .map_err(|error| format!("启动桌面 Tauri 构建失败: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("桌面 Tauri 构建失败: {status}"))
    }
}

fn mobile_build(arguments: &[String]) -> Result<(), String> {
    let platform = arguments.first().ok_or_else(usage)?;
    let target = arguments.get(1).ok_or_else(usage)?;
    if platform == "android" {
        ensure_android_project_icon()?;
    }
    // Verify the model with the host runtime before mobile linker variables are set.
    verify_ocr_model(None)?;
    let prepared = prepare_mobile_ort(platform, Some(target))?;
    let mut command = npm_command();
    command
        .current_dir(repo_root()?)
        .args(["run", "tauri", "--", platform, "build", "--target", target]);
    command.args(&arguments[2..]);
    configure_mobile_link_environment(&mut command, platform, &prepared)?;
    let status = command
        .status()
        .map_err(|error| format!("启动 Tauri {platform} 构建失败: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("Tauri {platform} 构建失败: {status}"))
    }
}

fn mobile_dev(arguments: &[String]) -> Result<(), String> {
    let platform = arguments.first().ok_or_else(usage)?;
    let target = arguments.get(1).ok_or_else(usage)?;
    if platform == "android" {
        ensure_android_project_icon()?;
    }
    verify_ocr_model(None)?;
    let prepared = prepare_mobile_ort(platform, Some(target))?;
    let mut command = npm_command();
    command
        .current_dir(repo_root()?)
        .args(["run", "tauri", "--", platform, "dev"])
        .args(&arguments[2..]);
    configure_mobile_link_environment(&mut command, platform, &prepared)?;
    let status = command
        .status()
        .map_err(|error| format!("启动 Tauri {platform} 开发环境失败: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("Tauri {platform} 开发环境失败: {status}"))
    }
}

fn ensure_android_project_icon() -> Result<(), String> {
    let root = repo_root()?;
    let project_dir = root.join("src-tauri/gen/android");
    if !project_dir.join("app/build.gradle.kts").is_file() {
        let status = npm_command()
            .current_dir(&root)
            .args([
                "run",
                "tauri",
                "--",
                "android",
                "init",
                "--ci",
                "--skip-targets-install",
            ])
            .status()
            .map_err(|error| format!("初始化 Android 原生工程失败: {error}"))?;
        if !status.success() {
            return Err(format!("初始化 Android 原生工程失败: {status}"));
        }
    }

    let status = npm_command()
        .current_dir(&root)
        .args([
            "run",
            "tauri",
            "--",
            "icon",
            "assets/img/icon.png",
            "--output",
            "src-tauri/.icon-build",
        ])
        .status()
        .map_err(|error| format!("生成 Android launcher 图标失败: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("生成 Android launcher 图标失败: {status}"))
    }
}

fn configure_mobile_link_environment(
    command: &mut Command,
    platform: &str,
    prepared: &Path,
) -> Result<(), String> {
    match platform {
        "android" => {
            command.env("ORT_LIB_PATH", prepared);
            command.env("ORT_PREFER_DYNAMIC_LINK", "1");
            command.env("DEP_Z_INCLUDE", android_zlib_include()?);
        }
        "ios" => {
            command.env("ORT_IOS_XCFWK_PATH", prepared);
        }
        _ => return Err(format!("不支持的移动平台: {platform}")),
    }
    Ok(())
}

fn android_zlib_include() -> Result<PathBuf, String> {
    let mut ndk_roots = Vec::new();
    for variable in ["ANDROID_NDK_HOME", "ANDROID_NDK_ROOT"] {
        if let Ok(value) = env::var(variable) {
            if !value.trim().is_empty() {
                ndk_roots.push(PathBuf::from(value));
            }
        }
    }
    if let Ok(sdk) = env::var("ANDROID_HOME").or_else(|_| env::var("ANDROID_SDK_ROOT")) {
        ndk_roots.push(PathBuf::from(sdk).join("ndk").join(ANDROID_NDK_VERSION));
    }

    let host = if cfg!(target_os = "windows") {
        "windows-x86_64"
    } else if cfg!(target_os = "macos") {
        "darwin-x86_64"
    } else {
        "linux-x86_64"
    };
    for root in ndk_roots {
        let include = root
            .join("toolchains")
            .join("llvm")
            .join("prebuilt")
            .join(host)
            .join("sysroot")
            .join("usr")
            .join("include");
        if include.join("zlib.h").is_file() {
            return Ok(include);
        }
    }
    Err(format!(
        "无法定位 Android NDK {ANDROID_NDK_VERSION} 的 zlib 头文件，请设置 ANDROID_NDK_HOME 或 ANDROID_HOME"
    ))
}

fn npm_command() -> Command {
    if cfg!(windows) {
        Command::new("npm.cmd")
    } else {
        Command::new("npm")
    }
}

fn update_homebrew_cask(arguments: &[String]) -> Result<(), String> {
    let [formula, version, arm_sha256, intel_sha256, url] = arguments else {
        return Err(usage());
    };
    validate_sha256(arm_sha256)?;
    validate_sha256(intel_sha256)?;
    let path = Path::new(formula);
    let source = fs::read_to_string(path)
        .map_err(|error| format!("读取 Homebrew Cask 失败 {}: {error}", path.display()))?;
    let updated = updated_homebrew_cask(&source, version, arm_sha256, intel_sha256, url)?;
    fs::write(path, updated)
        .map_err(|error| format!("写入 Homebrew Cask 失败 {}: {error}", path.display()))
}

fn validate_sha256(value: &str) -> Result<(), String> {
    if value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        Ok(())
    } else {
        Err(format!("无效 SHA-256: {value}"))
    }
}

fn updated_homebrew_cask(
    source: &str,
    version: &str,
    arm_sha256: &str,
    intel_sha256: &str,
    url: &str,
) -> Result<String, String> {
    let trailing_newline = source.ends_with('\n');
    let mut lines: Vec<String> = source.lines().map(str::to_string).collect();
    let version_index = find_cask_line(&lines, "version ")?;
    lines[version_index] = format!("  version \"{version}\"");

    let arch_line = "  arch arm: \"arm64\", intel: \"x64\"".to_string();
    if let Some(index) = lines
        .iter()
        .position(|line| line.trim_start().starts_with("arch "))
    {
        lines[index] = arch_line;
    } else {
        lines.insert(version_index + 1, arch_line);
    }

    let sha_index = find_cask_line(&lines, "sha256 ")?;
    let url_index = find_cask_line(&lines, "url ")?;
    if url_index <= sha_index {
        return Err("Homebrew Cask 的 sha256 必须位于 url 之前".to_string());
    }
    lines.splice(
        sha_index..url_index,
        [
            format!("  sha256 arm: \"{arm_sha256}\","),
            format!("         intel: \"{intel_sha256}\""),
        ],
    );
    let url_index = find_cask_line(&lines, "url ")?;
    lines[url_index] = format!("  url \"{url}\"");

    let mut output = lines.join("\n");
    if trailing_newline {
        output.push('\n');
    }
    Ok(output)
}

fn find_cask_line(lines: &[String], prefix: &str) -> Result<usize, String> {
    lines
        .iter()
        .position(|line| line.trim_start().starts_with(prefix))
        .ok_or_else(|| format!("Homebrew Cask 缺少 {prefix}字段"))
}

fn prepare_mobile_ort(platform: &str, target: Option<&str>) -> Result<PathBuf, String> {
    match platform {
        "android" => prepare_android_ort(target.ok_or_else(usage)?),
        "ios" => prepare_ios_ort(),
        _ => Err(format!("不支持的移动平台: {platform}")),
    }
}

fn prepare_android_ort(target: &str) -> Result<PathBuf, String> {
    let abi = android_abi(target).ok_or_else(|| format!("不支持的 Android target: {target}"))?;
    let root = repo_root()?;
    let cache = root.join("src-tauri/.mobile-runtime");
    let archive = cache
        .join("archives")
        .join(format!("onnxruntime-android-{ORT_VERSION}.aar"));
    let archive = verified_archive(
        "EPUB_TOOL_ORT_ANDROID_ARCHIVE",
        ANDROID_URL,
        ANDROID_SHA256,
        &archive,
    )?;

    let library_dir = cache
        .join(format!("onnxruntime-android-{ORT_VERSION}"))
        .join(abi);
    let library = library_dir.join("libonnxruntime.so");
    extract_file(&archive, &format!("jni/{abi}/libonnxruntime.so"), &library)?;

    let android_project = root.join("src-tauri/gen/android/app/src/main");
    if android_project.is_dir() {
        let packaged = android_project
            .join("jniLibs")
            .join(abi)
            .join("libonnxruntime.so");
        copy_if_changed(&library, &packaged)?;
    }
    println!(
        "Android ONNX Runtime prepared: target={target} abi={abi} ORT_LIB_PATH={}",
        library_dir.display()
    );
    Ok(library_dir)
}

fn android_abi(target: &str) -> Option<&'static str> {
    match target {
        "aarch64" => Some("arm64-v8a"),
        "armv7" => Some("armeabi-v7a"),
        "i686" => Some("x86"),
        "x86_64" => Some("x86_64"),
        _ => None,
    }
}

fn prepare_ios_ort() -> Result<PathBuf, String> {
    let root = repo_root()?;
    let cache = root.join("src-tauri/.mobile-runtime");
    let archive = cache
        .join("archives")
        .join(format!("onnxruntime-c-{ORT_VERSION}.zip"));
    let archive = verified_archive("EPUB_TOOL_ORT_IOS_ARCHIVE", IOS_URL, IOS_SHA256, &archive)?;
    let destination = cache.join(format!("onnxruntime-c-{ORT_VERSION}"));
    let framework = destination.join("onnxruntime.xcframework");
    for prefix in [
        "onnxruntime.xcframework/Info.plist",
        "onnxruntime.xcframework/macos-arm64_x86_64/",
        "onnxruntime.xcframework/ios-arm64/",
        "onnxruntime.xcframework/ios-arm64_x86_64-simulator/",
    ] {
        extract_prefix(&archive, prefix, &destination)?;
    }
    for slice in ["ios-arm64", "ios-arm64_x86_64-simulator"] {
        let binary = framework
            .join(slice)
            .join("onnxruntime.framework/onnxruntime");
        if !binary.is_file() {
            return Err(format!("iOS ONNX Runtime 切片不完整: {}", binary.display()));
        }
    }
    println!(
        "iOS ONNX Runtime prepared: ORT_IOS_XCFWK_PATH={}",
        framework.display()
    );
    Ok(framework)
}

fn macos_onnx_runtime_library(framework: &Path) -> Result<PathBuf, String> {
    let library = framework
        .join("macos-arm64_x86_64")
        .join("onnxruntime.framework")
        .join("Versions")
        .join("A")
        .join("onnxruntime");
    if library.is_file() {
        Ok(library)
    } else {
        Err(format!(
            "macOS ONNX Runtime 切片不完整: {}",
            library.display()
        ))
    }
}

fn prepare_macos_ort() -> Result<PathBuf, String> {
    let framework = prepare_ios_ort()?;
    let library = macos_onnx_runtime_library(&framework)?;
    let destination = framework
        .parent()
        .ok_or_else(|| {
            format!(
                "macOS ONNX Runtime 框架路径无父目录: {}",
                framework.display()
            )
        })?
        .join("macos-static");
    copy_if_changed(&library, &destination.join("libonnxruntime.a"))?;
    println!(
        "macOS ONNX Runtime prepared: ORT_LIB_PATH={}",
        destination.display()
    );
    Ok(destination)
}

fn download_verified(url: &str, expected_sha256: &str, destination: &Path) -> Result<(), String> {
    if destination.is_file() && sha256(destination)? == expected_sha256 {
        return Ok(());
    }
    let parent = destination
        .parent()
        .ok_or_else(|| format!("归档路径无父目录: {}", destination.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("创建归档目录失败 {}: {error}", parent.display()))?;
    let temporary = destination.with_extension("download");
    let status = Command::new("curl")
        .args(["-fL", "--retry", "3", "--output"])
        .arg(&temporary)
        .arg(url)
        .status()
        .map_err(|error| format!("启动 curl 失败: {error}"))?;
    if !status.success() {
        return Err(format!("下载 ONNX Runtime 失败: {url}"));
    }
    let actual = sha256(&temporary)?;
    if actual != expected_sha256 {
        return Err(format!(
            "ONNX Runtime SHA-256 不匹配: {actual} != {expected_sha256}"
        ));
    }
    fs::rename(&temporary, destination).map_err(|error| {
        format!(
            "保存 ONNX Runtime 归档失败 {}: {error}",
            destination.display()
        )
    })
}

fn verified_archive(
    environment_name: &str,
    url: &str,
    expected_sha256: &str,
    cache_path: &Path,
) -> Result<PathBuf, String> {
    if let Ok(value) = env::var(environment_name) {
        if !value.trim().is_empty() {
            let path = PathBuf::from(value);
            let actual = sha256(&path)?;
            if actual != expected_sha256 {
                return Err(format!(
                    "{environment_name} 指定归档的 SHA-256 不匹配: {actual} != {expected_sha256}"
                ));
            }
            return Ok(path);
        }
    }
    download_verified(url, expected_sha256, cache_path)?;
    Ok(cache_path.to_path_buf())
}

fn sha256(path: &Path) -> Result<String, String> {
    let file =
        File::open(path).map_err(|error| format!("读取文件失败 {}: {error}", path.display()))?;
    let mut reader = BufReader::new(file);
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|error| format!("计算 SHA-256 失败 {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

fn extract_file(archive: &Path, entry_name: &str, destination: &Path) -> Result<(), String> {
    if destination.is_file() {
        return Ok(());
    }
    let file = File::open(archive)
        .map_err(|error| format!("打开归档失败 {}: {error}", archive.display()))?;
    let mut zip = ZipArchive::new(file)
        .map_err(|error| format!("读取归档失败 {}: {error}", archive.display()))?;
    let mut entry = zip
        .by_name(entry_name)
        .map_err(|error| format!("归档缺少 {entry_name}: {error}"))?;
    write_zip_entry(&mut entry, destination)
}

fn extract_prefix(archive: &Path, prefix: &str, destination: &Path) -> Result<(), String> {
    let file = File::open(archive)
        .map_err(|error| format!("打开归档失败 {}: {error}", archive.display()))?;
    let mut zip = ZipArchive::new(file)
        .map_err(|error| format!("读取归档失败 {}: {error}", archive.display()))?;
    for index in 0..zip.len() {
        let mut entry = zip
            .by_index(index)
            .map_err(|error| format!("读取归档条目失败: {error}"))?;
        if !entry.name().starts_with(prefix) {
            continue;
        }
        let relative = entry
            .enclosed_name()
            .ok_or_else(|| format!("归档包含不安全路径: {}", entry.name()))?;
        let output = destination.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&output)
                .map_err(|error| format!("创建目录失败 {}: {error}", output.display()))?;
        } else {
            write_zip_entry(&mut entry, &output)?;
        }
    }
    Ok(())
}

fn write_zip_entry(entry: &mut zip::read::ZipFile<'_>, destination: &Path) -> Result<(), String> {
    let parent = destination
        .parent()
        .ok_or_else(|| format!("解压路径无父目录: {}", destination.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("创建解压目录失败 {}: {error}", parent.display()))?;
    #[cfg(unix)]
    if entry.is_symlink() {
        use std::os::unix::fs::symlink;

        let mut target = String::new();
        entry
            .read_to_string(&mut target)
            .map_err(|error| format!("读取符号链接失败 {}: {error}", destination.display()))?;
        if destination.exists() || destination.is_symlink() {
            fs::remove_file(destination).map_err(|error| {
                format!("移除旧符号链接失败 {}: {error}", destination.display())
            })?;
        }
        symlink(target, destination)
            .map_err(|error| format!("创建符号链接失败 {}: {error}", destination.display()))?;
        return Ok(());
    }
    let mut output = File::create(destination)
        .map_err(|error| format!("创建解压文件失败 {}: {error}", destination.display()))?;
    io::copy(entry, &mut output)
        .map_err(|error| format!("解压文件失败 {}: {error}", destination.display()))?;
    #[cfg(unix)]
    if let Some(mode) = entry.unix_mode() {
        use std::os::unix::fs::PermissionsExt;

        fs::set_permissions(destination, fs::Permissions::from_mode(mode))
            .map_err(|error| format!("设置解压文件权限失败 {}: {error}", destination.display()))?;
    }
    Ok(())
}

fn copy_if_changed(source: &Path, destination: &Path) -> Result<(), String> {
    if destination.is_file()
        && fs::metadata(source).ok().map(|value| value.len())
            == fs::metadata(destination).ok().map(|value| value.len())
    {
        return Ok(());
    }
    let parent = destination
        .parent()
        .ok_or_else(|| format!("目标路径无父目录: {}", destination.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("创建目标目录失败 {}: {error}", parent.display()))?;
    fs::copy(source, destination)
        .map_err(|error| format!("复制原生库失败 {}: {error}", destination.display()))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{android_abi, updated_homebrew_cask};

    #[test]
    fn maps_every_tauri_android_target_to_its_aar_abi() {
        assert_eq!(android_abi("aarch64"), Some("arm64-v8a"));
        assert_eq!(android_abi("armv7"), Some("armeabi-v7a"));
        assert_eq!(android_abi("i686"), Some("x86"));
        assert_eq!(android_abi("x86_64"), Some("x86_64"));
        assert_eq!(android_abi("unknown"), None);
    }

    #[test]
    fn updates_homebrew_cask_without_external_script_runtime() {
        let source = r#"cask \"epub-tool-newui\" do
  version \"1.0.0\"
  sha256 arm: \"old-arm\",
         intel: \"old-intel\"
  url \"https://old.invalid\"
end
"#;
        let arm = "a".repeat(64);
        let intel = "b".repeat(64);
        let updated = updated_homebrew_cask(
            source,
            "2.0.0",
            &arm,
            &intel,
            "https://example.invalid/#{version}/#{arch}.dmg",
        )
        .unwrap();
        assert!(updated.contains("  version \"2.0.0\""));
        assert!(updated.contains("  arch arm: \"arm64\", intel: \"x64\""));
        assert!(updated.contains(&format!("  sha256 arm: \"{arm}\",")));
        assert!(updated.contains(&format!("         intel: \"{intel}\"")));
        assert!(updated.contains("  url \"https://example.invalid/#{version}/#{arch}.dmg\""));
    }
}
