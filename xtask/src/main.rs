use std::{
    env,
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
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| "无法定位仓库根目录".to_string())?;
    let npm = if cfg!(windows) { "npm.cmd" } else { "npm" };
    let mut child = Command::new(npm);
    child
        .current_dir(root)
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
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or_else(|| "无法定位仓库根目录".to_string())?;
    let project_dir = root.join("src-tauri/gen/android");
    if project_dir.join("app/build.gradle.kts").is_file() {
        return Ok(());
    }
    let npm = if cfg!(windows) { "npm.cmd" } else { "npm" };
    let status = Command::new(npm)
        .current_dir(root)
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
    status
        .success()
        .then_some(())
        .ok_or_else(|| format!("初始化 Android 原生工程失败: {status}"))
}
