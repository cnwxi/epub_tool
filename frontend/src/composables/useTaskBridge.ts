import { Channel, invoke } from "@tauri-apps/api/core";
import { shallowRef } from "vue";

import type {
  EngineEvent,
  EngineResponse,
  ImagePreviewResponse,
  PlatformCapabilities,
  EngineStatus,
  TaskRequest,
} from "../types";

const isTauriRuntime = (): boolean =>
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);

export function useTaskBridge() {
  const platformCapabilities = shallowRef<PlatformCapabilities>({
    platform: "unknown",
    runtime: "browser",
    supportsDirectoryPicker: false,
    supportsDirectoryScan: false,
    supportsOpenPath: false,
    requiresOutputExport: false,
    supportsFileAssociations: false,
    supportsFontOcr: false,
  });

  const refreshPlatformCapabilities = async (): Promise<PlatformCapabilities> => {
    if (isTauriRuntime()) {
      platformCapabilities.value = await invoke<PlatformCapabilities>(
        "get_platform_capabilities",
      );
    }
    return platformCapabilities.value;
  };

  const isMobileRuntime = (): boolean =>
    platformCapabilities.value.platform === "android" ||
    platformCapabilities.value.platform === "ios";

  const runTask = async (
    request: TaskRequest,
    onEvent: (event: EngineEvent) => void,
  ): Promise<EngineResponse> => {
    if (!isTauriRuntime()) {
      throw new Error("当前环境不支持该功能，请在桌面应用中使用。");
    }

    const channel = new Channel<EngineEvent>((event) => {
      onEvent(event);
    });

    return invoke<EngineResponse>("run_epub_task", {
      request,
      onEvent: channel,
    });
  };

  const listFontTargetsBatch = async (
    filePaths: string[],
    onEvent: (event: EngineEvent) => void,
  ): Promise<EngineResponse> => {
    if (!isTauriRuntime()) {
      throw new Error("当前环境不支持该功能，请在桌面应用中使用。");
    }
    const channel = new Channel<EngineEvent>((event) => {
      onEvent(event);
    });
    return invoke<EngineResponse>("list_font_targets_batch", {
      request: {
        protocolVersion: "PROTOCOL_VERSION_V1",
        requestId: crypto.randomUUID(),
        scanFonts: { inputFiles: filePaths },
      },
      onEvent: channel,
    });
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

  const getPersistedStorePath = async (): Promise<string> => {
    if (!isTauriRuntime()) {
      return "";
    }
    return invoke<string>("get_persisted_store_path");
  };

  const takeOpenedSources = async (): Promise<string[]> => {
    if (!isTauriRuntime()) {
      return [];
    }
    return invoke<string[]>("take_opened_sources");
  };

  const getEngineStatus = async (): Promise<EngineStatus | null> => {
    if (!isTauriRuntime()) {
      return null;
    }
    return invoke<EngineStatus>("get_engine_status");
  };

  const loadPersistedState = async <T>(
    key: string,
  ): Promise<{ found: boolean; value: T | null }> => {
    if (!isTauriRuntime()) {
      return { found: false, value: null };
    }
    return invoke<{ found: boolean; value: T | null }>("load_persisted_state", {
      key,
    });
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

  const validateOutputDirectory = async (directoryPath: string): Promise<void> => {
    if (!isTauriRuntime()) {
      return;
    }
    await invoke("validate_output_directory", { directoryPath });
  };

  const openPath = async (path: string): Promise<void> => {
    if (!isTauriRuntime()) {
      return;
    }
    await invoke("open_path", { path });
  };

  const readImagePreview = async (path: string): Promise<ImagePreviewResponse> => {
    if (!isTauriRuntime()) {
      throw new Error("当前环境不支持本地图片预览。");
    }
    return invoke<ImagePreviewResponse>("read_image_preview", { path });
  };

  const savePersistedState = async (key: string, value: unknown): Promise<void> => {
    if (!isTauriRuntime()) {
      return;
    }
    await invoke("save_persisted_state", { key, value });
  };

  return {
    collectEpubFiles,
    getLogPath,
    takeOpenedSources,
    getPersistedStorePath,
    getEngineStatus,
    isMobileRuntime,
    isTauriRuntime,
    listFontTargetsBatch,
    loadPersistedState,
    openPath,
    readImagePreview,
    refreshPlatformCapabilities,
    resolveInputSources,
    runTask,
    savePersistedState,
    stageSourceForTask,
    exportOutput,
    platformCapabilities,
    validateOutputDirectory,
  };
}
