import { Channel, invoke } from "@tauri-apps/api/core";

import type {
  EngineEvent,
  EngineResponse,
  TaskRequest,
} from "../types";

const isTauriRuntime = (): boolean =>
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);

export function useTaskBridge() {
  const runTask = async (
    request: TaskRequest,
    onEvent: (event: EngineEvent) => void,
  ): Promise<EngineResponse> => {
    if (!isTauriRuntime()) {
      throw new Error("当前环境不支持该功能，请在移动应用中使用。");
    }

    const channel = new Channel<EngineEvent>((event) => {
      onEvent(event);
    });

    return invoke<EngineResponse>("run_epub_task", {
      request,
      onEvent: channel,
    });
  };

  const takeOpenedSources = async (): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return [];
    }
    return invoke<string[]>("take_opened_sources");
  };

  const resolveInputSources = async (inputPaths: string[]): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return inputPaths;
    }
    return invoke<string[]>("resolve_input_sources", {
      inputPaths,
    });
  };

  const stageSourceForTask = async (
    sourcePath: string,
    extension: string,
  ): Promise<string> => {
    if (!isTauriRuntime()) {
      return sourcePath;
    }
    return invoke<string>("stage_source_for_task", {
      sourcePath,
      extension,
    });
  };

  const exportOutput = async (
    sourcePath: string,
    destinationPath: string,
  ): Promise<void> => {
    if (!isTauriRuntime()) {
      return;
    }
    await invoke("export_output", { sourcePath, destinationPath });
  };

  return {
    takeOpenedSources,
    resolveInputSources,
    runTask,
    stageSourceForTask,
    exportOutput,
  };
}
