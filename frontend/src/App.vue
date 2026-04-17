<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWindow } from "@tauri-apps/api/window";

import DropZone from "./components/DropZone.vue";
import SideNav from "./components/SideNav.vue";
import { usePersistentState } from "./composables/usePersistentState";
import { useTaskBridge } from "./composables/useTaskBridge";
import type {
  AppSettings,
  FontLoadStatus,
  QueuedFile,
  SectionKey,
  TaskAggregateStats,
  TaskEvent,
  TaskHistoryEntry,
  TaskOutputDirectoryMap,
  TaskRequest,
  TaskResult,
  TaskType,
  UpdateCheckState,
} from "./types";

type MasonryCardKey = "task-config" | "font-panel" | "file-queue" | "task-log" | "task-result";

type MasonryCard = {
  key: MasonryCardKey;
  weight: number;
  preferredColumn?: number;
};

const sectionItems: Array<{
  key: SectionKey;
  label: string;
  description: string;
}> = [
    { key: "reformat", label: "格式化", description: "重构 EPUB 结构" },
    { key: "decrypt", label: "文件解密", description: "处理文件名混淆" },
    { key: "encrypt", label: "文件加密", description: "生成混淆版 EPUB" },
    { key: "font_encrypt", label: "字体加密", description: "对所选字体进行混淆" },
    { key: "transfer_img", label: "图片转换", description: "批量转换 WEBP 图片" },
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

const createTaskRecord = <T,>(factory: () => T): Record<TaskType, T> => ({
  reformat: factory(),
  decrypt: factory(),
  encrypt: factory(),
  font_encrypt: factory(),
  transfer_img: factory(),
});
const createOutputDirectoryMap = (): TaskOutputDirectoryMap => createTaskRecord(() => "");

const CURRENT_FALLBACK_VERSION = __APP_VERSION__;
const APP_IDENTIFIER = "com.cnwxi.epubtool.newui";
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
const defaultTaskAggregateStats: TaskAggregateStats = {
  total: 0,
  success: 0,
  failed: 0,
  skipped: 0,
};

const {
  collectEpubFiles,
  getLogPath,
  isTauriRuntime,
  listFontTargets,
  openPath,
  resolveInputSources,
  runTask,
} = useTaskBridge();

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
const outputDirs = usePersistentState<TaskOutputDirectoryMap>(
  "epub-tool.output-dirs",
  createOutputDirectoryMap(),
);
const settings = usePersistentState<AppSettings>(
  "epub-tool.settings",
  defaultSettings,
);
const taskHistory = usePersistentState<TaskHistoryEntry[]>(
  "epub-tool.task-history",
  [],
);
const taskAggregateStats = usePersistentState<TaskAggregateStats>(
  "epub-tool.aggregate-stats",
  defaultTaskAggregateStats,
);
const persistedUpdateCheckState = usePersistentState<UpdateCheckState>(
  "epub-tool.update-check-state",
  defaultUpdateCheckState,
);
const taskFilesByType = ref<Record<TaskType, QueuedFile[]>>(createTaskRecord(() => []));
const selectedFilePathByType = ref<Record<TaskType, string>>(createTaskRecord(() => ""));
const taskLogsByType = ref<Record<TaskType, TaskEvent[]>>(createTaskRecord(() => []));
const taskResultByType = ref<Record<TaskType, TaskResult | null>>(
  createTaskRecord(() => null),
);
const progress = ref(0);
const taskRunning = ref(false);
const runningTaskType = ref<TaskType | null>(null);
const taskStatus = ref("待命");
const fontLoading = ref(false);
const taskProgressCurrent = ref(0);
const taskProgressTotal = ref(0);
const taskProgressFileName = ref("");
const fontProgressCurrent = ref(0);
const fontProgressTotal = ref(0);
const fontProgressFileName = ref("");
let fontLoadRequestId = 0;
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
const currentLogPath = ref("");
const prefersReducedMotion = ref(false);
const defaultLogPaths = [
  {
    platform: "Windows",
    path: `%LOCALAPPDATA%\\${APP_IDENTIFIER}\\logs\\log.txt`,
  },
  {
    platform: "macOS",
    path: `~/Library/Logs/${APP_IDENTIFIER}/log.txt`,
  },
  {
    platform: "Linux",
    path: `~/.local/share/${APP_IDENTIFIER}/logs/log.txt`,
  },
];

const masonryBoardRef = ref<HTMLElement | null>(null);
const masonryBoardWidth = ref(0);
const workspaceRef = ref<HTMLElement | null>(null);
const sideNavShellRef = ref<HTMLElement | null>(null);
const workspaceScrollbarTrackRef = ref<HTMLElement | null>(null);
const sideNavScrollbarTrackRef = ref<HTMLElement | null>(null);

const workspaceScrollbarVisible = ref(false);
const workspaceScrollbarThumbHeight = ref(0);
const workspaceScrollbarThumbTop = ref(0);

const sideNavScrollbarVisible = ref(false);
const sideNavScrollbarThumbHeight = ref(0);
const sideNavScrollbarThumbTop = ref(0);

let masonryResizeObserver: ResizeObserver | null = null;
let customScrollbarResizeObserver: ResizeObserver | null = null;
const handleMasonryWindowResize = () => {
  void measureMasonryBoard();
};
const observeMasonryBoard = () => {
  if (!masonryResizeObserver) {
    return;
  }
  masonryResizeObserver.disconnect();
  if (masonryBoardRef.value) {
    masonryResizeObserver.observe(masonryBoardRef.value);
  }
};
const measureMasonryBoard = async () => {
  await nextTick();
  observeMasonryBoard();
  masonryBoardWidth.value = masonryBoardRef.value?.clientWidth ?? 0;
};

const clampScrollbarThumb = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

const updateCustomScrollbar = (
  element: HTMLElement | null,
  trackElement: HTMLElement | null,
  thumbHeightRef: { value: number },
  thumbTopRef: { value: number },
  visibleRef: { value: boolean },
) => {
  if (!element || typeof window === "undefined") {
    thumbHeightRef.value = 0;
    thumbTopRef.value = 0;
    visibleRef.value = false;
    return;
  }

  const { scrollTop, scrollHeight, clientHeight } = element;
  const trackHeight = trackElement?.clientHeight ?? 0;
  const overflow = scrollHeight - clientHeight;

  if (overflow <= 1 || clientHeight <= 0 || trackHeight <= 0) {
    thumbHeightRef.value = 0;
    thumbTopRef.value = 0;
    visibleRef.value = false;
    return;
  }

  const rawThumbHeight = (clientHeight / scrollHeight) * trackHeight;
  const thumbHeight = clampScrollbarThumb(rawThumbHeight, 36, trackHeight);
  const maxThumbTravel = Math.max(trackHeight - thumbHeight, 0);
  const scrollRatio = Math.min(Math.max(scrollTop / Math.max(overflow, 1), 0), 1);
  const thumbTop = maxThumbTravel * scrollRatio;
  thumbHeightRef.value = thumbHeight;
  thumbTopRef.value = thumbTop;
  visibleRef.value = true;
};

const updateWorkspaceScrollbar = () => {
  updateCustomScrollbar(
    workspaceRef.value,
    workspaceScrollbarTrackRef.value,
    workspaceScrollbarThumbHeight,
    workspaceScrollbarThumbTop,
    workspaceScrollbarVisible,
  );
};

const updateSideNavScrollbar = () => {
  updateCustomScrollbar(
    sideNavShellRef.value,
    sideNavScrollbarTrackRef.value,
    sideNavScrollbarThumbHeight,
    sideNavScrollbarThumbTop,
    sideNavScrollbarVisible,
  );
};

const updateAllCustomScrollbars = () => {
  updateWorkspaceScrollbar();
  updateSideNavScrollbar();
};

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
outputDirs.value = {
  ...createOutputDirectoryMap(),
  ...(outputDirs.value ?? {}),
};
taskAggregateStats.value = {
  ...defaultTaskAggregateStats,
  ...(taskAggregateStats.value ?? {}),
};

if (typeof window !== "undefined") {
  const legacyOutputDirKey = "epub-tool.output-dir";
  const hasTaskSpecificOutputDir = taskSections.some((taskType) =>
    Boolean(outputDirs.value[taskType]),
  );

  if (!hasTaskSpecificOutputDir) {
    try {
      const rawLegacyOutputDir = window.localStorage.getItem(legacyOutputDirKey);
      if (rawLegacyOutputDir) {
        const parsedLegacyOutputDir = JSON.parse(rawLegacyOutputDir);
        if (typeof parsedLegacyOutputDir === "string" && parsedLegacyOutputDir.trim()) {
          outputDirs.value = createTaskRecord(() => parsedLegacyOutputDir);
        }
      }
    } catch {
      // 忽略旧版单值配置解析失败，继续使用当前默认值。
    }
  }

  window.localStorage.removeItem(legacyOutputDirKey);
}

const hasAggregateStats = Object.values(taskAggregateStats.value).some((value) => value > 0);
if (!hasAggregateStats && taskHistory.value.length > 0) {
  taskAggregateStats.value = taskHistory.value.reduce<TaskAggregateStats>(
    (accumulator, entry) => ({
      total: accumulator.total + entry.summary.total,
      success: accumulator.success + entry.summary.success,
      failed: accumulator.failed + entry.summary.failed,
      skipped: accumulator.skipped + entry.summary.skipped,
    }),
    { ...defaultTaskAggregateStats },
  );
}

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
const outputDir = computed<string>({
  get: () => (activeTask.value ? outputDirs.value[activeTask.value] : ""),
  set: (value) => {
    if (!activeTask.value) {
      return;
    }
    outputDirs.value[activeTask.value] = value;
  },
});
const files = computed<QueuedFile[]>({
  get: () => (activeTask.value ? taskFilesByType.value[activeTask.value] : []),
  set: (value) => {
    if (activeTask.value) {
      taskFilesByType.value[activeTask.value] = value;
    }
  },
});
const selectedFilePath = computed<string>({
  get: () => (activeTask.value ? selectedFilePathByType.value[activeTask.value] : ""),
  set: (value) => {
    if (activeTask.value) {
      selectedFilePathByType.value[activeTask.value] = value;
    }
  },
});
const logs = computed<TaskEvent[]>({
  get: () => (activeTask.value ? taskLogsByType.value[activeTask.value] : []),
  set: (value) => {
    if (activeTask.value) {
      taskLogsByType.value[activeTask.value] = value;
    }
  },
});
const result = computed<TaskResult | null>({
  get: () => (activeTask.value ? taskResultByType.value[activeTask.value] : null),
  set: (value) => {
    if (activeTask.value) {
      taskResultByType.value[activeTask.value] = value;
    }
  },
});
const activeTaskLabel = computed(() => {
  if (!activeTask.value) {
    return "";
  }
  return formatTaskType(activeTask.value);
});
const masonryColumnsCount = computed(() => {
  const width = masonryBoardWidth.value;
  if (width >= 760) {
    return 2;
  }
  return 1;
});
const masonryCards = computed<MasonryCard[]>(() => {
  if (!isTaskSection.value) {
    return [];
  }

  const isSingleColumn = masonryColumnsCount.value === 1;

  // 单列顺序：这里改
  if (isSingleColumn) {
    const cards: MasonryCard[] = [
      { key: "task-config", weight: 380 },
      { key: "file-queue", weight: 300 },
      { key: "task-result", weight: 220 },
      { key: "task-log", weight: 320 },
    ];

    if (activeTask.value === "font_encrypt") {
      cards.splice(2, 0, { key: "font-panel", weight: 340 });
    }

    return cards;
  }

  // 双列顺序：这里保留你现在的列偏好逻辑
  const cards: MasonryCard[] = [
    { key: "task-config", weight: 380, preferredColumn: 0 },
    { key: "file-queue", weight: 300, preferredColumn: 1 },
    { key: "task-log", weight: 320, preferredColumn: 0 },
    { key: "task-result", weight: 220 },
  ];

  if (activeTask.value === "font_encrypt") {
    cards.splice(2, 0, { key: "font-panel", weight: 340, preferredColumn: 0 });
  }

  return cards;
});
const masonryColumns = computed<MasonryCard[][]>(() => {
  const columnCount = masonryColumnsCount.value;
  const columns: MasonryCard[][] = Array.from({ length: columnCount }, () => []);
  const columnHeights = Array.from({ length: columnCount }, () => 0);

  for (const card of masonryCards.value) {
    let targetIndex = 0;

    if (
      columnCount > 1 &&
      typeof card.preferredColumn === "number" &&
      card.preferredColumn >= 0 &&
      card.preferredColumn < columnCount
    ) {
      targetIndex = card.preferredColumn;
    } else {
      let minHeight = columnHeights[0] ?? 0;
      for (let index = 1; index < columnHeights.length; index += 1) {
        const currentHeight = columnHeights[index] ?? 0;
        if (currentHeight < minHeight) {
          minHeight = currentHeight;
          targetIndex = index;
        }
      }
    }

    columns[targetIndex]?.push(card);
    columnHeights[targetIndex] += card.weight;
  }

  return columns;
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
const runningTaskLabel = computed(() => {
  if (!runningTaskType.value) {
    return "";
  }

  return formatTaskType(runningTaskType.value);
});
const outputDirectorySummary = computed(() =>
  taskSections.map((taskType) => ({
    taskType,
    label: formatTaskType(taskType),
    path: outputDirs.value[taskType] || "源文件同级目录",
  })),
);
const aboutStats = computed(() => taskAggregateStats.value);
const aboutAnimatedStats = ref<TaskAggregateStats>({ ...defaultTaskAggregateStats });
const aboutAnimationProgress = ref(1);
let aboutAnimationFrame = 0;
const aboutSummary = computed(() => {
  const { total, success, skipped, failed } = aboutStats.value;
  if (total === 0) {
    return "当前还没有累计处理记录，开始执行任务后会在这里展示统计结果。";
  }
  return `累计已处理 ${total} 本 EPUB，其中成功 ${success} 本，跳过 ${skipped} 本，失败 ${failed} 本。`;
});
const settingsStatusItems = computed(() => [
  {
    label: "当前版本",
    value: `v${currentVersion.value}`,
  },
  {
    label: "更新状态",
    value: updateStatusLabel.value,
  },
  {
    label: "自动检查更新",
    value: settings.value.autoCheckUpdates ? "已开启" : "已关闭",
  },
  {
    label: "自动打开输出目录",
    value: settings.value.autoOpenOutputFolder ? "已开启" : "已关闭",
  },
  {
    label: "自动打开日志",
    value: settings.value.autoOpenLogFile ? "已开启" : "已关闭",
  },
  {
    label: "历史记录条数",
    value: `${historyLimit.value} 条`,
  },
]);
const aboutHasStats = computed(() => aboutStats.value.total > 0);
const toPercentText = (value: number, total: number): string => {
  if (total <= 0) {
    return "0%";
  }
  return `${((value / total) * 100).toFixed(1)}%`;
};
const aboutMetricDistribution = computed(() => {
  const total = Math.max(aboutStats.value.total, 1);
  const minimumWeight = Math.max(total * 0.16, 1);
  const interpolateWeight = (value: number) => {
    const targetWeight = Math.max(value, minimumWeight);
    const currentWeight = 1 + (targetWeight - 1) * aboutAnimationProgress.value;
    return {
      flexGrow: currentWeight.toFixed(3),
      flexShrink: "1",
      flexBasis: "0px",
    };
  };

  return {
    success: {
      percent: toPercentText(aboutStats.value.success, aboutStats.value.total),
      style: interpolateWeight(aboutStats.value.success),
    },
    skipped: {
      percent: toPercentText(aboutStats.value.skipped, aboutStats.value.total),
      style: interpolateWeight(aboutStats.value.skipped),
    },
    failed: {
      percent: toPercentText(aboutStats.value.failed, aboutStats.value.total),
      style: interpolateWeight(aboutStats.value.failed),
    },
  };
});
const aboutChartStyle = computed(() => {
  const total = aboutStats.value.total;
  if (total <= 0) {
    return {
      background: "conic-gradient(rgba(20, 33, 61, 0.08) 0deg 360deg)",
    };
  }

  const successRatio = aboutStats.value.success / total;
  const skippedRatio = aboutStats.value.skipped / total;
  const failedRatio = aboutStats.value.failed / total;
  const baseRatio = 1 / 3;
  const progress = aboutAnimationProgress.value;

  const successDeg =
    (baseRatio + (successRatio - baseRatio) * progress) * 360;
  const skippedDeg =
    (baseRatio + (skippedRatio - baseRatio) * progress) * 360;
  const failedDeg = Math.max(360 - successDeg - skippedDeg, 0);

  return {
    background: `conic-gradient(
      rgba(188, 227, 208, 0.98) 0deg ${successDeg}deg,
      rgba(247, 220, 166, 0.98) ${successDeg}deg ${successDeg + skippedDeg}deg,
      rgba(244, 191, 183, 0.98) ${successDeg + skippedDeg}deg ${successDeg + skippedDeg + failedDeg}deg,
      rgba(20, 33, 61, 0.08) ${successDeg + skippedDeg + failedDeg}deg 360deg
    )`,
  };
});

const animateAboutDashboard = () => {
  if (typeof window === "undefined") {
    aboutAnimatedStats.value = { ...aboutStats.value };
    return;
  }

  if (prefersReducedMotion.value) {
    aboutAnimatedStats.value = { ...aboutStats.value };
    aboutAnimationProgress.value = 1;
    return;
  }

  if (aboutAnimationFrame) {
    window.cancelAnimationFrame(aboutAnimationFrame);
    aboutAnimationFrame = 0;
  }

  const target = { ...aboutStats.value };
  const start =
    activeSection.value === "about"
      ? { ...defaultTaskAggregateStats }
      : { ...aboutAnimatedStats.value };
  aboutAnimationProgress.value = activeSection.value === "about" ? 0 : 1;
  const duration = 1580;
  const startedAt = performance.now();

  const step = (now: number) => {
    const progressValue = Math.min((now - startedAt) / duration, 1);
    const easedProgress =
      progressValue < 0.5
        ? 4 * progressValue * progressValue * progressValue
        : 1 - Math.pow(-2 * progressValue + 2, 3) / 2;
    aboutAnimationProgress.value = easedProgress;

    aboutAnimatedStats.value = {
      total: Math.round(start.total + (target.total - start.total) * easedProgress),
      success: Math.round(start.success + (target.success - start.success) * easedProgress),
      failed: Math.round(start.failed + (target.failed - start.failed) * easedProgress),
      skipped: Math.round(start.skipped + (target.skipped - start.skipped) * easedProgress),
    };

    if (progressValue < 1) {
      aboutAnimationFrame = window.requestAnimationFrame(step);
      return;
    }

    aboutAnimatedStats.value = target;
    aboutAnimationProgress.value = 1;
    aboutAnimationFrame = 0;
  };

  aboutAnimationFrame = window.requestAnimationFrame(step);
};
const isViewingRunningTask = computed(
  () => taskRunning.value && !!activeTask.value && activeTask.value === runningTaskType.value,
);
const isOtherTaskRunning = computed(
  () => taskRunning.value && !!runningTaskType.value && !isViewingRunningTask.value,
);

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
      return "集中管理版本更新、使用偏好、日志入口与最近任务。";
    default:
      return "查看功能范围、输出规则和日志位置说明。";
  }
});

