export type TaskType =
  | "reformat"
  | "decrypt"
  | "encrypt"
  | "font_encrypt"
  | "transfer_img";
export type SectionKey = TaskType | "settings" | "about";
export type FontLoadStatus = "idle" | "loading" | "loaded" | "error";
export type TaskOutputDirectoryMap = Record<TaskType, string>;

export interface QueuedFile {
  path: string;
  name: string;
  fontFamilies: string[];
  selectedFontFamilies: string[];
  fontLoadStatus: FontLoadStatus;
  fontLoadError: string;
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
