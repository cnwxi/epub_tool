import type { EngineErrorJson, EngineEventJson, EngineRequestJson, EngineResponseJson, FileIssueJson, TaskEventJson, TaskResultJson, TaskSummaryJson } from "./generated/epub_tool/v1/engine_pb";

export type TaskType = "reformat_epub" | "decrypt_epub" | "encrypt_epub" | "webp_to_img" | "image_compress" | "image_to_webp" | "chinese_convert" | "replace_cover";
export type SectionKey = TaskType | "overview" | "engine" | "settings" | "about";
export type EngineState = "starting" | "ready" | "busy" | "unavailable";
export type TaskOutputDirectoryMap = Record<TaskType, string>;
export interface QueuedFile { path: string; name: string; coverPath: string; coverPreviewUrl: string; }
export interface ImagePreviewResponse { bytes: number[]; mimeType: string; }
export interface NewTaskSettings { jpegQuality: number; webpQuality: number; pngToJpg: boolean; imageCompressQuantizePng: boolean; webpToImageQuality: number; webpToImageQuantizePng: boolean; imageWebpQuality: number; chineseDirection: "s2t" | "t2s"; }
export type TaskRequest = EngineRequestJson;
type EngineError = Required<EngineErrorJson>; type FileIssue = Required<FileIssueJson>; type TaskSummary = Required<TaskSummaryJson>;
export type TaskResult = Omit<Required<TaskResultJson>, "errors" | "skipped" | "summary"> & { errors: FileIssue[]; skipped: FileIssue[]; summary: TaskSummary; };
export type TaskEvent = Omit<TaskEventJson, "progress" | "result"> & { event: string; taskId: string; status: string; progress: number; message: string; level: string; result?: TaskResult; };
export type EngineEvent = Omit<Required<EngineEventJson>, "taskEvent"> & { taskEvent?: TaskEvent; };
export type EngineResponse = Omit<Required<EngineResponseJson>, "taskResult" | "error"> & { taskResult?: TaskResult; error?: EngineError; };
export interface AppSettings { autoOpenOutputFolder: boolean; autoOpenLogFile: boolean; autoCheckUpdates: boolean; keepHistoryCount: number; }
export interface EngineStatus { state: EngineState; message: string; lastError?: string | null; }
export interface PlatformCapabilities { platform: "linux" | "macos" | "windows" | "android" | "ios" | "unknown"; runtime: "inProcess" | "browser"; supportsDirectoryPicker: boolean; supportsDirectoryScan: boolean; supportsOpenPath: boolean; requiresOutputExport: boolean; supportsFileAssociations: boolean; }
export interface TaskAggregateStats { total: number; success: number; failed: number; skipped: number; }
export interface UpdateCheckState { checkedAt: string; latestVersion: string; latestReleaseUrl: string; status: "idle" | "checking" | "available" | "latest" | "error"; message: string; }
export interface TaskHistoryEntry { id: string; createdAt: string; taskType: TaskType; status: string; summary: TaskResult["summary"]; firstOutput?: string | null; }
