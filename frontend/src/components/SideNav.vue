<script setup lang="ts">
import { computed, ref, watch } from "vue";
import brandEasterIconUrl from "../../../assets/img/icon.png";

import type { PythonWorkerStatus, SectionKey } from "../types";

const props = defineProps<{
  active: SectionKey;
  items: Array<{ key: SectionKey; label: string; description: string }>;
  brandEasterActive: boolean;
  handleBrandEasterClick: () => void;
  triggerBrandEasterAnimation: () => void;
  pythonWorkerStatus: PythonWorkerStatus;
  pythonWorkerStatusLabel: string;
}>();

const emit = defineEmits<{
  (event: "select", value: SectionKey): void;
}>();

const executionSectionKeys: SectionKey[] = [
  "reformat_epub",
  "decrypt_epub",
  "encrypt_epub",
  "decrypt_font",
  "encrypt_font",
  "image_compress",
  "webp_to_img",
  "image_to_webp",
  "replace_cover",
  "chinese_convert",
];
const overviewSectionKeys: SectionKey[] = ["overview", "engine"];
const utilitySectionKeys: SectionKey[] = ["settings", "about"];

const overviewItem = computed(() =>
  props.items.find((item) => item.key === "overview"),
);

const executionItems = computed(() =>
  props.items.filter((item) => executionSectionKeys.includes(item.key)),
);
const utilityItems = computed(() =>
  props.items.filter((item) => utilitySectionKeys.includes(item.key)),
);

const executionOpen = ref(true);
const overviewOpen = ref(true);
const utilityOpen = ref(true);
const pythonWorkerStateLabel = computed(() =>
  props.pythonWorkerStatusLabel.replace(/^处理引擎/, "") || props.pythonWorkerStatusLabel,
);

watch(
  () => props.active,
  (active) => {
    if (executionSectionKeys.includes(active)) {
      executionOpen.value = true;
    }
    if (overviewSectionKeys.includes(active)) {
      overviewOpen.value = true;
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
      <div
        class="brand-block"
        :class="{ 'brand-block-easter-active': props.brandEasterActive }"
        role="button"
        tabindex="0"
        title="7连击召唤Thor"
        @click="props.handleBrandEasterClick"
        @keydown.enter.prevent="props.triggerBrandEasterAnimation"
        @keydown.space.prevent="props.triggerBrandEasterAnimation">
        <div class="brand-content">
          <h1 class="brand-title" aria-label="Epub Tool, E-Book Thor">
            <span class="brand-title-main">Epub Tool</span>
            <span class="brand-title-alias">E-Book Thor</span>
          </h1>
        </div>
        <div class="brand-easter-stage" aria-hidden="true">
          <div class="brand-easter-emblem">
            <span class="brand-easter-glow"></span>
            <img class="brand-easter-icon" :src="brandEasterIconUrl" alt="" />
          </div>
          <span class="brand-easter-caption">E-BOOK THOR</span>
          <span class="brand-easter-author">BY CNWXI</span>
        </div>
      </div>
    </section>

    <section class="nav-group nav-group-collapsible nav-group-overview nav-animated-block"
      :class="{ open: overviewOpen }">
      <button class="nav-group-toggle" type="button" @click="overviewOpen = !overviewOpen">
        <span class="nav-group-title">概览</span>
        <span class="nav-group-chevron" :class="{ open: overviewOpen }" aria-hidden="true">
          ▾
        </span>
      </button>
      <nav v-show="overviewOpen" class="nav-list">
        <button v-if="overviewItem" class="nav-item" :class="{ active: overviewItem.key === active }"
          type="button" @click="emit('select', overviewItem.key)">
          <span>{{ overviewItem.label }}</span>
        </button>
        <button class="nav-item nav-worker-status" :class="`state-${props.pythonWorkerStatus.state}`"
          type="button" :title="`${props.pythonWorkerStatusLabel}：${props.pythonWorkerStatus.message}`"
          @click="emit('select', 'engine')">
          <span class="nav-worker-name">处理引擎</span>
          <span class="nav-worker-state">
            <span class="nav-worker-dot" aria-hidden="true"></span>
            <strong>{{ pythonWorkerStateLabel }}</strong>
          </span>
        </button>
      </nav>
    </section>

    <section class="nav-group nav-group-collapsible nav-animated-block" :class="{ open: executionOpen }">
      <button class="nav-group-toggle" type="button" @click="executionOpen = !executionOpen">
        <span class="nav-group-title">工具</span>
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
        </button>
      </nav>
    </section>

    <section class="nav-group nav-group-collapsible nav-animated-block" :class="{ open: utilityOpen }">
      <button class="nav-group-toggle" type="button" @click="utilityOpen = !utilityOpen">
        <span class="nav-group-title">系统</span>
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
        </button>
      </nav>
    </section>

  </aside>
</template>
