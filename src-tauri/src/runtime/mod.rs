mod engine;
mod files;
mod paths;
mod resources;

use std::sync::Arc;

use tauri::AppHandle;

pub use engine::{EngineRuntime, ExecutionRequest};
pub use files::PlatformFiles;
pub use paths::resolve_log_path;
pub use resources::RuntimeResources;

pub struct RuntimeServices {
    engine: Arc<dyn EngineRuntime>,
    files: Arc<dyn PlatformFiles>,
    _resources: RuntimeResources,
}

impl RuntimeServices {
    pub fn new(app: &AppHandle) -> Result<Self, String> {
        let resources = resources::prepare(app)?;
        Ok(Self {
            engine: engine::create(),
            files: files::create(app.clone()),
            _resources: resources,
        })
    }

    pub fn engine(&self) -> Arc<dyn EngineRuntime> {
        Arc::clone(&self.engine)
    }

    pub fn files(&self) -> Arc<dyn PlatformFiles> {
        Arc::clone(&self.files)
    }
}
