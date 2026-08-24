use std::{
    env, fs,
    path::{Path, PathBuf},
    process::{Command, ExitCode},
};

fn main() -> ExitCode {
    match run(env::args().skip(1).collect()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

fn run(args: Vec<String>) -> Result<(), String> {
    match args.first().map(String::as_str) {
        Some("desktop-build") => build("desktop", &args[1..]),
        Some("mobile-build") => build(
            args.get(1).map(String::as_str).ok_or_else(usage)?,
            &args[2..],
        ),
        Some("mobile-dev") => dev(
            args.get(1).map(String::as_str).ok_or_else(usage)?,
            &args[2..],
        ),
        Some("update-homebrew-cask") => update_homebrew_cask(&args[1..]),
        _ => Err(usage()),
    }
}

fn usage() -> String {
    "Usage: desktop-build [options]; mobile-build <android|ios> [target] [options]; mobile-dev <android|ios> [target] [options]; update-homebrew-cask <formula> <version> <arm-sha256> <x64-sha256> <url>".to_string()
}
fn root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "无法定位仓库根目录".to_string())
}
fn npm() -> Command {
    if cfg!(windows) {
        Command::new("npm.cmd")
    } else {
        Command::new("npm")
    }
}
fn build(platform: &str, args: &[String]) -> Result<(), String> {
    if platform == "android" {
        ensure_android_project_icon()?;
    }
    let mut command = npm();
    command.current_dir(root()?).args(["run", "tauri", "--"]);
    if platform == "desktop" {
        command.arg("build");
    } else {
        command.args([platform, "build"]);
    }
    let status = command
        .args(mobile_target_args(platform, args))
        .status()
        .map_err(|e| format!("启动 Tauri 构建失败: {e}"))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| format!("Tauri 构建失败: {status}"))
}
fn dev(platform: &str, args: &[String]) -> Result<(), String> {
    if platform == "android" {
        ensure_android_project_icon()?;
    }
    let mut command = npm();
    command
        .current_dir(root()?)
        .args(["run", "tauri", "--", platform, "dev"])
        .args(mobile_target_args(platform, args));
    let status = command
        .status()
        .map_err(|e| format!("启动 Tauri 开发环境失败: {e}"))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| format!("Tauri 开发环境失败: {status}"))
}

fn mobile_target_args(platform: &str, args: &[String]) -> Vec<String> {
    if platform == "desktop" || args.first().is_none_or(|value| value.starts_with('-')) {
        return args.to_vec();
    }

    let mut command_args = Vec::with_capacity(args.len() + 1);
    command_args.push("--target".to_string());
    command_args.push(args[0].clone());
    command_args.extend_from_slice(&args[1..]);
    command_args
}

fn ensure_android_project_icon() -> Result<(), String> {
    let root = root()?;
    let project_dir = root.join("src-tauri/gen/android");
    if !project_dir.join("app/build.gradle.kts").is_file() {
        let status = npm()
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

    let icon_output = root.join("src-tauri/.icon-build");
    let icon_output_arg = icon_output.to_string_lossy().into_owned();
    let status = npm()
        .current_dir(&root)
        .args([
            "run",
            "tauri",
            "--",
            "icon",
            "assets/img/icon.png",
            "--output",
        ])
        .arg(&icon_output_arg)
        .status()
        .map_err(|error| format!("生成 Android launcher 图标失败: {error}"))?;
    if !status.success() {
        return Err(format!("生成 Android launcher 图标失败: {status}"));
    }

    let destination = project_dir.join("app/src/main/res");
    let source = icon_output.join("android");
    if source.is_dir() {
        copy_directory_contents(&source, &destination)
            .map_err(|error| format!("同步 Android launcher 图标失败: {error}"))?;
    }

    // Tauri writes directly to the generated Android project when it already exists.
    // When that happens there is no separate output/android directory to copy.
    for file in [
        "mipmap-xxxhdpi/ic_launcher.png",
        "mipmap-xxxhdpi/ic_launcher_foreground.png",
        "mipmap-anydpi-v26/ic_launcher.xml",
    ] {
        if !destination.join(file).is_file() {
            return Err(format!(
                "Android launcher 图标生成后缺少文件: {}",
                destination.join(file).display()
            ));
        }
    }
    Ok(())
}

fn copy_directory_contents(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let source_path = entry.path();
        let destination_path = destination.join(entry.file_name());
        if source_path.is_dir() {
            copy_directory_contents(&source_path, &destination_path)?;
        } else {
            fs::copy(source_path, destination_path)?;
        }
    }
    Ok(())
}

fn update_homebrew_cask(args: &[String]) -> Result<(), String> {
    let [formula, version, arm, intel, url] = args else {
        return Err(usage());
    };
    let path = Path::new(formula);
    let source = fs::read_to_string(path).map_err(|e| format!("读取 Homebrew Cask 失败: {e}"))?;
    let mut lines: Vec<String> = source.lines().map(str::to_string).collect();
    let find = |prefix: &str, lines: &[String]| {
        lines
            .iter()
            .position(|line| line.trim_start().starts_with(prefix))
            .ok_or_else(|| format!("Cask 缺少 {prefix} 字段"))
    };
    let version_index = find("version ", &lines)?;
    lines[version_index] = format!("  version \"{version}\"");
    let sha_index = find("sha256 ", &lines)?;
    let url_index = find("url ", &lines)?;
    lines.splice(
        sha_index..url_index,
        [
            format!("  sha256 arm: \"{arm}\","),
            format!("         intel: \"{intel}\""),
        ],
    );
    let url_index = find("url ", &lines)?;
    lines[url_index] = format!("  url \"{url}\"");
    fs::write(
        path,
        lines.join("\n") + if source.ends_with('\n') { "\n" } else { "" },
    )
    .map_err(|e| format!("写入 Homebrew Cask 失败: {e}"))
}
