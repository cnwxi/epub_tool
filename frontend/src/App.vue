<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWindow } from "@tauri-apps/api/window";

import DropZone from "./components/DropZone.vue";
import SideNav from "./components/SideNav.vue";
import TaskConsole from "./components/TaskConsole.vue";
import { usePersistentState } from "./composables/usePersistentState";
import { useTaskBridge } from "./composables/useTaskBridge";
import type {
  AppSettings,
  FontLoadStatus,
  QueuedFile,
  SectionKey,
  TaskEvent,
  TaskHistoryEntry,
  TaskRequest,
  TaskResult,
  TaskType,
  UpdateCheckState,
} from "./types";

const sectionItems: Array<{
  key: SectionKey;
  label: string;
  description: string;
}> = [
  { key: "reformat", label: "格式化", description: "单本或批量重构 EPUB 结构" },
  { key: "decrypt", label: "文件解密", description: "单本或批量处理文件名混淆" },
  { key: "encrypt", label: "文件加密", description: "单本或批量生成混淆版 EPUB" },
  { key: "font_encrypt", label: "字体加密", description: "按 EPUB 选择字体范围后批量执行" },
  { key: "transfer_img", label: "图片转换", description: "批量转换 EPUB 内 WEBP 图片" },
  { key: "settings", label: "设置", description: "输出偏好与历史记录" },
  { key: "about", label: "关于", description: "功能说明与使用提示" },
];
const taskSections: TaskType[] = [
  "reformat",
  "decrypt",
  "encrypt",
  "font_encrypt",
  "transfer_img",
];
const legacySectionMap: Record<string, SectionKey> = {
  batch: "reformat",
  font: "font_encrypt",
  image: "transfer_img",
  settings: "settings",
  about: "about",
};

const defaultSettings: AppSettings = {
  autoOpenOutputFolder: false,
  autoOpenLogFile: false,
  autoCheckUpdates: true,
  keepHistoryCount: 10,
};

const CURRENT_FALLBACK_VERSION = __APP_VERSION__;
const RELEASES_API_URL = "https://api.github.com/repos/cnwxi/epub_tool/releases/latest";
const LATEST_RELEASE_URL = "https://github.com/cnwxi/epub_tool/releases/latest";
const UPDATE_CHECK_INTERVAL_MS = 12 * 60 * 60 * 1000;
const defaultUpdateCheckState: UpdateCheckState = {
  checkedAt: "",
  latestVersion: "",
  latestReleaseUrl: LATEST_RELEASE_URL,
  status: "idle",
  message: "可手动检查是否有新版本。",
};

const { collectEpubFiles, isTauriRuntime, listFontTargets, openPath, runTask } =
  useTaskBridge();

const normalizeSectionKey = (value: unknown): SectionKey => {
  if (typeof value !== "string") {
    return "reformat";
  }

  const normalized = legacySectionMap[value] ?? value;
  if (sectionItems.some((item) => item.key === normalized)) {
    return normalized as SectionKey;
  }

  return "reformat";
};

const activeSection = usePersistentState<SectionKey>(
  "epub-tool.active-section",
  "reformat",
);
const outputDir = usePersistentState<string>("epub-tool.output-dir", "");
const settings = usePersistentState<AppSettings>(
  "epub-tool.settings",
  defaultSettings,
);
const taskHistory = usePersistentState<TaskHistoryEntry[]>(
  "epub-tool.task-history",
  [],
);
const persistedUpdateCheckState = usePersistentState<UpdateCheckState>(
  "epub-tool.update-check-state",
  defaultUpdateCheckState,
);
const files = ref<QueuedFile[]>([]);
const selectedFilePath = ref("");
const logs = ref<TaskEvent[]>([]);
const result = ref<TaskResult | null>(null);
const progress = ref(0);
const taskRunning = ref(false);
const taskStatus = ref("待命");
const fontLoading = ref(false);
const taskProgressCurrent = ref(0);
const taskProgressTotal = ref(0);
const taskProgressFileName = ref("");
const fontProgressCurrent = ref(0);
const fontProgressTotal = ref(0);
const fontProgressFileName = ref("");
const currentVersion = ref(CURRENT_FALLBACK_VERSION);
const latestVersion = ref(persistedUpdateCheckState.value.latestVersion);
const latestReleaseUrl = ref(
  persistedUpdateCheckState.value.latestReleaseUrl || LATEST_RELEASE_URL,
);
const updateMessage = ref(
  persistedUpdateCheckState.value.message || defaultUpdateCheckState.message,
);
const updateStatus = ref<"idle" | "checking" | "available" | "latest" | "error">(
  persistedUpdateCheckState.value.status || "idle",
);
const updateCheckedAt = ref(persistedUpdateCheckState.value.checkedAt);
const updateNoticeVisible = ref(false);
const dragActive = ref(false);
const browserFileInput = ref<HTMLInputElement | null>(null);

