<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { open, save } from "@tauri-apps/plugin-dialog";

import DropZone from "./components/DropZone.vue";
import { useTaskBridge } from "./composables/useTaskBridge";
import type { EngineEvent, QueuedFile, TaskRequest, TaskResult, TaskType } from "./types";

const tasks: Array<{ type: TaskType; label: string; description: string }> = [
  { type: "reformat_epub", label: "文件重构", description: "重构 EPUB 目录与引用。" },
  { type: "decrypt_epub", label: "文件解密", description: "还原 EPUB 内部文件名与资源引用。" },
  { type: "encrypt_epub", label: "文件加密", description: "混淆 EPUB 内部文件名与资源引用。" },
  { type: "image_compress", label: "图片压缩", description: "压缩内嵌图片，减小文件体积。" },
  { type: "webp_to_img", label: "WebP 转图片", description: "将 WebP 资源转换为常规图片格式。" },
  { type: "image_to_webp", label: "图片转 WebP", description: "将内嵌图片转换为 WebP。" },
  { type: "replace_cover", label: "更换封面", description: "为 EPUB 指定新的封面图片。" },
  { type: "chinese_convert", label: "简繁转换", description: "转换 EPUB 中的可见中文文本。" },
];

const taskTypeByWire: Partial<Record<TaskType, string>> = {
  reformat_epub: "TASK_TYPE_REFORMAT_EPUB",
  decrypt_epub: "TASK_TYPE_DECRYPT_EPUB",
  encrypt_epub: "TASK_TYPE_ENCRYPT_EPUB",
  image_compress: "TASK_TYPE_IMAGE_COMPRESS",
  webp_to_img: "TASK_TYPE_WEBP_TO_IMG",
  image_to_webp: "TASK_TYPE_IMAGE_TO_WEBP",
  replace_cover: "TASK_TYPE_REPLACE_COVER",
  chinese_convert: "TASK_TYPE_CHINESE_CONVERT",
};

const {
  exportOutput,
  resolveInputSources,
  runTask: runEngineTask,
  stageSourceForTask,
  takeOpenedSources,
} = useTaskBridge();

const activeTask = ref<TaskType>("reformat_epub");
const files = ref<QueuedFile[]>([]);
const logs = ref<string[]>([]);
const result = ref<TaskResult | null>(null);
const running = ref(false);
const selectedTask = computed(() => tasks.find((task) => task.type === activeTask.value)!);
const hasCoverForAllFiles = computed(() => files.value.length > 0 && files.value.every((file) => file.coverPath));
const canRun = computed(() => files.value.length > 0 && (activeTask.value !== "replace_cover" || hasCoverForAllFiles.value));

const toErrorMessage = (error: unknown) => error instanceof Error ? error.message : String(error);

const addFiles = (paths: string[]) => {
  const existing = new Set(files.value.map((file) => file.path));
  const added = paths
    .map((path) => path.trim())
    .filter((path) => path && !existing.has(path))
    .map((path) => ({
      path,
      name: path.split(/[\\/]/).pop() || path,
      coverPath: "",
      coverPreviewUrl: "",
    }));
  files.value.push(...added);
};

const resolveAndAddFiles = async (paths: string[]) => {
  const inputs = paths.map((path) => path.trim()).filter(Boolean);
  if (!inputs.length) return;
  try {
    addFiles(await resolveInputSources(inputs));
  } catch (error) {
    logs.value.push(`添加文件失败：${toErrorMessage(error)}`);
  }
};

const pickFiles = async () => {
  const selected = await open({
    multiple: true,
    directory: false,
    filters: [{ name: "EPUB", extensions: ["epub"] }],
  });
  if (selected) await resolveAndAddFiles(Array.isArray(selected) ? selected : [selected]);
};

const clearFiles = () => {
  files.value = [];
  result.value = null;
  logs.value = [];
};

const clearResult = () => {
  result.value = null;
};

const pickCover = async () => {
  const selected = await open({
    multiple: false,
    directory: false,
    filters: [{ name: "封面图片", extensions: ["jpg", "jpeg", "png", "webp"] }],
  });
  if (typeof selected !== "string") return;
  try {
    const coverPath = await stageSourceForTask(selected, "cover");
    files.value.forEach((file) => { file.coverPath = coverPath; });
  } catch (error) {
    logs.value.push(`选择封面失败：${toErrorMessage(error)}`);
  }
};

const taskOptions = () => {
  if (activeTask.value === "image_compress") {
    return { imageCompress: { jpegQuality: 82, webpQuality: 82, pngToJpg: false, pngQuantize: false } };
  }
  if (["image_to_webp", "webp_to_img"].includes(activeTask.value)) {
    return { imageConversion: { quality: 82, pngQuantize: false } };
  }
  if (activeTask.value === "chinese_convert") return { chineseConvert: { direction: "s2t" } };
  if (activeTask.value === "replace_cover") {
    return { replaceCover: { coverPathByFile: Object.fromEntries(files.value.map((file) => [file.path, file.coverPath])) } };
  }
  return { empty: {} };
};

