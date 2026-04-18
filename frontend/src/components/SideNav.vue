<script setup lang="ts">
import { computed, ref, watch } from "vue";
import brandEasterIconUrl from "../../../img/icon.png";

import type { SectionKey } from "../types";

const props = defineProps<{
  active: SectionKey;
  items: Array<{ key: SectionKey; label: string; description: string }>;
  brandEasterActive: boolean;
  handleBrandEasterClick: () => void;
  triggerBrandEasterAnimation: () => void;
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
          <p class="eyebrow brand-eyebrow">NEW UI · ET</p>
          <h1 class="brand-title" aria-label="Epub Tool, E-Book Thor">
            <span class="brand-title-main">Epub Tool</span>
            <span class="brand-title-alias">E-Book Thor</span>
          </h1>
          <p class="brand-copy">
            挥动小锤，批量锻造。
          </p>
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
