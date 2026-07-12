export type TaskType =
  | "reformat"
  | "decrypt"
  | "encrypt"
  | "font_encrypt"
  | "font_decrypt"
  | "transfer_img";
export type SectionKey = TaskType | "settings" | "about";
export type FontLoadStatus = "idle" | "loading" | "loaded" | "error";
export type OcrCharPolicy = "strict" | "compatible";
export type PythonWorkerState =
  | "stopped"
  | "starting"
  | "ready"
  | "busy"
  | "recovering"
  | "unavailable";
export type TaskOutputDirectoryMap = Record<TaskType, string>;

export interface QueuedFile {
  path: string;
  name: string;
  fontFamilies: string[];
  selectedFontFamilies: string[];
  fontLoadStatus: FontLoadStatus;
  fontLoadError: string;
}

export interface FontTargetResult {
  ok: boolean;
  input_file: string;
  font_families: string[];
  error?: string | null;
}

export interface FontTargetProgressEvent {
  event: "font-targets.progress";
  current_index: number;
  total_files: number;
  result: FontTargetResult;
}

export interface TaskRequest {
  taskId: string;
  taskType: TaskType;
  inputFiles: string[];
  outputDir?: string | null;
  options?: Record<string, unknown>;
}

export interface TaskResult {
  ok: boolean;
  status: string;
  outputs: string[];
  errors: Array<{ input_file: string; message: string }>;
  skipped: Array<{ input_file: string; message: string }>;
  summary: {
    total: number;
    success: number;
    failed: number;
    skipped: number;
  };
  log_path?: string | null;
}

export interface TaskEvent {
  event: string;
  task_id: string;
  status: string;
  progress: number;
  message: string;
  current_file?: string | null;
  current_index?: number | null;
  total_files?: number | null;
  output_path?: string | null;
  level?: string;
  result?: TaskResult;
}

export interface AppSettings {
  autoOpenOutputFolder: boolean;
  autoOpenLogFile: boolean;
  autoCheckUpdates: boolean;
  keepHistoryCount: number;
  pythonWorkerAutoRestartLimit: number;
}

export interface PythonWorkerStatus {
  state: PythonWorkerState;
  message: string;
  lastError?: string | null;
  pid?: number | null;
  recoveryAttempts: number;
  autoRestartLimit: number;
}

export interface FontDecryptSettings {
  ocrCharPolicy: OcrCharPolicy;
  minOcrConfidence: number;
}

export interface TaskAggregateStats {
  total: number;
  success: number;
  failed: number;
  skipped: number;
}

export interface UpdateCheckState {
  checkedAt: string;
  latestVersion: string;
  latestReleaseUrl: string;
  status: "idle" | "checking" | "available" | "latest" | "error";
  message: string;
}

export interface TaskHistoryEntry {
  id: string;
  createdAt: string;
  taskType: TaskType;
  status: string;
  summary: TaskResult["summary"];
  firstOutput?: string | null;
}
