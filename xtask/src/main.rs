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
        Some("mobile-build") => invoke_tauri("build", &args[1..]),
        Some("mobile-dev") => invoke_tauri("dev", &args[1..]),
        _ => Err(
            "Usage: mobile-build android [target] [options]; mobile-dev android [target] [options]"
                .to_string(),
        ),
    }
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

fn invoke_tauri(command: &str, args: &[String]) -> Result<(), String> {
    let platform = args
        .first()
        .ok_or_else(|| "必须指定 android 平台".to_string())?;
    if platform != "android" {
        return Err(format!("仅支持 Android，收到: {platform}"));
    }
    if command == "build" {
        ensure_android_project_icon()?;
    }
    let root = root()?;
    let mut child = npm();
    child
        .current_dir(&root)
        .args(["run", "tauri", "--", "android", command]);
    if let Some(target) = args.get(1).filter(|value| !value.starts_with('-')) {
        child.args(["--target", target]);
        child.args(&args[2..]);
    } else {
        child.args(&args[1..]);
    }
    let status = child
        .status()
        .map_err(|error| format!("启动 Android Tauri {command} 失败: {error}"))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| format!("Android Tauri {command} 失败: {status}"))
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
