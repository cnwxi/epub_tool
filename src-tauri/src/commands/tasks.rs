use std::path::Path;

use tauri::{ipc::Channel, AppHandle, State};

use crate::{
    engine_adapter,
    engine_protocol::v1::{
        engine_event, engine_request, engine_response, EngineEvent, EngineRequest, EngineResponse,
        FontScanProgress, FontScanResult, ProtocolVersion,
    },
    runtime::{resolve_log_path, ExecutionRequest, RuntimeServices},
    rust_backend,
};

#[tauri::command]
pub async fn list_font_targets_batch(
    request: EngineRequest,
    on_event: Channel<EngineEvent>,
) -> Result<EngineResponse, String> {
    validate_engine_request(&request)?;
    let request_id = request.request_id.clone();
    let Some(engine_request::Operation::ScanFonts(scan_request)) = request.operation else {
        return Err("字体扫描命令只接受 scanFonts operation".to_string());
    };

    tauri::async_runtime::spawn_blocking(move || -> Result<EngineResponse, String> {
        let total_files = u32::try_from(scan_request.input_files.len())
            .map_err(|_| "字体扫描文件数超出 Protobuf uint32 范围".to_string())?;
        let mut results = Vec::with_capacity(scan_request.input_files.len());
        for (position, input_file) in scan_request.input_files.into_iter().enumerate() {
            let result = engine_adapter::font_target_result(
                input_file.clone(),
                rust_backend::font::font_targets::list_font_targets(Path::new(&input_file)),
            );
            on_event
                .send(EngineEvent {
                    protocol_version: ProtocolVersion::V1 as i32,
                    request_id: request_id.clone(),
                    payload: Some(engine_event::Payload::FontScanProgress(FontScanProgress {
                        current_index: u32::try_from(position + 1)
                            .map_err(|_| "字体扫描索引超出 Protobuf uint32 范围".to_string())?,
                        total_files,
                        result: Some(result.clone()),
                    })),
                })
                .map_err(|error| format!("推送 Rust 字体扫描事件失败: {error}"))?;
            results.push(result);
        }
        Ok(EngineResponse {
            protocol_version: ProtocolVersion::V1 as i32,
            request_id,
            payload: Some(engine_response::Payload::FontScanResult(FontScanResult {
                results,
            })),
        })
    })
    .await
    .map_err(|error| format!("异步字体扫描失败: {error}"))?
}

#[tauri::command]
pub async fn run_epub_task(
    app: AppHandle,
    services: State<'_, RuntimeServices>,
    request: EngineRequest,
    on_event: Channel<EngineEvent>,
) -> Result<EngineResponse, String> {
    validate_engine_request(&request)?;
    let request_id = request.request_id.clone();
    let Some(engine_request::Operation::RunTask(run_request)) = request.operation else {
        return Err("任务命令只接受 runTask operation".to_string());
    };
    let execution = ExecutionRequest {
        task: engine_adapter::task_spec(&run_request)?,
        log_path: resolve_log_path(&app)?,
    };
    let engine = services.engine();
    tauri::async_runtime::spawn_blocking(move || -> Result<EngineResponse, String> {
        let result = engine.execute(execution, &mut |event| {
            let task_event = engine_adapter::task_event(event)?;
            on_event
                .send(EngineEvent {
                    protocol_version: ProtocolVersion::V1 as i32,
                    request_id: request_id.clone(),
                    payload: Some(engine_event::Payload::TaskEvent(task_event)),
                })
                .map_err(|error| format!("推送 Rust 引擎事件失败: {error}"))
        })?;
        Ok(EngineResponse {
            protocol_version: ProtocolVersion::V1 as i32,
            request_id,
            payload: Some(engine_adapter::task_result_response(result)?),
        })
    })
    .await
    .map_err(|error| format!("异步任务失败: {error}"))?
}

fn validate_engine_request(request: &EngineRequest) -> Result<(), String> {
    if request.protocol_version != ProtocolVersion::V1 as i32 {
        return Err("请求使用了不支持的 protocolVersion".to_string());
    }
    if request.request_id.trim().is_empty() {
        return Err("请求缺少 requestId".to_string());
    }
    if request.operation.is_none() {
        return Err("请求缺少 operation".to_string());
    }
    Ok(())
}
