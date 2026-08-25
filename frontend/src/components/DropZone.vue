<script setup lang="ts">
defineProps<{
  fileCount: number;
}>();

const emit = defineEmits<{
  (event: "pick-files"): void;
  (event: "clear"): void;
}>();
</script>

<template>
  <section class="dropzone glass-medium content-animated-block">
    <div>
      <p class="eyebrow">输入源</p>
      <h2>直接从系统选择 EPUB 文件</h2>
      <p class="muted">所选文件会安全暂存到应用内处理。当前队列 {{ fileCount }} 个文件。</p>
    </div>
    <div class="dropzone-actions">
      <button class="primary-btn" type="button" @click="emit('pick-files')">
        选择文件
      </button>
      <button class="ghost-btn task-action-btn" type="button" @click="emit('clear')">
        清空队列
      </button>
    </div>
  </section>
</template>
