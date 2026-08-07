<script setup lang="ts">
defineProps<{
  isActive: boolean;
  fileCount: number;
  isMobileRuntime: boolean;
}>();

const emit = defineEmits<{
  (event: "pick-files"): void;
  (event: "scan-directory"): void;
  (event: "clear"): void;
  (event: "drag-state", active: boolean): void;
  (event: "drop-files", files: File[]): void;
}>();

const handleDragEnter = () => {
  emit("drag-state", true);
};

const handleDragLeave = (event: DragEvent) => {
  const nextTarget = event.relatedTarget;
  if (nextTarget instanceof Node && event.currentTarget instanceof Node) {
    if (event.currentTarget.contains(nextTarget)) {
      return;
    }
  }
  emit("drag-state", false);
};

const handleDrop = (event: DragEvent) => {
  emit("drag-state", false);
  emit("drop-files", Array.from(event.dataTransfer?.files ?? []));
};
</script>

<template>
  <section
    class="dropzone glass-medium content-animated-block"
    :class="{ active: isActive }"
    @dragenter.prevent="handleDragEnter"
    @dragleave.prevent="handleDragLeave"
    @dragover.prevent="handleDragEnter"
    @drop.prevent="handleDrop"
  >
    <div>
      <p class="eyebrow">输入源</p>
      <h2>{{ isMobileRuntime ? "直接从系统选择 EPUB 文件" : "拖入 EPUB、文件夹或直接从系统选择" }}</h2>
      <p v-if="!isMobileRuntime" class="muted">
        支持单文件、多文件、拖拽文件夹或目录扫描。当前队列 {{ fileCount }} 个文件。
      </p>
      <p v-else class="muted">移动端会将所选文件安全暂存到应用内处理。当前队列 {{ fileCount }} 个文件。</p>
    </div>
    <div class="dropzone-actions">
      <button class="primary-btn" type="button" @click="emit('pick-files')">
        选择文件
      </button>
      <button v-if="!isMobileRuntime" class="secondary-btn" type="button" @click="emit('scan-directory')">
        扫描目录
      </button>
      <button class="ghost-btn task-action-btn" type="button" @click="emit('clear')">
        清空队列
      </button>
    </div>
  </section>
</template>