const normalizeSettings = (value: unknown): AppSettings => {
  const raw =
    value && typeof value === "object"
      ? (value as Partial<AppSettings> & {
          autoOpenFirstOutput?: boolean;
          autoCheckUpdate?: boolean;
        })
      : {};

  return {
    autoOpenOutputFolder:
      typeof raw.autoOpenOutputFolder === "boolean"
        ? raw.autoOpenOutputFolder
        : typeof raw.autoOpenFirstOutput === "boolean"
          ? raw.autoOpenFirstOutput
          : defaultSettings.autoOpenOutputFolder,
    autoOpenLogFile:
      typeof raw.autoOpenLogFile === "boolean"
        ? raw.autoOpenLogFile
        : defaultSettings.autoOpenLogFile,
    autoCheckUpdates:
      typeof raw.autoCheckUpdates === "boolean"
        ? raw.autoCheckUpdates
        : typeof raw.autoCheckUpdate === "boolean"
          ? raw.autoCheckUpdate
          : defaultSettings.autoCheckUpdates,
    keepHistoryCount:
      typeof raw.keepHistoryCount === "number"
        ? raw.keepHistoryCount
        : defaultSettings.keepHistoryCount,
  };
};

settings.value = normalizeSettings(settings.value);

const headerEyebrow = computed(() => {
  switch (activeSection.value) {
    case "settings":
      return "使用偏好";
    case "about":
      return "软件说明";
    default:
      return "功能执行";
  }
});
const historyLimit = computed(() => {
  const raw = Number(settings.value.keepHistoryCount);
  if (!Number.isFinite(raw)) {
    return defaultSettings.keepHistoryCount;
  }
  return Math.max(1, Math.min(30, Math.round(raw)));
});
const recentHistory = computed(() =>
  taskHistory.value.slice(0, historyLimit.value),
);
const updateStatusLabel = computed(() => {
  switch (updateStatus.value) {
    case "checking":
      return "正在检查更新...";
    case "available":
      return latestVersion.value
        ? `发现新版本 v${latestVersion.value}`
        : "发现新版本";
    case "latest":
      return latestVersion.value
        ? `当前已是最新版本 v${latestVersion.value}`
        : "当前已是最新版本";
    case "error":
      return updateMessage.value;
    default:
      return updateMessage.value;
  }
});
const selectedFile = computed(
  () => files.value.find((item) => item.path === selectedFilePath.value) ?? null,
);
const selectedFileFontMessage = computed(() => {
  if (!selectedFile.value) {
    return "";
  }

  if (selectedFile.value.fontLoadStatus === "loading") {
    return "正在读取字体列表...";
  }

  if (selectedFile.value.fontLoadStatus === "error") {
    return selectedFile.value.fontLoadError || "读取字体列表失败，请重试。";
  }

  if (
    selectedFile.value.fontLoadStatus === "loaded" &&
    selectedFile.value.fontFamilies.length === 0
  ) {
    return "该文件暂未识别到可处理的字体，请确认 EPUB 内容完整。";
  }

  return "";
});
const isTaskSection = computed(() =>
  taskSections.includes(activeSection.value as TaskType),
);
const activeTask = computed<TaskType | null>(() =>
  isTaskSection.value ? (activeSection.value as TaskType) : null,
);
const activeTaskLabel = computed(() => {
  if (!activeTask.value) {
    return "";
  }
  return formatTaskType(activeTask.value);
});
const activeTaskDescription = computed(() => {
  switch (activeTask.value) {
    case "reformat":
      return "重构 EPUB 结构、标准化文件布局，支持单本、批量和目录扫描。";
    case "decrypt":
      return "执行文件解密流程，支持单本、批量和目录扫描。";
    case "encrypt":
      return "生成文件加密版本，支持单本、批量和目录扫描。";
    case "font_encrypt":
      return "按每本 EPUB 独立选择字体范围，随后批量执行。";
    case "transfer_img":
      return "转换 EPUB 内 WEBP 图片，支持单本、批量和目录扫描。";
    default:
      return "";
  }
});

const activeTitle = computed(() => {
  switch (activeSection.value) {
    case "reformat":
      return "格式化";
    case "decrypt":
      return "文件解密";
    case "encrypt":
      return "文件加密";
    case "font_encrypt":
      return "字体加密";
    case "transfer_img":
      return "图片转换";
    case "settings":
      return "设置";
    default:
      return "关于";
  }
});

const activeDescription = computed(() => {
  switch (activeSection.value) {
    case "reformat":
    case "decrypt":
    case "encrypt":
    case "font_encrypt":
    case "transfer_img":
      return activeTaskDescription.value;
    case "settings":
      return "管理输出位置、自动打开选项与历史记录。";
    default:
      return "查看软件支持范围、使用方式与处理提示。";
  }
});

