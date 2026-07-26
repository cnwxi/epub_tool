//! Generated engine wire types. They are deliberately separate from Tauri
//! shell commands: only engine operations use this module.

pub mod v1 {
    include!(concat!(env!("OUT_DIR"), "/epub_tool.v1.rs"));
    include!(concat!(env!("OUT_DIR"), "/epub_tool.v1.serde.rs"));
}
