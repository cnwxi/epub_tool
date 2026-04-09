<script setup lang="ts">
defineProps<{
  isActive: boolean;
  fileCount: number;
}>();

const emit = defineEmits<{
  (event: "pick-files"): void;
  (event: "scan-directory"): void;
  (event: "clear"): void;
}>();
</script>

<template>
  <section class="dropzone" :class="{ active: isActive }">
    <div>
      <p class="eyebrow">输入源</p>
      <h2>拖入 EPUB 或直接从系统选择</h2>
      <p class="muted">
        支持单文件、多文件或目录扫描。当前队列 {{ fileCount }} 个文件。
      </p>
    </div>
    <div class="dropzone-actions">
      <button class="primary-btn" type="button" @click="emit('pick-files')">
        选择文件
      </button>
      <button class="secondary-btn" type="button" @click="emit('scan-directory')">
        扫描目录
      </button>
      <button class="ghost-btn" type="button" @click="emit('clear')">
        清空队列
      </button>
    </div>
  </section>
</template>
