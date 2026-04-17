<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { SectionKey } from "../types";

const props = defineProps<{
  active: SectionKey;
  items: Array<{ key: SectionKey; label: string; description: string }>;
}>();

const emit = defineEmits<{
  (event: "select", value: SectionKey): void;
}>();

const executionSectionKeys: SectionKey[] = [
  "reformat",
  "decrypt",
  "encrypt",
  "font_encrypt",
  "transfer_img",
];
const utilitySectionKeys: SectionKey[] = ["settings", "about"];

const executionItems = computed(() =>
  props.items.filter((item) => executionSectionKeys.includes(item.key)),
);
const utilityItems = computed(() =>
  props.items.filter((item) => utilitySectionKeys.includes(item.key)),
);

const executionOpen = ref(true);
const utilityOpen = ref(true);

watch(
  () => props.active,
  (active) => {
    if (executionSectionKeys.includes(active)) {
      executionOpen.value = true;
    }
    if (utilitySectionKeys.includes(active)) {
      utilityOpen.value = true;
    }
  },
  { immediate: true },
);
</script>

<template>
  <aside class="side-nav nav-animated-panel">
    <section class="nav-group nav-group-brand nav-animated-block">
      <div class="brand-block">
        <p class="eyebrow">NewUI</p>
        <h1>Epub Tool</h1>
        <p class="brand-copy">
          面向 EPUB 批量处理的桌面工具，统一提供队列执行、结果回看、日志定位与历史统计。
        </p>
      </div>
    </section>

    <section class="nav-group nav-group-collapsible nav-animated-block" :class="{ open: executionOpen }">
      <button class="nav-group-toggle" type="button" @click="executionOpen = !executionOpen">
        <span class="nav-group-title">工具箱</span>
        <span class="nav-group-chevron" :class="{ open: executionOpen }" aria-hidden="true">
          ▾
        </span>
      </button>
      <nav v-show="executionOpen" class="nav-list">
        <button
          v-for="item in executionItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: item.key === active }"
          type="button"
          @click="emit('select', item.key)"
        >
          <span>{{ item.label }}</span>
          <small>{{ item.description }}</small>
        </button>
      </nav>
    </section>

    <section class="nav-group nav-group-collapsible nav-animated-block" :class="{ open: utilityOpen }">
      <button class="nav-group-toggle" type="button" @click="utilityOpen = !utilityOpen">
        <span class="nav-group-title">设置与关于</span>
        <span class="nav-group-chevron" :class="{ open: utilityOpen }" aria-hidden="true">
          ▾
        </span>
      </button>
      <nav v-show="utilityOpen" class="nav-list nav-list-compact">
        <button
          v-for="item in utilityItems"
          :key="item.key"
          class="nav-item"
          :class="{ active: item.key === active }"
          type="button"
          @click="emit('select', item.key)"
        >
          <span>{{ item.label }}</span>
          <small>{{ item.description }}</small>
        </button>
      </nav>
    </section>
  </aside>
</template>