const visibleProgressValue = computed(() => {
  if (taskRunning.value) {
    return Math.max(0, Math.min(100, progress.value));
  }

  if (fontLoading.value && fontProgressTotal.value > 0) {
    return Math.round((fontProgressCurrent.value / fontProgressTotal.value) * 100);
  }

  return 0;
});

const visibleProgressText = computed(() => {
  if (taskRunning.value && taskProgressTotal.value > 0) {
    return `第 ${taskProgressCurrent.value}/${taskProgressTotal.value} 本`;
  }

  if (fontLoading.value && fontProgressTotal.value > 0) {
    return `正在读取第 ${fontProgressCurrent.value}/${fontProgressTotal.value} 本`;
  }

  return "";
});

const visibleProgressMessage = computed(() => {
  if (taskRunning.value && taskProgressFileName.value) {
    return `当前处理：${taskProgressFileName.value}`;
  }

  if (fontLoading.value && fontProgressFileName.value) {
    return `当前读取：${fontProgressFileName.value}`;
  }

  return "";
});

const ensureSelectedFile = (preferredPath?: string) => {
  if (preferredPath && files.value.some((item) => item.path === preferredPath)) {
    selectedFilePath.value = preferredPath;
    return;
  }

  if (files.value.some((item) => item.path === selectedFilePath.value)) {
    return;
  }

  selectedFilePath.value = files.value[0]?.path ?? "";
};

const pickNeighborPath = (snapshot: QueuedFile[], currentPath: string): string => {
  const currentIndex = snapshot.findIndex((item) => item.path === currentPath);
  if (currentIndex === -1) {
    return snapshot[0]?.path ?? "";
  }

  return snapshot[currentIndex + 1]?.path ?? snapshot[currentIndex - 1]?.path ?? "";
};

const selectFile = (path: string) => {
  ensureSelectedFile(path);
};

const toErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error === "string" && error.trim()) {
    return error;
  }
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  if (error && typeof error === "object" && "message" in error) {
    return String(error.message);
  }
  return fallback;
};

const normalizeVersion = (value: string): string => value.trim().replace(/^v/i, "");

const compareVersions = (left: string, right: string): number => {
  const leftParts = normalizeVersion(left)
    .split(/[^0-9]+/)
    .filter(Boolean)
    .map((item) => Number.parseInt(item, 10));
  const rightParts = normalizeVersion(right)
    .split(/[^0-9]+/)
    .filter(Boolean)
    .map((item) => Number.parseInt(item, 10));
  const length = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < length; index += 1) {
    const leftValue = leftParts[index] ?? 0;
    const rightValue = rightParts[index] ?? 0;
    if (leftValue > rightValue) {
      return 1;
    }
    if (leftValue < rightValue) {
      return -1;
    }
  }

  return 0;
};

const getContainingDirectory = (path: string): string => {
  const normalized = path.replace(/[\\/]+$/, "");
  const lastForwardSlash = normalized.lastIndexOf("/");
  const lastBackwardSlash = normalized.lastIndexOf("\\");
  const lastSeparator = Math.max(lastForwardSlash, lastBackwardSlash);

  if (lastSeparator < 0) {
    return normalized;
  }
  if (lastSeparator === 0) {
    return normalized.slice(0, 1);
  }
  if (lastSeparator === 2 && /^[A-Za-z]:/.test(normalized)) {
    return normalized.slice(0, 3);
  }

  return normalized.slice(0, lastSeparator);
};

const formatUpdateTime = (value: string): string =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const openLatestReleasePage = () => {
  const targetUrl = latestReleaseUrl.value || LATEST_RELEASE_URL;
  updateNoticeVisible.value = false;
  if (isTauriRuntime()) {
    void openPath(targetUrl);
    return;
  }
  if (typeof window !== "undefined") {
    window.open(targetUrl, "_blank", "noopener,noreferrer");
  }
};

const persistUpdateCheckState = () => {
  persistedUpdateCheckState.value = {
    checkedAt: updateCheckedAt.value,
    latestVersion: latestVersion.value,
    latestReleaseUrl: latestReleaseUrl.value || LATEST_RELEASE_URL,
    status: updateStatus.value,
    message: updateMessage.value,
  };
};

const shouldSkipAutomaticUpdateCheck = (): boolean => {
  if (!updateCheckedAt.value) {
    return false;
  }

  const checkedAt = new Date(updateCheckedAt.value).getTime();
  if (!Number.isFinite(checkedAt)) {
    return false;
  }

  return Date.now() - checkedAt < UPDATE_CHECK_INTERVAL_MS;
};

