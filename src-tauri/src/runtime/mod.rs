mod capabilities;
mod engine;
mod files;
mod paths;
mod resources;

use std::sync::Arc;

use tauri::AppHandle;

pub use capabilities::PlatformCapabilities;
pub use engine::{EngineRuntime, EngineStatus, ExecutionRequest};
pub use files::PlatformFiles;
pub use paths::{resolve_log_path, workspace_root};
pub use resources::RuntimeResources;

pub struct RuntimeServices {
    engine: Arc<dyn EngineRuntime>,
    files: Arc<dyn PlatformFiles>,
    _resources: RuntimeResources,
    pub capabilities: PlatformCapabilities,
}

impl RuntimeServices {
    pub fn new(app: &AppHandle) -> Result<Self, String> {
        let resources = resources::prepare(app)?;
        Ok(Self {
            engine: engine::create(resources.clone()),
            files: files::create(app.clone()),
            _resources: resources,
            capabilities: PlatformCapabilities::current(),
        })
    }

    pub fn engine(&self) -> Arc<dyn EngineRuntime> {
        Arc::clone(&self.engine)
    }

    pub fn files(&self) -> Arc<dyn PlatformFiles> {
        Arc::clone(&self.files)
    }
}
