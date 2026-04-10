import { Channel, invoke } from "@tauri-apps/api/core";

import type { TaskEvent, TaskRequest, TaskResult } from "../types";

const isTauriRuntime = (): boolean =>
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);

export function useTaskBridge() {
  const runTask = async (
    request: TaskRequest,
    onEvent: (event: TaskEvent) => void,
  ): Promise<TaskResult> => {
    if (!isTauriRuntime()) {
      throw new Error("当前环境不支持该功能，请在桌面应用中使用。");
    }

    const channel = new Channel<TaskEvent>((event) => {
      onEvent(event);
    });

    return invoke<TaskResult>("run_epub_task", {
      request,
      onEvent: channel,
    });
  };

  const listFontTargets = async (filePath: string): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return [];
    }
    const result = await invoke<{ font_families: string[] }>("list_font_targets", {
      filePath,
    });
    return result.font_families ?? [];
  };

  const collectEpubFiles = async (directoryPath: string): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return [];
    }
    return invoke<string[]>("collect_epub_files", {
      directoryPath,
    });
  };

  const getLogPath = async (): Promise<string> => {
    if (!isTauriRuntime()) {
      return "";
    }
    return invoke<string>("get_log_path");
  };

  const resolveInputSources = async (inputPaths: string[]): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return inputPaths;
    }
    return invoke<string[]>("resolve_input_sources", {
      inputPaths,
    });
  };

  const openPath = async (path: string): Promise<void> => {
    if (!isTauriRuntime()) {
      return;
    }
    await invoke("open_path", { path });
  };

  return {
    collectEpubFiles,
    getLogPath,
    isTauriRuntime,
    listFontTargets,
    openPath,
    resolveInputSources,
    runTask,
  };
}
