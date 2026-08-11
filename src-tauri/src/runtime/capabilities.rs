use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PlatformCapabilities {
    pub platform: &'static str,
    pub runtime: &'static str,
    pub supports_directory_picker: bool,
    pub supports_directory_scan: bool,
    pub supports_open_path: bool,
    pub requires_output_export: bool,
    pub supports_file_associations: bool,
    pub supports_font_ocr: bool,
}

impl PlatformCapabilities {
    pub fn current() -> Self {
        let mobile = cfg!(any(target_os = "android", target_os = "ios"));
        Self {
            platform: current_platform(),
            runtime: "inProcess",
            supports_directory_picker: !mobile,
            supports_directory_scan: !mobile,
            supports_open_path: !mobile,
            requires_output_export: mobile,
            supports_file_associations: true,
            supports_font_ocr: true,
        }
    }
}

fn current_platform() -> &'static str {
    if cfg!(target_os = "android") {
        "android"
    } else if cfg!(target_os = "ios") {
        "ios"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else {
        "unknown"
    }
}

#[cfg(test)]
mod tests {
    use super::PlatformCapabilities;

    #[test]
    #[cfg(not(mobile))]
    fn desktop_capabilities_use_in_process_runtime() {
        let capabilities = PlatformCapabilities::current();
        assert_eq!(capabilities.runtime, "inProcess");
        assert!(capabilities.supports_directory_picker);
        assert!(capabilities.supports_directory_scan);
        assert!(capabilities.supports_open_path);
        assert!(!capabilities.requires_output_export);
        assert!(capabilities.supports_font_ocr);
    }
}