const visibleProgressValue = computed(() => {
  if (isViewingRunningTask.value) {
    return Math.max(0, Math.min(100, progress.value));
  }

  if (fontLoading.value && fontProgressTotal.value > 0) {
    return Math.round((fontProgressCurrent.value / fontProgressTotal.value) * 100);
  }

  return 0;
});

const visibleProgressText = computed(() => {
  if (isViewingRunningTask.value && taskProgressTotal.value > 0) {
    return `已完成 ${taskProgressCurrent.value}/${taskProgressTotal.value} 本`;
  }

  if (fontLoading.value && fontProgressTotal.value > 0) {
    return `已完成 ${fontProgressCurrent.value}/${fontProgressTotal.value} 本`;
  }

  return "";
});

const visibleProgressMessage = computed(() => {
  if (isViewingRunningTask.value && taskProgressFileName.value) {
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

const syncTaskQueueWithFinishedFile = (
  taskType: TaskType,
  finishedPath: string,
  keepInQueue: boolean,
) => {
  if (keepInQueue) {
    return;
  }

  const snapshot = [...taskFilesByType.value[taskType]];
  const currentSelectedPath = selectedFilePathByType.value[taskType];
  const nextSelectedPath =
    currentSelectedPath === finishedPath
      ? pickNeighborPath(snapshot, finishedPath)
      : currentSelectedPath;

  taskFilesByType.value[taskType] = snapshot.filter((item) => item.path !== finishedPath);

  if (
    !taskFilesByType.value[taskType].some(
      (item) => item.path === selectedFilePathByType.value[taskType],
    )
  ) {
    selectedFilePathByType.value[taskType] = nextSelectedPath;
  }
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

const syncPersistedUpdateStateWithCurrentVersion = () => {
  if (!latestVersion.value) {
    return;
  }

  if (compareVersions(latestVersion.value, currentVersion.value) > 0) {
    return;
  }

  updateStatus.value = "latest";
  updateMessage.value = `当前已是最新版本 v${currentVersion.value}。`;
  updateNoticeVisible.value = false;
  persistUpdateCheckState();
};

const checkForUpdates = async (options?: { silent?: boolean; automatic?: boolean }) => {
  if (options?.automatic && shouldSkipAutomaticUpdateCheck()) {
    syncPersistedUpdateStateWithCurrentVersion();
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
  const addedPaths: string[] = [];
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
    addedPaths.push(path);
    if (!firstQueuedPath) {
      firstQueuedPath = path;
    }
  }

  if (hadSelectedFile) {
    ensureSelectedFile();
  } else {
    ensureSelectedFile(firstQueuedPath);
  }

  if (activeSection.value === "font_encrypt" && addedPaths.length > 0) {
    void nextTick(async () => {
      await loadFontFamilies({
        filePaths: addedPaths,
        silent: true,
      });
    });
  }
};

const resolveAndQueuePaths = async (paths: string[]) => {
  const normalized = paths.map((path) => path.trim()).filter(Boolean);
  if (normalized.length === 0) {
    return;
  }

  if (isTauriRuntime()) {
    const resolved = await resolveInputSources(normalized);
    queuePaths(resolved);
    return;
  }

  queuePaths(normalized);
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

  await resolveAndQueuePaths(Array.isArray(selected) ? selected : [selected]);
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

const handleDropZoneFiles = (droppedFiles: File[]) => {
  if (isTauriRuntime()) {
    return;
  }

  queuePaths(droppedFiles.map((file) => file.name));
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
  const pendingTargets = options?.force
    ? targets
    : targets.filter((item) => item.fontLoadStatus !== "loaded");
  if (pendingTargets.length === 0) {
    return;
  }

  const requestId = ++fontLoadRequestId;
  fontLoading.value = true;
  fontProgressCurrent.value = 0;
  fontProgressTotal.value = pendingTargets.length;
  fontProgressFileName.value = "";
  let refreshedCount = 0;
  let emptyCount = 0;
  let errorCount = 0;
  let completedCount = 0;

  try {
    for (const item of pendingTargets) {
      if (requestId !== fontLoadRequestId) {
        return;
      }
      fontProgressFileName.value = item.name;

      const previousStatus = item.fontLoadStatus;
      item.fontLoadStatus = "loading";
      item.fontLoadError = "";

      try {
        const families = await listFontTargets(item.path);
        if (requestId !== fontLoadRequestId) {
          return;
        }
        syncFontFamilies(item, families, previousStatus);
        item.fontLoadStatus = "loaded";
        refreshedCount += 1;
        if (families.length === 0) {
          emptyCount += 1;
        }
      } catch (error) {
        if (requestId !== fontLoadRequestId) {
          return;
        }
        item.fontFamilies = [];
        item.selectedFontFamilies = [];
        item.fontLoadStatus = "error";
        item.fontLoadError = toErrorMessage(error, "读取字体列表失败，请重试。");
        errorCount += 1;
      }

      completedCount += 1;
      fontProgressCurrent.value = completedCount;
    }
  } finally {
    if (requestId === fontLoadRequestId) {
      fontLoading.value = false;
      fontProgressCurrent.value = 0;
      fontProgressTotal.value = 0;
      fontProgressFileName.value = "";
    }
  }

  if (requestId !== fontLoadRequestId) {
    return;
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
  taskAggregateStats.value = {
    total: taskAggregateStats.value.total + taskResult.summary.total,
    success: taskAggregateStats.value.success + taskResult.summary.success,
    failed: taskAggregateStats.value.failed + taskResult.summary.failed,
    skipped: taskAggregateStats.value.skipped + taskResult.summary.skipped,
  };
};

const syncQueueWithResult = (taskType: TaskType, taskResult: TaskResult) => {
  const snapshot = [...taskFilesByType.value[taskType]];
  const nextSelectedPath = pickNeighborPath(snapshot, selectedFilePathByType.value[taskType]);
  const blockedPaths = new Set(taskResult.errors.map((item) => item.input_file));

  taskFilesByType.value[taskType] = snapshot.filter((item) => blockedPaths.has(item.path));
  if (
    !taskFilesByType.value[taskType].some(
      (item) => item.path === selectedFilePathByType.value[taskType],
    )
  ) {
    selectedFilePathByType.value[taskType] = nextSelectedPath;
    return;
  }

  if (!taskFilesByType.value[taskType].some((item) => item.path === nextSelectedPath)) {
    return;
  }
};

const createRunningTaskResult = (total: number): TaskResult => ({
  ok: false,
  status: "running",
  outputs: [],
  errors: [],
  skipped: [],
  summary: {
    total,
    success: 0,
    failed: 0,
    skipped: 0,
  },
  log_path: null,
});

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
  const targetTaskType = runningTaskType.value;
  if (targetTaskType) {
    taskLogsByType.value[targetTaskType] = [...taskLogsByType.value[targetTaskType], event].slice(
      -300,
    );
  }
  progress.value = event.progress;
  taskStatus.value = event.message;
  if (event.event === "task.started") {
    taskProgressCurrent.value = 0;
  } else if (event.event === "task.file.started") {
    taskProgressCurrent.value = Math.max((event.current_index ?? 1) - 1, 0);
  } else if (event.event === "task.file.finished") {
    taskProgressCurrent.value = event.current_index ?? taskProgressCurrent.value;
  } else if (event.event === "task.finished") {
    taskProgressCurrent.value =
      event.result?.summary.total ?? event.total_files ?? taskProgressCurrent.value;
  }
  taskProgressTotal.value = event.total_files ?? taskProgressTotal.value;
  taskProgressFileName.value = event.current_file
    ? event.current_file.split(/[\\/]/).pop() ?? event.current_file
    : taskProgressFileName.value;
  if (targetTaskType && event.event === "task.file.finished" && event.current_file) {
    const currentResult =
      taskResultByType.value[targetTaskType] ??
      createRunningTaskResult(event.total_files ?? taskProgressTotal.value);
    const nextResult: TaskResult = {
      ...currentResult,
      outputs: [...currentResult.outputs],
      errors: [...currentResult.errors],
      skipped: [...currentResult.skipped],
      summary: { ...currentResult.summary },
    };

    if (event.status === "success") {
      nextResult.summary.success += 1;
      if (event.output_path) {
        nextResult.outputs.push(event.output_path);
      }
    } else if (event.status === "skip") {
      nextResult.summary.skipped += 1;
      nextResult.skipped.push({
        input_file: event.current_file,
        message: event.message,
      });
    } else if (event.status === "error") {
      nextResult.summary.failed += 1;
      nextResult.errors.push({
        input_file: event.current_file,
        message: event.message,
      });
    }

    taskResultByType.value[targetTaskType] = nextResult;
    syncTaskQueueWithFinishedFile(
      targetTaskType,
      event.current_file,
      event.status === "error",
    );
  }
  if (targetTaskType && event.result) {
    taskResultByType.value[targetTaskType] = event.result;
  }
};

const runSelectedTask = async () => {
  if (files.value.length === 0 || taskRunning.value || !activeTask.value) {
    return;
  }
  const taskType = activeTask.value;
  if (taskType === "font_encrypt") {
    await loadFontFamilies();
  }

  taskRunning.value = true;
  runningTaskType.value = taskType;
  progress.value = 0;
  taskProgressCurrent.value = 0;
  taskProgressTotal.value = files.value.length;
  taskProgressFileName.value = "";
  taskLogsByType.value[taskType] = [];
  taskResultByType.value[taskType] = createRunningTaskResult(files.value.length);
  const request = buildRequest();

  try {
    const taskResult = await runTask(request, pushLog);
    taskResultByType.value[taskType] = taskResult;
    syncQueueWithResult(taskType, taskResult);
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
    runningTaskType.value = null;
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

const openCurrentLogDirectory = () => {
  if (!currentLogPath.value) {
    return;
  }

  void openPath(getContainingDirectory(currentLogPath.value));
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
  if (activeSection.value === "about") {
    animateAboutDashboard();
  }
  if (activeSection.value === "font_encrypt" && files.value.length > 0) {
    await loadFontFamilies({
      filePaths: files.value
        .filter((item) => item.fontLoadStatus !== "loaded")
        .map((item) => item.path),
      silent: true,
    });
  }
  await measureMasonryBoard();
}, { flush: "post" });

watch(activeTask, async () => {
  await measureMasonryBoard();
}, { flush: "post" });

watch(() => masonryBoardRef.value, async () => {
  await measureMasonryBoard();
}, { flush: "post" });

watch(() => workspaceRef.value, async () => {
  await nextTick();
  updateAllCustomScrollbars();
}, { flush: "post" });

watch(() => sideNavShellRef.value, async () => {
  await nextTick();
  updateAllCustomScrollbars();
}, { flush: "post" });

watch(() => workspaceScrollbarTrackRef.value, async () => {
  await nextTick();
  updateAllCustomScrollbars();
}, { flush: "post" });

watch(() => sideNavScrollbarTrackRef.value, async () => {
  await nextTick();
  updateAllCustomScrollbars();
}, { flush: "post" });

watch(
  () => files.value.map((item) => item.path).join("|"),
  async (currentPaths, previousPaths) => {
    ensureSelectedFile();
    if (activeSection.value === "font_encrypt") {
      const previous = new Set(
        previousPaths
          .split("|")
          .map((path) => path.trim())
          .filter(Boolean),
      );
      const addedPaths = currentPaths
        .split("|")
        .map((path) => path.trim())
        .filter((path) => path && !previous.has(path));

      if (addedPaths.length === 0) {
        return;
      }

      await loadFontFamilies({
        filePaths: addedPaths,
        silent: true,
      });
    }
    await nextTick();
    updateAllCustomScrollbars();
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
  () => [logs.value.length, result.value, activeSection.value, activeTask.value],
  async () => {
    await nextTick();
    updateAllCustomScrollbars();
  },
  { deep: true },
);

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

watch(
  aboutStats,
  () => {
    if (activeSection.value === "about") {
      animateAboutDashboard();
      return;
    }
    aboutAnimatedStats.value = { ...aboutStats.value };
  },
  { deep: true },
);

let unlistenDrop: (() => void) | null = null;
let motionMediaQuery: MediaQueryList | null = null;
let removeMotionPreferenceListener: (() => void) | null = null;
let removeMasonryResizeListener: (() => void) | null = null;
let removeCustomScrollbarResizeListener: (() => void) | null = null;

onMounted(async () => {
  if (typeof window !== "undefined") {
    motionMediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    prefersReducedMotion.value = motionMediaQuery.matches;
    const handleMotionPreferenceChange = (event: MediaQueryListEvent) => {
      prefersReducedMotion.value = event.matches;
      if (event.matches) {
        aboutAnimatedStats.value = { ...aboutStats.value };
      }
    };

    if (typeof motionMediaQuery.addEventListener === "function") {
      motionMediaQuery.addEventListener("change", handleMotionPreferenceChange);
      removeMotionPreferenceListener = () =>
        motionMediaQuery?.removeEventListener("change", handleMotionPreferenceChange);
    } else {
      motionMediaQuery.addListener(handleMotionPreferenceChange);
      removeMotionPreferenceListener = () =>
        motionMediaQuery?.removeListener(handleMotionPreferenceChange);
    }

    if (typeof ResizeObserver !== "undefined") {
      masonryResizeObserver = new ResizeObserver(() => {
        void measureMasonryBoard();
      });
      window.addEventListener("resize", handleMasonryWindowResize);
      removeMasonryResizeListener = () => {
        masonryResizeObserver?.disconnect();
        masonryResizeObserver = null;
        window.removeEventListener("resize", handleMasonryWindowResize);
      };
    } else {
      window.addEventListener("resize", handleMasonryWindowResize);
      removeMasonryResizeListener = () => {
        window.removeEventListener("resize", handleMasonryWindowResize);
      };
    }

    const handleCustomScrollbarViewportResize = () => {
      updateAllCustomScrollbars();
    };
    window.addEventListener("resize", handleCustomScrollbarViewportResize);
    removeCustomScrollbarResizeListener = () => {
      window.removeEventListener("resize", handleCustomScrollbarViewportResize);
    };

    if (typeof ResizeObserver !== "undefined") {
      customScrollbarResizeObserver = new ResizeObserver(() => {
        updateAllCustomScrollbars();
      });
    }
  }

  sideNavShellRef.value?.addEventListener("scroll", updateSideNavScrollbar, { passive: true });
  workspaceRef.value?.addEventListener("scroll", updateWorkspaceScrollbar, { passive: true });
  if (customScrollbarResizeObserver) {
    if (sideNavShellRef.value) {
      customScrollbarResizeObserver.observe(sideNavShellRef.value);
    }
    if (workspaceRef.value) {
      customScrollbarResizeObserver.observe(workspaceRef.value);
    }
    if (sideNavScrollbarTrackRef.value) {
      customScrollbarResizeObserver.observe(sideNavScrollbarTrackRef.value);
    }
    if (workspaceScrollbarTrackRef.value) {
      customScrollbarResizeObserver.observe(workspaceScrollbarTrackRef.value);
    }
  }
  aboutAnimatedStats.value = { ...aboutStats.value };
  if (isTauriRuntime()) {
    try {
      currentVersion.value = normalizeVersion(await getVersion());
      syncPersistedUpdateStateWithCurrentVersion();
    } catch {
      currentVersion.value = CURRENT_FALLBACK_VERSION;
    }
    try {
      currentLogPath.value = await getLogPath();
    } catch {
      currentLogPath.value = "";
    }
  }

  if (settings.value.autoCheckUpdates) {
    void checkForUpdates({ silent: true, automatic: true });
  }

  if (!isTauriRuntime()) {
    await measureMasonryBoard();
    await nextTick();
    updateAllCustomScrollbars();
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
      void resolveAndQueuePaths(event.payload.paths);
      return;
    }
    dragActive.value = false;
  });
  await nextTick();
  updateAllCustomScrollbars();
  await measureMasonryBoard();
});

onBeforeUnmount(() => {
  if (aboutAnimationFrame && typeof window !== "undefined") {
    window.cancelAnimationFrame(aboutAnimationFrame);
  }
  removeMotionPreferenceListener?.();
  removeMasonryResizeListener?.();
  removeCustomScrollbarResizeListener?.();
  sideNavShellRef.value?.removeEventListener("scroll", updateSideNavScrollbar);
  workspaceRef.value?.removeEventListener("scroll", updateWorkspaceScrollbar);
  customScrollbarResizeObserver?.disconnect();
  customScrollbarResizeObserver = null;
  unlistenDrop?.();
});

activeSection.value = normalizeSectionKey(activeSection.value);
</script>

<template>
  <div class="app-shell">
    <!-- <SideNav :active="activeSection" :items="sectionItems" @select="activeSection = $event" /> -->
    <div class="side-nav-frame">
      <div ref="sideNavShellRef" class="side-nav-shell">
        <SideNav :active="activeSection" :items="sectionItems" @select="activeSection = $event" />
      </div>
      <div
        ref="sideNavScrollbarTrackRef"
        class="custom-scrollbar custom-scrollbar-side"
        :class="{ visible: sideNavScrollbarVisible }"
        aria-hidden="true">
        <div class="custom-scrollbar-thumb" :style="{
          height: `${sideNavScrollbarThumbHeight}px`,
          transform: `translateY(${sideNavScrollbarThumbTop}px)`,
        }" />
      </div>
    </div>

    <!-- <main class="workspace"> -->
    <div class="workspace-frame">
      <div ref="workspaceRef" class="workspace-shell">
        <main class="workspace">
          <!-- <main ref="workspaceRef" class="workspace"> -->
          <section v-if="updateNoticeVisible && updateStatus === 'available'"
            class="update-toast glass-strong workspace-animated-block">
            <div class="update-toast-copy">
              <p class="eyebrow">版本更新</p>
              <strong class="content-animated-value">发现新版本 v{{ latestVersion }}</strong>
              <p class="content-animated-value">{{ updateMessage }}</p>
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

          <header class="workspace-header workspace-animated-header">
            <div>
              <p class="eyebrow">{{ headerEyebrow }}</p>
              <h2 class="content-animated-value">{{ activeTitle }}</h2>
              <p class="muted content-animated-value">{{ activeDescription }}</p>
            </div>
          </header>

          <template v-if="isTaskSection">
            <DropZone :is-active="dragActive" :file-count="files.length" @drag-state="dragActive = $event"
              @drop-files="handleDropZoneFiles" @pick-files="pickFiles" @scan-directory="scanInputDirectory"
              @clear="clearFiles" />

            <section ref="masonryBoardRef" class="masonry-board content-animated-grid"
              :style="{ '--masonry-columns': String(masonryColumnsCount) }">
              <div v-for="(column, columnIndex) in masonryColumns" :key="`masonry-col-${columnIndex}`"
                class="masonry-column">
                <template v-for="card in column" :key="card.key">
                  <article v-if="card.key === 'task-config'"
                    class="panel task-panel glass-medium content-animated-block">
                    <div class="panel-head">
                      <div>
                        <p class="eyebrow">任务配置</p>
                        <h3>输出与执行</h3>
                      </div>
                      <div class="panel-actions">
                        <button class="ghost-btn task-action-btn" type="button" @click="pickOutputDirectory">
                          选择输出目录
                        </button>
                        <button class="ghost-btn task-action-btn" type="button" @click="resetOutput">
                          重置输出路径
                        </button>
                      </div>
                    </div>

                    <div class="settings-stack">
                      <label class="field glass-soft task-field-card">
                        <span>输出目录</span>
                        <input :value="outputDir || '默认：源文件同级目录'" readonly type="text" />
                      </label>

                      <div class="task-callout glass-soft">
                        <strong class="content-animated-value">{{ activeTaskLabel }}</strong>
                        <span class="content-animated-value">{{ activeTaskDescription }}</span>
                      </div>

                      <div v-if="taskRunning || fontLoading" class="task-progress glass-soft">
                        <div class="task-progress-head">
                          <strong class="content-animated-value">{{ visibleProgressText }}</strong>
                          <span class="content-animated-value">{{ visibleProgressValue }}%</span>
                        </div>
                        <div class="task-progress-track">
                          <div class="task-progress-fill" :style="{ width: `${visibleProgressValue}%` }" />
                        </div>
                        <p v-if="visibleProgressMessage" class="task-progress-message">
                          <span class="content-animated-value">{{ visibleProgressMessage }}</span>
                        </p>
                      </div>

                      <button class="primary-btn wide" :disabled="taskRunning || files.length === 0" type="button"
                        @click="runSelectedTask">
                        {{ taskRunning ? "处理中..." : "开始执行" }}
                      </button>
                    </div>
                  </article>

                  <section v-else-if="card.key === 'font-panel'"
                    class="panel font-panel glass-medium content-animated-block">
                    <div class="panel-head">
                      <div>
                        <p class="eyebrow">字体范围</p>
                        <h3>文件级选择</h3>
                        <p class="muted">
                          {{ selectedFile ? `当前聚焦：${selectedFile.name}` : "先从右侧队列选择一本 EPUB。" }}
                        </p>
                      </div>
                      <div class="panel-actions">
                        <button class="ghost-btn task-action-btn" :disabled="fontLoading || files.length === 0"
                          type="button" @click="loadFontFamilies({ force: true })">
                          {{ fontLoading ? "刷新中..." : "刷新字体列表" }}
                        </button>
                      </div>
                    </div>
                    <div class="font-grid">
                      <div v-if="!selectedFile" class="empty-state">
                        队列为空，暂时没有可配置的字体目标。
                      </div>
                      <div v-else class="font-card font-card-focus glass-soft">
                        <strong>{{ selectedFile.name }}</strong>
                        <p>{{ selectedFile.fontFamilies.length }} 个可选字体</p>
                        <label v-for="family in selectedFile.fontFamilies" :key="`${selectedFile.path}-${family}`"
                          class="font-option">
                          <input :disabled="selectedFile.fontLoadStatus === 'loading'"
                            :checked="selectedFile.selectedFontFamilies.includes(family)" type="checkbox"
                            @change="toggleFontFamily(selectedFile.path, family)" />
                          <span>{{ family }}</span>
                        </label>
                        <p v-if="selectedFileFontMessage" class="muted">
                          {{ selectedFileFontMessage }}
                        </p>
                      </div>
                    </div>
                  </section>

                  <article v-else-if="card.key === 'file-queue'"
                    class="panel task-panel glass-medium content-animated-block">
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
                      <div v-for="file in files" :key="file.path" class="file-row"
                        :class="{ active: file.path === selectedFilePath }">
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

                  <article v-else-if="card.key === 'task-log'"
                    class="panel panel-console glass-medium content-animated-block">
                    <div class="panel-head">
                      <div>
                        <p class="eyebrow">过程</p>
                        <h3>处理日志</h3>
                      </div>
                      <div class="panel-actions">
                        <button class="ghost-btn task-action-btn" type="button" @click="openLogFile">
                          打开处理日志
                        </button>
                        <button class="ghost-btn task-action-btn" type="button" @click="clearLogs">
                          清空面板
                        </button>
                      </div>
                    </div>
                    <div class="log-list">
                      <div v-if="logs.length === 0" class="log-empty">尚未执行任务。</div>
                      <div v-for="(log, index) in [...logs].reverse()" :key="`${log.event}-${index}`"
                        class="log-row glass-soft" :class="log.level ?? log.status">
                        <span class="log-event">{{ log.event }}</span>
                        <span class="log-message">{{ log.message }}</span>
                      </div>
                    </div>
                  </article>

                  <article v-else-if="card.key === 'task-result'"
                    class="panel panel-result glass-medium content-animated-block">
                    <div class="panel-head">
                      <div>
                        <p class="eyebrow">结果</p>
                        <h3>最近一次执行摘要</h3>
                      </div>
                    </div>
                    <div v-if="!result" class="result-empty">还没有可展示的执行结果。</div>
                    <div v-else class="result-block">
                      <div class="result-metrics">
                        <div class="result-metric-card total">
                          <strong class="content-animated-value">{{ result.summary.total }}</strong>
                          <span>总文件</span>
                        </div>
                        <div class="result-metric-card success">
                          <strong class="content-animated-value">{{ result.summary.success }}</strong>
                          <span>成功</span>
                        </div>
                        <div class="result-metric-card error">
                          <strong class="content-animated-value">{{ result.summary.failed }}</strong>
                          <span>失败</span>
                        </div>
                        <div class="result-metric-card skip">
                          <strong class="content-animated-value">{{ result.summary.skipped }}</strong>
                          <span>跳过</span>
                        </div>
                      </div>

                      <div v-if="result.outputs.length > 0" class="result-detail-block">
                        <div class="result-detail-head">
                          <strong>成功</strong>
                          <span class="content-animated-value">{{ result.outputs.length }} 项</span>
                        </div>
                        <div class="result-output-list">
                          <button v-for="output in result.outputs" :key="output"
                            class="result-output-row success glass-soft" type="button"
                            @click="openOutputFolder(output)">
                            <div class="result-row-head">
                              <strong>{{ output.split(/[\\/]/).pop() ?? output }}</strong>
                              <span class="result-status-tag success">成功</span>
                            </div>
                            <span>{{ output }}</span>
                          </button>
                        </div>
                      </div>

                      <div v-if="result.errors.length > 0" class="result-detail-block">
                        <div class="result-detail-head">
                          <strong>失败</strong>
                          <span class="content-animated-value">{{ result.errors.length }} 项</span>
                        </div>
                        <div class="result-detail-list">
                          <div v-for="item in result.errors" :key="`${item.input_file}-${item.message}`"
                            class="result-detail-row error glass-soft">
                            <div class="result-row-head">
                              <strong>{{ item.input_file.split(/[\\/]/).pop() ?? item.input_file }}</strong>
                              <span class="result-status-tag error">失败</span>
                            </div>
                            <p>{{ item.message }}</p>
                            <span>{{ item.input_file }}</span>
                          </div>
                        </div>
                      </div>

                      <div v-if="result.skipped.length > 0" class="result-detail-block">
                        <div class="result-detail-head">
                          <strong>跳过</strong>
                          <span class="content-animated-value">{{ result.skipped.length }} 项</span>
                        </div>
                        <div class="result-detail-list">
                          <div v-for="item in result.skipped" :key="`${item.input_file}-${item.message}`"
                            class="result-detail-row skip glass-soft">
                            <div class="result-row-head">
                              <strong>{{ item.input_file.split(/[\\/]/).pop() ?? item.input_file }}</strong>
                              <span class="result-status-tag skip">跳过</span>
                            </div>
                            <p>{{ item.message }}</p>
                            <span>{{ item.input_file }}</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </article>
                </template>
              </div>
            </section>
          </template>

          <!-- <section v-if="activeSection === 'settings'" class="panel settings-panel section-animated-panel"> -->
          <section v-if="activeSection === 'settings'" class="settings-panel section-animated-panel">
            <section class="settings-overview section-animated-block glass-medium">
              <div class="settings-block-head">
                <div>
                  <p class="eyebrow">状态总览</p>
                  <h3>当前设置状态</h3>
                </div>
              </div>
              <div class="settings-status-grid">
                <article v-for="item in settingsStatusItems" :key="item.label"
                  class="settings-status-card settings-interactive-card glass-medium">
                  <span>{{ item.label }}</span>
                  <strong :key="`${item.label}-${item.value}`" class="content-animated-value">
                    {{ item.value }}
                  </strong>
                </article>
              </div>
            </section>

            <section class="settings-block section-animated-block glass-medium">
              <div class="settings-block-head">
                <div>
                  <p class="eyebrow">更新</p>
                  <h3>版本更新</h3>
                </div>
                <div class="panel-actions">
                  <button class="ghost-btn settings-action-btn" :disabled="updateStatus === 'checking'" type="button"
                    @click="checkForUpdates()">
                    {{ updateStatus === "checking" ? "检查中..." : "检查更新" }}
                  </button>
                  <button class="ghost-btn settings-action-btn" type="button" @click="openLatestReleasePage">
                    下载最新版本
                  </button>
                </div>
              </div>
              <div class="settings-update-card settings-interactive-card glass-medium">
                <div class="settings-update-copy">
                  <strong :key="`current-version-${currentVersion}`" class="content-animated-value">
                    当前版本 v{{ currentVersion }}
                  </strong>
                  <span :key="`update-status-${updateStatusLabel}`" class="content-animated-value">
                    {{ updateStatusLabel }}
                  </span>
                  <span v-if="latestVersion" :key="`latest-version-${latestVersion}`" class="content-animated-value">
                    最新版本：v{{ latestVersion }}
                  </span>
                  <span v-if="updateCheckedAt" :key="`checked-at-${updateCheckedAt}`" class="content-animated-value">
                    最近检查：{{ formatUpdateTime(updateCheckedAt) }}
                  </span>
                </div>
              </div>
            </section>

            <section class="settings-block section-animated-block glass-medium">
              <div class="settings-block-head">
                <div>
                  <p class="eyebrow">偏好设置</p>
                  <h3>使用偏好</h3>
                </div>
              </div>
              <div class="settings-preference-grid">
                <label class="settings-preference-card settings-interactive-card glass-medium">
                  <div>
                    <strong>自动打开输出文件夹</strong>
                    <p>任务完成后直接定位到输出目录。</p>
                  </div>
                  <input v-model="settings.autoOpenOutputFolder" type="checkbox" />
                </label>
                <label class="settings-preference-card settings-interactive-card glass-medium">
                  <div>
                    <strong>自动打开处理日志</strong>
                    <p>便于立刻回看处理细节。</p>
                  </div>
                  <input v-model="settings.autoOpenLogFile" type="checkbox" />
                </label>
                <label class="settings-preference-card settings-interactive-card glass-medium">
                  <div>
                    <strong>启动时自动检查更新</strong>
                    <p>自动检查 GitHub Release 最新版本。</p>
                  </div>
                  <input v-model="settings.autoCheckUpdates" type="checkbox" />
                </label>
                <label
                  class="settings-preference-card settings-preference-card-number settings-interactive-card glass-medium">
                  <div>
                    <strong>历史记录条数</strong>
                    <p>控制最近任务列表的保留上限。</p>
                  </div>
                  <input v-model.number="settings.keepHistoryCount" min="1" max="30" type="number" />
                </label>
              </div>
            </section>

            <section class="settings-block section-animated-block glass-medium">
              <div class="settings-block-head">
                <div>
                  <p class="eyebrow">日志工具</p>
                  <h3>日志位置</h3>
                </div>
                <div class="panel-actions">
                  <button v-if="currentLogPath" class="ghost-btn settings-action-btn" type="button"
                    @click="openLogFile">
                    打开日志文件
                  </button>
                  <button v-if="currentLogPath" class="ghost-btn settings-action-btn" type="button"
                    @click="openCurrentLogDirectory">
                    打开日志目录
                  </button>
                </div>
              </div>
              <div class="settings-log-card settings-interactive-card glass-medium">
                <span>当前日志文件</span>
                <strong>{{ currentLogPath || "开发环境默认写入仓库根目录的 log.txt。" }}</strong>
              </div>
            </section>

            <section class="settings-block section-animated-block glass-medium">
              <div class="settings-block-head">
                <div>
                  <p class="eyebrow">历史</p>
                  <h3>最近任务</h3>
                </div>
                <div class="panel-actions">
                  <button class="ghost-btn settings-action-btn" type="button" @click="clearHistory">
                    清空历史
                  </button>
                </div>
              </div>
              <div class="history-list">
                <div v-if="recentHistory.length === 0" class="empty-state">
                  还没有已完成任务记录。
                </div>
                <div v-for="entry in recentHistory" :key="entry.id"
                  class="history-row settings-interactive-card glass-medium">
                  <div>
                    <strong>{{ formatTaskType(entry.taskType) }}</strong>
                    <p>
                      {{ formatHistoryTime(entry.createdAt) }} · {{ entry.status }} · 成功
                      {{ entry.summary.success }}/{{ entry.summary.total }}
                    </p>
                  </div>
                  <button v-if="entry.firstOutput" class="ghost-btn settings-action-btn" type="button"
                    @click="openOutputFolder(entry.firstOutput)">
                    打开输出文件夹
                  </button>
                </div>
              </div>
            </section>
          </section>

          <!-- <section v-if="activeSection === 'about'" class="panel about-panel glass-soft section-animated-panel"> -->
          <section v-if="activeSection === 'about'" class="about-panel section-animated-panel">
            <section class="about-dashboard section-animated-block">
              <article class="about-card glass-medium">
                <div class="about-dashboard-head">
                  <div class="about-card-head">
                    <p class="eyebrow">处理统计</p>
                    <h4>累计处理概览</h4>
                  </div>
                  <p class="muted">{{ aboutSummary }}</p>
                </div>
                <div class="about-dashboard-body">
                  <div class="about-chart-wrap">
                    <div class="about-chart glass-medium" :style="aboutChartStyle">
                      <div class="about-chart-core">
                        <strong class="content-animated-value">{{ aboutAnimatedStats.total }}</strong>
                        <span>累计处理</span>
                      </div>
                    </div>
                    <div class="about-chart-legend">
                      <span class="about-legend-item success">
                        <i />
                        成功
                      </span>
                      <span class="about-legend-item skip">
                        <i />
                        跳过
                      </span>
                      <span class="about-legend-item error">
                        <i />
                        失败
                      </span>
                    </div>
                  </div>
                  <div class="about-metric-stack">
                    <div class="about-metric about-metric-wide total glass-medium">
                      <strong class="content-animated-value">{{ aboutAnimatedStats.total }}</strong>
                      <span>总数</span>
                      <small>占比 100%</small>
                    </div>
                    <div class="about-metric-row">
                      <div class="about-metric success glass-medium" :style="aboutMetricDistribution.success.style">
                        <strong class="content-animated-value">{{ aboutAnimatedStats.success }}</strong>
                        <span>成功</span>
                        <small>占比 {{ aboutMetricDistribution.success.percent }}</small>
                      </div>
                      <div class="about-metric skip glass-medium" :style="aboutMetricDistribution.skipped.style">
                        <strong class="content-animated-value">{{ aboutAnimatedStats.skipped }}</strong>
                        <span>跳过</span>
                        <small>占比 {{ aboutMetricDistribution.skipped.percent }}</small>
                      </div>
                      <div class="about-metric error glass-medium" :style="aboutMetricDistribution.failed.style">
                        <strong class="content-animated-value">{{ aboutAnimatedStats.failed }}</strong>
                        <span>失败</span>
                        <small>占比 {{ aboutMetricDistribution.failed.percent }}</small>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-if="!aboutHasStats" class="about-dashboard-empty">
                  还没有累计处理记录。执行任意 EPUB 任务后，这里会自动开始统计。
                </div>
              </article>
            </section>

            <section class="about-hero glass-medium section-animated-block">
              <p class="eyebrow">软件说明</p>
              <h3>Epub Tool 能做什么</h3>
              <p class="muted">
                面向 EPUB 批量处理场景，当前提供五类处理能力，并统一支持文件导入、目录扫描、结果回看与日志定位。
              </p>
            </section>

            <section class="about-summary-grid section-animated-block">
              <article class="about-summary-card glass-medium">
                <strong>5 类功能</strong>
                <span>格式化、解密、加密、字体加密、图片转换</span>
              </article>
              <article class="about-summary-card glass-medium">
                <strong>3 种输入方式</strong>
                <span>单文件、多文件、目录扫描</span>
              </article>
              <article class="about-summary-card glass-medium">
                <strong>独立输出规则</strong>
                <span>每个子功能分别保存自己的默认输出目录</span>
              </article>
            </section>

            <section class="about-grid section-animated-block">
              <article class="about-card glass-medium">
                <div class="about-card-head">
                  <p class="eyebrow">功能范围</p>
                  <h4>支持的处理能力</h4>
                </div>
                <div class="about-list">
                  <span>支持格式化、文件解密、文件加密、字体加密和图片转换五类 EPUB 任务。</span>
                  <span>所有功能都支持单本、多本和目录扫描。</span>
                </div>
              </article>

              <article class="about-card glass-medium">
                <div class="about-card-head">
                  <p class="eyebrow">处理行为</p>
                  <h4>执行方式说明</h4>
                </div>
                <div class="about-list">
                  <span>字体加密支持按每本 EPUB 单独选择字体范围后再批量执行。</span>
                  <span>处理完成后可在结果区打开输出文件夹，并查看失败或跳过原因。</span>
                  <span>队列中存在多本文件时，当前文件处理完成后会自动切换到下一本。</span>
                </div>
              </article>
            </section>

            <article class="about-card glass-medium section-animated-block">
              <div class="about-card-head">
                <p class="eyebrow">输出规则</p>
                <h4>子功能输出目录</h4>
              </div>
              <p class="muted">每个子功能会独立保存自己的默认输出位置。</p>
              <div class="about-path-grid">
                <div v-for="item in outputDirectorySummary" :key="item.taskType" class="about-path-card glass-medium">
                  <strong>{{ item.label }}</strong>
                  <p>{{ item.path }}</p>
                </div>
              </div>
            </article>

            <article class="about-card glass-medium section-animated-block">
              <div class="about-card-head">
                <p class="eyebrow">日志说明</p>
                <h4>日志文件位置</h4>
              </div>
              <div class="about-list">
                <span v-if="currentLogPath">当前实际日志文件：{{ currentLogPath }}</span>
                <span v-else>开发环境默认写入仓库根目录的 log.txt，打包版写入系统应用日志目录。</span>
              </div>
              <div class="about-path-grid about-path-grid-compact">
                <div v-for="item in defaultLogPaths" :key="item.platform" class="about-path-card glass-medium">
                  <strong>{{ item.platform }}</strong>
                  <p>{{ item.path }}</p>
                </div>
              </div>
            </article>
          </section>
        </main>
      </div>
      <div
        ref="workspaceScrollbarTrackRef"
        class="custom-scrollbar custom-scrollbar-workspace"
        :class="{ visible: workspaceScrollbarVisible }"
        aria-hidden="true">
        <div class="custom-scrollbar-thumb" :style="{
          height: `${workspaceScrollbarThumbHeight}px`,
          transform: `translateY(${workspaceScrollbarThumbTop}px)`,
        }" />
      </div>
    </div>
  </div>
  <input ref="browserFileInput" accept=".epub" hidden multiple type="file" @change="handleBrowserFiles" />
</template>
