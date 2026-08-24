use tauri::{ipc::Channel, AppHandle, State};

use crate::{
    engine_adapter,
    engine_protocol::v1::{
        engine_event, engine_request, EngineEvent, EngineRequest, EngineResponse, ProtocolVersion,
    },
    runtime::{resolve_log_path, ExecutionRequest, RuntimeServices},
};

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
