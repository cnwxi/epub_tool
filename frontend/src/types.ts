export type TaskType =
  | "reformat_epub"
  | "webp_to_img"
  | "image_compress"
  | "image_to_webp"
  | "chinese_convert"
  | "replace_cover";

export interface QueuedFile {
  path: string;
  name: string;
  coverPath: string;
  coverPreviewUrl: string;
}

export type TaskRequest = EngineRequestJson;
type EngineError = Required<EngineErrorJson>;

type FileIssue = Required<FileIssueJson>;
type TaskSummary = Required<TaskSummaryJson>;

export type TaskResult = Omit<Required<TaskResultJson>, "errors" | "skipped" | "summary"> & {
  errors: FileIssue[];
  skipped: FileIssue[];
  summary: TaskSummary;
};

export type TaskEvent = Omit<TaskEventJson, "progress" | "result"> & {
  event: string;
  taskId: string;
  status: string;
  progress: number;
  message: string;
  level: string;
  result?: TaskResult;
};

export type EngineEvent = Omit<Required<EngineEventJson>, "taskEvent"> & {
  taskEvent?: TaskEvent;
};

export type EngineResponse = Omit<
  Required<EngineResponseJson>,
  "taskResult" | "error"
> & {
  taskResult?: TaskResult;
  error?: EngineError;
};

import type {
  EngineErrorJson,
  EngineEventJson,
  EngineRequestJson,
  EngineResponseJson,
  FileIssueJson,
  TaskEventJson,
  TaskResultJson,
  TaskSummaryJson,
} from "./generated/epub_tool/v1/engine_pb";
