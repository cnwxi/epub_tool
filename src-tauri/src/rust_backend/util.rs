//! 跨模块共享的小工具。

use regex::Regex;
use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

static REGEX_CACHE: LazyLock<Mutex<HashMap<String, Regex>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// 返回按 `pattern` 缓存的编译后正则。
///
/// 调用方常用 `format!` 拼接模式；重复以相同模式调用时复用已编译结果，
/// 避免在每个处理文件/成员上重复编译正则。`Regex::clone` 只复制内部引用，
/// 代价可忽略。
pub fn cached_regex(pattern: &str) -> Result<Regex, regex::Error> {
    let mut cache = REGEX_CACHE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(regex) = cache.get(pattern) {
        return Ok(regex.clone());
    }
    let regex = Regex::new(pattern)?;
    cache.insert(pattern.to_string(), regex.clone());
    Ok(regex)
}