const runTask = async () => {
  if (!canRun.value || running.value) return;
  running.value = true;
  logs.value = [];
  result.value = null;
  const request: TaskRequest = {
    protocolVersion: "PROTOCOL_VERSION_V1",
    requestId: crypto.randomUUID(),
    runTask: {
      taskId: crypto.randomUUID(),
      taskType: taskTypeByWire[activeTask.value] as never,
      inputFiles: files.value.map((file) => file.path),
      options: taskOptions(),
    },
  };
  try {
    const response = await runEngineTask(request, (event: EngineEvent) => {
      if (event.taskEvent?.message) logs.value.push(event.taskEvent.message);
    });
    if (response.error?.message) logs.value.push(response.error.message);
    result.value = response.taskResult ?? null;
    if (response.taskResult?.status) logs.value.push(`状态：${response.taskResult.status}`);
  } catch (error) {
    logs.value.push(toErrorMessage(error));
  } finally {
    running.value = false;
  }
};

const exportResult = async (sourcePath: string) => {
  const destination = await save({
    defaultPath: sourcePath.split(/[\\/]/).pop() || "processed.epub",
    filters: [{ name: "EPUB", extensions: ["epub"] }],
  });
  if (typeof destination !== "string") return;
  try {
    await exportOutput(sourcePath, destination);
    logs.value.push("处理结果已导出。");
  } catch (error) {
    logs.value.push(`导出失败：${toErrorMessage(error)}`);
  }
};

onMounted(async () => {
  await resolveAndAddFiles(await takeOpenedSources());
});
</script>

<template>
  <main class="app-shell mobile-app">
    <header class="mobile-app-header">
      <div><p class="eyebrow">Epub Tool · Android</p><h1>EPUB 处理工具</h1></div>
      <span class="status-pill">Rust 引擎</span>
    </header>
    <nav class="task-tabs" aria-label="任务类型">
      <button v-for="task in tasks" :key="task.type" type="button" :class="{ active: activeTask === task.type }" @click="activeTask = task.type">{{ task.label }}</button>
    </nav>
    <DropZone :file-count="files.length" @pick-files="pickFiles" @clear="clearFiles" />
    <section class="workspace mobile-workspace">
      <article class="panel mobile-task-panel">
        <div class="panel-head"><div><p class="eyebrow">当前任务</p><h2>{{ selectedTask.label }}</h2></div></div>
        <p class="muted">{{ selectedTask.description }} 已选择 {{ files.length }} 个 EPUB 文件。</p>
        <div class="mobile-task-actions"><button type="button" class="primary-btn" :disabled="running || !canRun" @click="runTask">{{ running ? "处理中…" : "开始执行" }}</button></div>
        <div v-if="activeTask === 'replace_cover'" class="mobile-cover-action"><button type="button" class="secondary-btn" @click="pickCover">选择统一封面</button><span>{{ hasCoverForAllFiles ? "已为当前队列指定封面" : "请先为当前队列选择封面" }}</span></div>
        <ul v-if="files.length" class="mobile-file-list" aria-label="已选择文件"><li v-for="file in files" :key="file.path">{{ file.name }}</li></ul>
      </article>
      <article v-if="result" class="panel mobile-result-panel">
        <div class="panel-head">
          <h2>处理结果</h2>
          <div class="mobile-panel-actions">
            <span class="status-pill">{{ result.status }}</span>
            <button type="button" class="ghost-btn" @click="clearResult">清空</button>
          </div>
        </div>
        <p class="muted">成功 {{ result.summary.success }} 项，失败 {{ result.summary.failed }} 项。</p>
        <p v-if="result.outputs.length" class="mobile-save-hint">处理已完成，请点击下方“导出”按钮并选择保存位置。</p>
        <div v-if="result.outputs.length" class="mobile-output-list"><button v-for="output in result.outputs" :key="output" type="button" class="ghost-btn" @click="exportResult(output)">{{ `导出 ${output.split(/[\\/]/).pop()}` }}</button></div>
      </article>
      <article class="panel mobile-log-panel">
        <div class="panel-head">
          <div><p class="eyebrow">执行记录</p><h2>处理日志</h2></div>
          <button type="button" class="ghost-btn" :disabled="!logs.length" @click="logs = []">清空</button>
        </div>
        <div class="log-list">
          <p v-for="(log, index) in logs" :key="index">{{ log }}</p>
          <p v-if="!logs.length" class="muted">暂无日志。</p>
        </div>
      </article>
    </section>
  </main>
</template>
