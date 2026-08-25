use crate::{
    commands::{self, OpenedSources},
    runtime::RuntimeServices,
};
use tauri::Manager;
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .setup(|app| {
            app.manage(RuntimeServices::new(app.handle())?);
            app.manage(OpenedSources::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::files::export_output,
            commands::files::resolve_input_sources,
            commands::tasks::run_epub_task,
            commands::files::stage_source_for_task,
            commands::files::take_opened_sources,
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|_app_handle, _event| {
        #[cfg(mobile)]
        if let tauri::RunEvent::Opened { urls } = _event {
            use tauri::Emitter;

            let opened_sources = urls
                .into_iter()
                .map(|url| url.to_string())
                .collect::<Vec<_>>();
            _app_handle
                .state::<OpenedSources>()
                .extend(opened_sources.iter().cloned());
            let _ = _app_handle.emit("opened", opened_sources);
        }
    });
}
