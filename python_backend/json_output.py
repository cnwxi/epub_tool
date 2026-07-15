from __future__ import annotations

import json
from typing import Any


def _normalize_string(value: str) -> str:
    """Replace lone UTF-16 surrogates while preserving valid surrogate pairs."""
    return value.encode("utf-16", errors="surrogatepass").decode(
        "utf-16", errors="replace"
    )


def _normalize_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, dict):
        return {
            _normalize_string(key) if isinstance(key, str) else key: _normalize_strings(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_strings(item) for item in value)
    return value


def dumps_json_line(payload: Any) -> str:
    """Serialize a payload that Rust's strict JSON parser can always decode."""
    return json.dumps(_normalize_strings(payload), ensure_ascii=True)
