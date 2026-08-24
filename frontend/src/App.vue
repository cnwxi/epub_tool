<script setup lang="ts">
import { computed, ref } from "vue";
import { Channel, invoke } from "@tauri-apps/api/core";
import DropZone from "./components/DropZone.vue";
import type { EngineEvent, EngineResponse, QueuedFile, TaskRequest, TaskType } from "./types";

const tasks: Array<{ type: TaskType; label: string }> = [
  { type: "reformat_epub", label: "格式化" },
  { type: "decrypt_epub", label: "文件解密" },
  { type: "encrypt_epub", label: "文件加密" },
  { type: "image_compress", label: "图片压缩" },
  { type: "webp_to_img", label: "WebP 转图片" },
  { type: "image_to_webp", label: "图片转 WebP" },
  { type: "replace_cover", label: "更换封面" },
  { type: "chinese_convert", label: "简繁转换" },
];
const taskTypeByWire: Record<TaskType, string> = {
  reformat_epub: "TASK_TYPE_REFORMAT_EPUB", decrypt_epub: "TASK_TYPE_DECRYPT_EPUB", encrypt_epub: "TASK_TYPE_ENCRYPT_EPUB",
  image_compress: "TASK_TYPE_IMAGE_COMPRESS", webp_to_img: "TASK_TYPE_WEBP_TO_IMG", image_to_webp: "TASK_TYPE_IMAGE_TO_WEBP",
  replace_cover: "TASK_TYPE_REPLACE_COVER", chinese_convert: "TASK_TYPE_CHINESE_CONVERT",
};
const activeTask = ref<TaskType>("reformat_epub");
const files = ref<QueuedFile[]>([]);
const logs = ref<string[]>([]);
const running = ref(false);
const dragActive = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const selectedTask = computed(() => tasks.find((task) => task.type === activeTask.value)!);

const addFiles = (paths: string[]) => {
  const existing = new Set(files.value.map((file) => file.path));
  files.value.push(...paths.filter((path) => !existing.has(path)).map((path) => ({ path, name: path.split(/[\\/]/).pop() || path, coverPath: "", coverPreviewUrl: "" })));
};
const pickFiles = () => fileInput.value?.click();
const onFileInput = (event: Event) => addFiles(Array.from((event.target as HTMLInputElement).files ?? []).map((file) => file.name));
const clearFiles = () => { files.value = []; logs.value = []; };
const handleDrop = (droppedFiles: File[]) => addFiles(droppedFiles.map((file) => file.name));
const runTask = async () => {
  if (!files.value.length || running.value) return;
  running.value = true; logs.value = [];
  const options = activeTask.value === "image_compress"
    ? { imageCompress: { jpegQuality: 82, webpQuality: 82, pngToJpg: false, pngQuantize: false } }
    : ["image_to_webp", "webp_to_img"].includes(activeTask.value)
      ? { imageConversion: { quality: 82, pngQuantize: false } }
      : activeTask.value === "chinese_convert"
        ? { chineseConvert: { direction: "s2t" } }
        : activeTask.value === "replace_cover"
          ? { replaceCover: { coverPathByFile: {} } }
          : { empty: {} };
  const request: TaskRequest = {
    protocolVersion: "PROTOCOL_VERSION_V1", requestId: crypto.randomUUID(),
    runTask: { taskId: crypto.randomUUID(), taskType: taskTypeByWire[activeTask.value] as never, inputFiles: files.value.map((file) => file.path), options },
  };
  const channel = new Channel<EngineEvent>((event) => { if (event.taskEvent?.message) logs.value.push(event.taskEvent.message); });
  try {
    const response = await invoke<EngineResponse>("run_epub_task", { request, onEvent: channel });
    if (response.error?.message) logs.value.push(response.error.message);
    if (response.taskResult?.status) logs.value.push(`状态：${response.taskResult.status}`);
  } catch (error) { logs.value.push(String(error)); }
  finally { running.value = false; }
};
</script>

<template>
  <main class="app-shell">
    <header class="app-header"><div><p class="eyebrow">Epub Tool</p><h1>EPUB 处理工具</h1></div><span class="status-pill">Rust 引擎</span></header>
    <nav class="task-tabs" aria-label="任务类型"><button v-for="task in tasks" :key="task.type" type="button" :class="{ active: activeTask === task.type }" @click="activeTask = task.type">{{ task.label }}</button></nav>
    <DropZone :is-active="dragActive" :file-count="files.length" :is-mobile-runtime="false" @drag-state="dragActive = $event" @drop-files="handleDrop" @pick-files="pickFiles" @clear="clearFiles" />
    <input ref="fileInput" type="file" accept=".epub" multiple hidden @change="onFileInput" />
    <section class="workspace">
      <div class="panel"><div class="panel-head"><div><p class="eyebrow">当前任务</p><h2>{{ selectedTask.label }}</h2></div><button type="button" class="primary-btn" :disabled="running || !files.length" @click="runTask">{{ running ? "处理中..." : "开始执行" }}</button></div><p class="muted">已选择 {{ files.length }} 个 EPUB 文件。字体加密、解密和 OCR 功能已移除。</p></div>
      <div class="panel"><div class="panel-head"><h2>处理日志</h2><button type="button" class="ghost-btn" @click="logs = []">清空</button></div><div class="log-list"><p v-for="(log, index) in logs" :key="index">{{ log }}</p><p v-if="!logs.length" class="muted">暂无日志。</p></div></div>
    </section>
  </main>
</template>
