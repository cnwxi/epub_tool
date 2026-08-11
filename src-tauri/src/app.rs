use crate::{
    commands::{self, OpenedSources, PersistedStore},
    runtime::RuntimeServices,
};
use tauri::Manager;

#[cfg(target_os = "macos")]
use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};

#[cfg(target_os = "windows")]
use window_vibrancy::{apply_blur, apply_mica};

#[cfg(not(mobile))]
fn setup_window_effects(app: &tauri::App) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "未找到主窗口 main".to_string())?;

    #[cfg(target_os = "macos")]
    {
        apply_vibrancy(&window, NSVisualEffectMaterial::HudWindow, None, None)
            .map_err(|error| format!("应用 macOS 毛玻璃效果失败: {error}"))?;
    }

    #[cfg(target_os = "windows")]
    {
        window
            .set_decorations(true)
            .map_err(|error| format!("恢复 Windows 原生窗口装饰失败: {error}"))?;
        apply_mica(&window, None)
            .or_else(|_| apply_blur(&window, Some((245, 239, 231, 180))))
            .map_err(|error| format!("应用 Windows 毛玻璃效果失败: {error}"))?;
    }

    #[cfg(target_os = "linux")]
    {
        let _ = window;
    }

    Ok(())
}

#[cfg(mobile)]
fn setup_window_effects(_app: &tauri::App) -> Result<(), String> {
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            app.manage(PersistedStore::load(app.handle()));
            app.manage(RuntimeServices::new(app.handle())?);
            app.manage(OpenedSources::new());
            setup_window_effects(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::files::collect_epub_files,
            commands::files::export_output,
            commands::files::get_log_path,
            commands::engine::get_platform_capabilities,
            commands::state::get_persisted_store_path,
            commands::engine::get_engine_status,
            commands::tasks::list_font_targets_batch,
            commands::state::load_persisted_state,
            commands::files::open_path,
            commands::files::read_image_preview,
            commands::files::resolve_input_sources,
            commands::tasks::run_epub_task,
            commands::state::save_persisted_state,
            commands::files::stage_source_for_task,
            commands::files::take_opened_sources,
            commands::files::validate_output_directory,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        #[cfg(mobile)]
        if let tauri::RunEvent::Opened { urls } = event {
            use tauri::Emitter;

            let opened_sources = urls
                .into_iter()
                .map(|url| url.to_string())
                .collect::<Vec<_>>();
            app_handle
                .state::<OpenedSources>()
                .extend(opened_sources.iter().cloned());
            let _ = app_handle.emit("opened", opened_sources);
        }

        #[cfg(not(mobile))]
        let _ = (app_handle, event);
    });
}