const checkForUpdates = async (options?: { silent?: boolean; automatic?: boolean }) => {
  if (options?.automatic && shouldSkipAutomaticUpdateCheck()) {
    return;
  }

  updateStatus.value = "checking";
  updateMessage.value = "正在检查更新...";
  persistUpdateCheckState();

  try {
    const response = await fetch(RELEASES_API_URL, {
      headers: {
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    });
    if (!response.ok) {
      if (response.status === 403) {
        throw new Error("GitHub 更新接口当前不可用或已触发频率限制，请稍后再试。");
      }
      throw new Error(`更新检查失败（${response.status}）`);
    }

    const payload = (await response.json()) as {
      tag_name?: string;
      html_url?: string;
      name?: string;
    };
    const releaseVersion = normalizeVersion(
      payload.tag_name || payload.name || currentVersion.value,
    );

    latestVersion.value = releaseVersion;
    latestReleaseUrl.value = payload.html_url || LATEST_RELEASE_URL;
    updateCheckedAt.value = new Date().toISOString();

    if (compareVersions(releaseVersion, currentVersion.value) > 0) {
      updateStatus.value = "available";
      updateMessage.value = `发现新版本 v${releaseVersion}，可前往 GitHub Release 下载。`;
      updateNoticeVisible.value = true;
      persistUpdateCheckState();
      return;
    }

    updateStatus.value = "latest";
    updateMessage.value = `当前已是最新版本 v${currentVersion.value}。`;
    updateNoticeVisible.value = false;
    persistUpdateCheckState();
  } catch (error) {
    updateStatus.value = "error";
    updateMessage.value = toErrorMessage(error, "检查更新失败，请稍后重试。");
    updateCheckedAt.value = new Date().toISOString();
    latestReleaseUrl.value = LATEST_RELEASE_URL;
    updateNoticeVisible.value = false;
    persistUpdateCheckState();
    if (options?.silent) {
      return;
    }
  }
};

const dismissUpdateNotice = () => {
  updateNoticeVisible.value = false;
};

const queuePaths = (paths: string[]) => {
  const hadSelectedFile = files.value.some(
    (item) => item.path === selectedFilePath.value,
  );
  let firstQueuedPath = "";
  const valid = paths
    .map((path) => path.trim())
    .filter((path) => path.toLowerCase().endsWith(".epub"));

  for (const path of valid) {
    if (files.value.some((item) => item.path === path)) {
      continue;
    }
    files.value.push({
      path,
      name: path.split(/[\\/]/).pop() ?? path,
      fontFamilies: [],
      selectedFontFamilies: [],
      fontLoadStatus: "idle",
      fontLoadError: "",
    });
    if (!firstQueuedPath) {
      firstQueuedPath = path;
    }
  }

  if (hadSelectedFile) {
    ensureSelectedFile();
    return;
  }

  ensureSelectedFile(firstQueuedPath);
};

const pickFiles = async () => {
  if (!isTauriRuntime()) {
    browserFileInput.value?.click();
    return;
  }

  const selected = await open({
    multiple: true,
    directory: false,
    filters: [{ name: "EPUB", extensions: ["epub"] }],
  });
  if (selected == null) {
    return;
  }

  queuePaths(Array.isArray(selected) ? selected : [selected]);
};

const scanInputDirectory = async () => {
  if (!isTauriRuntime()) {
    return;
  }

  const selected = await open({
    directory: true,
    multiple: false,
  });

  if (typeof selected === "string") {
    const found = await collectEpubFiles(selected);
    queuePaths(found);
  }
};

const pickOutputDirectory = async () => {
  if (!isTauriRuntime()) {
    return;
  }

  const selected = await open({
    directory: true,
    multiple: false,
  });

  if (typeof selected === "string") {
    outputDir.value = selected;
  }
};

const resetOutput = () => {
  outputDir.value = "";
};

const removeFile = (path: string) => {
  const snapshot = [...files.value];
  const nextSelectedPath =
    selectedFilePath.value === path
      ? pickNeighborPath(snapshot, path)
      : selectedFilePath.value;
  files.value = files.value.filter((item) => item.path !== path);
  if (snapshot.some((item) => item.path === selectedFilePath.value && item.path === path)) {
    ensureSelectedFile(nextSelectedPath);
    return;
  }

  ensureSelectedFile();
};

const clearFiles = () => {
  files.value = [];
  selectedFilePath.value = "";
};

const handleBrowserFiles = (event: Event) => {
  const input = event.target as HTMLInputElement;
  if (!input.files) {
    return;
  }
  queuePaths(Array.from(input.files).map((file) => file.name));
  input.value = "";
};

const syncFontFamilies = (
  item: QueuedFile,
  families: string[],
  previousStatus: FontLoadStatus,
) => {
  const previousSelection = new Set(item.selectedFontFamilies);
  item.fontFamilies = families;

  if (previousStatus === "loaded") {
    item.selectedFontFamilies = families.filter((family) =>
      previousSelection.has(family),
    );
    return;
  }

  item.selectedFontFamilies = [...families];
};

const loadFontFamilies = async (options?: {
  force?: boolean;
  filePaths?: string[];
  silent?: boolean;
}) => {
  if (!isTauriRuntime()) {
    return;
  }

  const targets = options?.filePaths?.length
    ? files.value.filter((item) => options.filePaths?.includes(item.path))
    : files.value;
  if (targets.length === 0) {
    return;
  }

  fontLoading.value = true;
  fontProgressCurrent.value = 0;
  fontProgressTotal.value = targets.length;
  fontProgressFileName.value = "";
  let refreshedCount = 0;
  let emptyCount = 0;
  let errorCount = 0;

  try {
    for (const [index, item] of targets.entries()) {
      if (!options?.force && item.fontLoadStatus === "loaded") {
        continue;
      }

      fontProgressCurrent.value = index + 1;
      fontProgressFileName.value = item.name;

      const previousStatus = item.fontLoadStatus;
      item.fontLoadStatus = "loading";
      item.fontLoadError = "";

      try {
        const families = await listFontTargets(item.path);
        syncFontFamilies(item, families, previousStatus);
        item.fontLoadStatus = "loaded";
        refreshedCount += 1;
        if (families.length === 0) {
          emptyCount += 1;
        }
      } catch (error) {
        item.fontFamilies = [];
        item.selectedFontFamilies = [];
        item.fontLoadStatus = "error";
        item.fontLoadError = toErrorMessage(error, "读取字体列表失败，请重试。");
        errorCount += 1;
      }
    }
  } finally {
    fontLoading.value = false;
    fontProgressCurrent.value = 0;
    fontProgressTotal.value = 0;
    fontProgressFileName.value = "";
  }

  if (options?.silent) {
    return;
  }

  if (errorCount > 0) {
    taskStatus.value = `字体列表已刷新，${errorCount} 本文件读取失败。`;
    return;
  }

  if (refreshedCount === 0) {
    taskStatus.value = "字体列表已是最新状态。";
    return;
  }

  if (emptyCount > 0) {
    taskStatus.value = `字体列表已刷新，${emptyCount} 本文件未识别到可处理字体。`;
    return;
  }

  taskStatus.value = `字体列表已刷新，共更新 ${refreshedCount} 本文件。`;
};

const buildRequest = (): TaskRequest => {
  if (!activeTask.value) {
    throw new Error("当前页面不是任务工具页，无法构建执行请求。");
  }
  const request: TaskRequest = {
    taskId: crypto.randomUUID(),
    taskType: activeTask.value,
    inputFiles: files.value.map((item) => item.path),
    outputDir: outputDir.value || null,
    options: {},
  };

  if (activeTask.value === "font_encrypt") {
    request.options = {
      target_font_families_by_file: Object.fromEntries(
        files.value.map((item) => [item.path, item.selectedFontFamilies]),
      ),
    };
  }

  return request;
};

const formatTaskType = (taskType: TaskType): string => {
  switch (taskType) {
    case "reformat":
      return "格式化";
    case "decrypt":
      return "文件解密";
    case "encrypt":
      return "文件加密";
    case "font_encrypt":
      return "字体加密";
    case "transfer_img":
      return "图片转换";
  }
};

const formatHistoryTime = (value: string): string =>
  new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const rememberTask = (taskType: TaskType, taskResult: TaskResult) => {
  const entry: TaskHistoryEntry = {
    id: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    taskType,
    status: taskResult.status,
    summary: taskResult.summary,
    firstOutput: taskResult.outputs[0] ?? null,
  };
  taskHistory.value = [entry, ...taskHistory.value].slice(0, historyLimit.value);
};

const syncQueueWithResult = (taskResult: TaskResult) => {
  const snapshot = [...files.value];
  const nextSelectedPath = pickNeighborPath(snapshot, selectedFilePath.value);
  const blockedPaths = new Set(taskResult.errors.map((item) => item.input_file));

  files.value = files.value.filter((item) => blockedPaths.has(item.path));
  if (!files.value.some((item) => item.path === selectedFilePath.value)) {
    ensureSelectedFile(nextSelectedPath);
    return;
  }

  ensureSelectedFile();
};

const clearHistory = () => {
  taskHistory.value = [];
};

const maybeOpenFollowUpTargets = (taskResult: TaskResult) => {
  if (!isTauriRuntime()) {
    return;
  }
  if (settings.value.autoOpenOutputFolder && taskResult.outputs[0]) {
    void openPath(getContainingDirectory(taskResult.outputs[0]));
  }
  if (settings.value.autoOpenLogFile && taskResult.log_path) {
    void openPath(taskResult.log_path);
  }
};

const pushLog = (event: TaskEvent) => {
  logs.value = [...logs.value, event].slice(-300);
  progress.value = event.progress;
  taskStatus.value = event.message;
  taskProgressCurrent.value = event.current_index ?? taskProgressCurrent.value;
  taskProgressTotal.value = event.total_files ?? taskProgressTotal.value;
  taskProgressFileName.value = event.current_file
    ? event.current_file.split(/[\\/]/).pop() ?? event.current_file
    : taskProgressFileName.value;
  if (event.result) {
    result.value = event.result;
  }
};

const runSelectedTask = async () => {
  if (files.value.length === 0 || taskRunning.value || !activeTask.value) {
    return;
  }
  if (activeTask.value === "font_encrypt") {
    await loadFontFamilies();
  }

  taskRunning.value = true;
  progress.value = 0;
  taskProgressCurrent.value = 0;
  taskProgressTotal.value = files.value.length;
  taskProgressFileName.value = "";
  logs.value = [];
  result.value = null;
  const request = buildRequest();

  try {
    const taskResult = await runTask(request, pushLog);
    result.value = taskResult;
    syncQueueWithResult(taskResult);
    rememberTask(request.taskType, taskResult);
    maybeOpenFollowUpTargets(taskResult);
    taskStatus.value = taskResult.ok ? "任务完成" : "任务结束，但存在失败项";
  } catch (error) {
    const message = toErrorMessage(error, "执行过程中出现未知错误");
    logs.value.push({
      event: "task.bridge.error",
      task_id: "local",
      status: "error",
      progress: progress.value,
      message,
      level: "error",
    });
    taskStatus.value = message;
  } finally {
    taskRunning.value = false;
    taskProgressCurrent.value = 0;
    taskProgressTotal.value = 0;
    taskProgressFileName.value = "";
  }
};

const clearLogs = () => {
  logs.value = [];
};

const openLogFile = () => {
  void openPath("log.txt");
};

const openOutputFolder = (path: string) => {
  void openPath(getContainingDirectory(path));
};

const toggleFontFamily = (filePath: string, family: string) => {
  const target = files.value.find((item) => item.path === filePath);
  if (!target) {
    return;
  }
  if (target.selectedFontFamilies.includes(family)) {
    target.selectedFontFamilies = target.selectedFontFamilies.filter(
      (item) => item !== family,
    );
  } else {
    target.selectedFontFamilies = [...target.selectedFontFamilies, family];
  }
};

watch(activeSection, async (section) => {
  activeSection.value = normalizeSectionKey(section);
  if (activeSection.value === "font_encrypt" && selectedFilePath.value) {
    await loadFontFamilies({
      filePaths: [selectedFilePath.value],
      silent: true,
    });
  }
});

watch(
  () => files.value.map((item) => item.path).join("|"),
  async () => {
    ensureSelectedFile();
    if (activeSection.value === "font_encrypt" && selectedFilePath.value) {
      await loadFontFamilies({
        filePaths: [selectedFilePath.value],
        silent: true,
      });
    }
  },
);

watch(selectedFilePath, async (path) => {
  if (activeSection.value !== "font_encrypt" || !path) {
    return;
  }

  await loadFontFamilies({
    filePaths: [path],
    silent: true,
  });
});

watch(
  () => settings.value.keepHistoryCount,
  () => {
    settings.value.keepHistoryCount = historyLimit.value;
    taskHistory.value = taskHistory.value.slice(0, historyLimit.value);
  },
);

watch(
  () => settings.value.autoCheckUpdates,
  (enabled, previous) => {
    if (enabled && previous === false) {
      void checkForUpdates({ silent: true, automatic: true });
    }
  },
);

let unlistenDrop: (() => void) | null = null;

onMounted(async () => {
  if (isTauriRuntime()) {
    try {
      currentVersion.value = normalizeVersion(await getVersion());
    } catch {
      currentVersion.value = CURRENT_FALLBACK_VERSION;
    }
  }

  if (settings.value.autoCheckUpdates) {
    void checkForUpdates({ silent: true, automatic: true });
  }

  if (!isTauriRuntime()) {
    return;
  }
  unlistenDrop = await getCurrentWindow().onDragDropEvent((event) => {
    if (!isTaskSection.value) {
      dragActive.value = false;
      return;
    }
    if (event.payload.type === "over") {
      dragActive.value = true;
      return;
    }
    if (event.payload.type === "drop") {
      dragActive.value = false;
      queuePaths(event.payload.paths);
      return;
    }
    dragActive.value = false;
  });
});

onBeforeUnmount(() => {
  unlistenDrop?.();
});

activeSection.value = normalizeSectionKey(activeSection.value);
</script>

<template>
  <div class="app-shell">
    <SideNav
      :active="activeSection"
      :items="sectionItems"
      @select="activeSection = $event"
    />

    <main class="workspace">
      <section v-if="updateNoticeVisible && updateStatus === 'available'" class="update-toast">
        <div class="update-toast-copy">
          <p class="eyebrow">版本更新</p>
          <strong>发现新版本 v{{ latestVersion }}</strong>
          <p>{{ updateMessage }}</p>
        </div>
        <div class="update-toast-actions">
          <button class="ghost-btn" type="button" @click="dismissUpdateNotice">
            稍后
          </button>
          <button class="primary-btn" type="button" @click="openLatestReleasePage">
            前往下载
          </button>
        </div>
      </section>

      <header class="workspace-header">
        <div>
          <p class="eyebrow">{{ headerEyebrow }}</p>
          <h2>{{ activeTitle }}</h2>
          <p class="muted">{{ activeDescription }}</p>
        </div>
      </header>

      <template v-if="isTaskSection">
        <DropZone
          :is-active="dragActive"
          :file-count="files.length"
          @pick-files="pickFiles"
          @scan-directory="scanInputDirectory"
          @clear="clearFiles"
        />

        <section class="work-grid">
          <article class="panel">
            <div class="panel-head">
              <div>
                <p class="eyebrow">任务配置</p>
                <h3>输出与执行</h3>
              </div>
              <div class="panel-actions">
                <button class="ghost-btn" type="button" @click="pickOutputDirectory">
                  选择输出目录
                </button>
                <button class="ghost-btn" type="button" @click="resetOutput">
                  重置输出路径
                </button>
              </div>
            </div>

            <div class="settings-stack">
              <label class="field">
                <span>输出目录</span>
                <input
                  :value="outputDir || '默认：源文件同级目录'"
                  readonly
                  type="text"
                />
              </label>

              <div class="task-callout">
                <strong>{{ activeTaskLabel }}</strong>
                <span>{{ activeTaskDescription }}</span>
              </div>

              <div v-if="taskRunning || fontLoading" class="task-progress">
                <div class="task-progress-head">
                  <strong>{{ visibleProgressText }}</strong>
                  <span>{{ visibleProgressValue }}%</span>
                </div>
                <div class="task-progress-track">
                  <div
                    class="task-progress-fill"
                    :style="{ width: `${visibleProgressValue}%` }"
                  />
                </div>
                <p v-if="visibleProgressMessage" class="task-progress-message">
                  {{ visibleProgressMessage }}
                </p>
              </div>

              <button
                class="primary-btn wide"
                :disabled="taskRunning || files.length === 0"
                type="button"
                @click="runSelectedTask"
              >
                {{ taskRunning ? "处理中..." : "开始执行" }}
              </button>
            </div>
          </article>

          <article class="panel">
            <div class="panel-head">
              <div>
                <p class="eyebrow">文件队列</p>
                <h3>待处理列表</h3>
              </div>
            </div>

            <div class="file-table">
              <div v-if="files.length === 0" class="empty-state">
                还没有加入 EPUB 文件。
              </div>
              <div
                v-for="file in files"
                :key="file.path"
                class="file-row"
                :class="{ active: file.path === selectedFilePath }"
              >
                <button class="file-select" type="button" @click="selectFile(file.path)">
                  <span class="file-row-head">
                    <strong>{{ file.name }}</strong>
                    <span v-if="file.path === selectedFilePath" class="file-tag">当前</span>
                  </span>
                  <p>{{ file.path }}</p>
                </button>
                <button class="ghost-btn" type="button" @click.stop="removeFile(file.path)">
                  移除
                </button>
              </div>
            </div>
          </article>
        </section>

        <section v-if="activeTask === 'font_encrypt'" class="panel font-panel">
          <div class="panel-head">
            <div>
              <p class="eyebrow">字体范围</p>
              <h3>文件级选择</h3>
              <p class="muted">
                {{ selectedFile ? `当前聚焦：${selectedFile.name}` : "先从右侧队列选择一本 EPUB。" }}
              </p>
            </div>
            <div class="panel-actions">
              <button
                class="ghost-btn"
                :disabled="fontLoading || files.length === 0"
                type="button"
                @click="loadFontFamilies({ force: true })"
              >
                {{ fontLoading ? "刷新中..." : "刷新字体列表" }}
              </button>
            </div>
          </div>
          <div class="font-grid">
            <div v-if="!selectedFile" class="empty-state">
              队列为空，暂时没有可配置的字体目标。
            </div>
            <div v-else class="font-card font-card-focus">
              <strong>{{ selectedFile.name }}</strong>
              <p>{{ selectedFile.fontFamilies.length }} 个可选字体</p>
              <label
                v-for="family in selectedFile.fontFamilies"
                :key="`${selectedFile.path}-${family}`"
                class="font-option"
              >
                <input
                  :disabled="selectedFile.fontLoadStatus === 'loading'"
                  :checked="selectedFile.selectedFontFamilies.includes(family)"
                  type="checkbox"
                  @change="toggleFontFamily(selectedFile.path, family)"
                />
                <span>{{ family }}</span>
              </label>
              <p v-if="selectedFileFontMessage" class="muted">
                {{ selectedFileFontMessage }}
              </p>
            </div>
          </div>
        </section>
      </template>

      <section v-if="activeSection === 'settings'" class="panel settings-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">设置</p>
            <h3>使用偏好</h3>
          </div>
        </div>
        <ul class="settings-list">
          <li>当前默认输出位置：{{ outputDir || "源文件同级目录" }}</li>
          <li>可在任务完成后自动打开输出文件夹，方便继续查看和整理结果。</li>
          <li>可选择自动打开处理日志，便于回看处理过程与提示信息。</li>
          <li>可自动检查新版本，并跳转到 GitHub Release 下载最新版。</li>
          <li>最近任务会保留在本机，方便回看处理记录。</li>
        </ul>
        <div class="settings-form">
          <label class="toggle-row">
            <input v-model="settings.autoOpenOutputFolder" type="checkbox" />
            <span>任务完成后自动打开输出文件夹</span>
          </label>
          <label class="toggle-row">
            <input v-model="settings.autoOpenLogFile" type="checkbox" />
            <span>任务完成后自动打开处理日志</span>
          </label>
          <label class="toggle-row">
            <input v-model="settings.autoCheckUpdates" type="checkbox" />
            <span>启动时自动检查更新</span>
          </label>
          <label class="field field-compact">
            <span>历史记录条数</span>
            <input v-model.number="settings.keepHistoryCount" min="1" max="30" type="number" />
          </label>
        </div>
        <div class="panel-head panel-head-sub">
          <div>
            <p class="eyebrow">更新</p>
            <h3>版本更新</h3>
          </div>
          <div class="panel-actions">
            <button
              class="ghost-btn"
              :disabled="updateStatus === 'checking'"
              type="button"
              @click="checkForUpdates()"
            >
              {{ updateStatus === "checking" ? "检查中..." : "检查更新" }}
            </button>
            <button class="ghost-btn" type="button" @click="openLatestReleasePage">
              下载最新版本
            </button>
          </div>
        </div>
        <div class="task-callout">
          <strong>当前版本 v{{ currentVersion }}</strong>
          <span>{{ updateStatusLabel }}</span>
          <span v-if="latestVersion">最新版本：v{{ latestVersion }}</span>
          <span v-if="updateCheckedAt">最近检查：{{ formatUpdateTime(updateCheckedAt) }}</span>
        </div>
        <div class="panel-head panel-head-sub">
          <div>
            <p class="eyebrow">历史</p>
            <h3>最近任务</h3>
          </div>
          <div class="panel-actions">
            <button class="ghost-btn" type="button" @click="clearHistory">
              清空历史
            </button>
          </div>
        </div>
        <div class="history-list">
          <div v-if="recentHistory.length === 0" class="empty-state">
            还没有已完成任务记录。
          </div>
          <div v-for="entry in recentHistory" :key="entry.id" class="history-row">
            <div>
              <strong>{{ formatTaskType(entry.taskType) }}</strong>
              <p>
                {{ formatHistoryTime(entry.createdAt) }} · {{ entry.status }} · 成功
                {{ entry.summary.success }}/{{ entry.summary.total }}
              </p>
            </div>
            <button
              v-if="entry.firstOutput"
              class="ghost-btn"
              type="button"
              @click="openOutputFolder(entry.firstOutput)"
            >
              打开输出文件夹
            </button>
          </div>
        </div>
      </section>

      <section v-if="activeSection === 'about'" class="panel settings-panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">关于</p>
            <h3>软件说明</h3>
          </div>
        </div>
        <ul class="settings-list">
          <li>支持格式化、文件解密、文件加密、字体加密和图片转换五类 EPUB 处理任务。</li>
          <li>所有功能都支持单本、多本和目录扫描，适合连续处理多个文件。</li>
          <li>字体加密支持按每本 EPUB 单独选择需要处理的字体范围。</li>
          <li>处理完成后可在结果区直接打开输出文件夹，也可以查看跳过或失败原因。</li>
          <li>如果队列里有多本书，当前文件处理完成后会自动切换到下一本。</li>
        </ul>
      </section>

      <TaskConsole
        v-if="isTaskSection"
        :logs="logs"
        :result="result"
        @clear-log="clearLogs"
        @open-log="openLogFile"
        @open-output-folder="openOutputFolder"
      />
    </main>

    <input
      ref="browserFileInput"
      accept=".epub"
      hidden
      multiple
      type="file"
      @change="handleBrowserFiles"
    />
  </div>
</template>
