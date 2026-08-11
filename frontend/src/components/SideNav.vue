<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import brandEasterIconUrl from "../../../assets/img/icon.png";

import type { EngineStatus, SectionKey } from "../types";

const props = defineProps<{
  active: SectionKey;
  items: Array<{ key: SectionKey; label: string; description: string }>;
  brandEasterActive: boolean;
  handleBrandEasterClick: () => void;
  triggerBrandEasterAnimation: () => void;
  engineStatus: EngineStatus;
  engineStatusLabel: string;
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
const compactMenuOpen = ref(false);
const isCompactNavigation = ref(false);
let compactNavigationMediaQuery: MediaQueryList | null = null;
const engineStateLabel = computed(() =>
  props.engineStatusLabel.replace(/^处理引擎/, "") || props.engineStatusLabel,
);

const openActiveGroup = (active: SectionKey) => {
  overviewOpen.value = overviewSectionKeys.includes(active);
  executionOpen.value = executionSectionKeys.includes(active);
  utilityOpen.value = utilitySectionKeys.includes(active);
};

const syncCompactNavigation = () => {
  isCompactNavigation.value = compactNavigationMediaQuery?.matches ?? false;
  if (isCompactNavigation.value) {
    compactMenuOpen.value = false;
    openActiveGroup(props.active);
  }
};

const selectSection = (section: SectionKey) => {
  compactMenuOpen.value = false;
  emit("select", section);
};

onMounted(() => {
  compactNavigationMediaQuery = window.matchMedia("(max-width: 900px)");
  syncCompactNavigation();
  compactNavigationMediaQuery.addEventListener("change", syncCompactNavigation);
});

onBeforeUnmount(() => {
  compactNavigationMediaQuery?.removeEventListener("change", syncCompactNavigation);
});

watch(
  () => props.active,
  (active) => {
    if (isCompactNavigation.value) {
      openActiveGroup(active);
      return;
    }
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
  <aside class="side-nav nav-animated-panel" :class="{ 'compact-menu-open': compactMenuOpen }">
    <div class="compact-nav-bar">
      <button class="compact-brand-button" type="button" @click="selectSection('overview')">
        <span class="compact-brand-name">Epub Tool</span>
        <span class="compact-brand-caption">EPUB</span>
      </button>
      <button
        class="compact-nav-menu-toggle"
        type="button"
        aria-label="切换导航菜单"
        aria-controls="compact-navigation"
        :aria-expanded="compactMenuOpen"
        @click="compactMenuOpen = !compactMenuOpen"
      >
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
        <span aria-hidden="true"></span>
      </button>
    </div>

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

    <div id="compact-navigation" class="nav-groups" :aria-hidden="isCompactNavigation && !compactMenuOpen">
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
            type="button" @click="selectSection(overviewItem.key)">
            <span>{{ overviewItem.label }}</span>
          </button>
          <button class="nav-item nav-engine-status" :class="`state-${props.engineStatus.state}`"
            type="button" :title="`${props.engineStatusLabel}：${props.engineStatus.message}`"
            @click="selectSection('engine')">
            <span class="nav-engine-name">处理引擎</span>
            <span class="nav-engine-state">
              <span class="nav-engine-dot" aria-hidden="true"></span>
              <strong>{{ engineStateLabel }}</strong>
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
            @click="selectSection(item.key)"
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
            @click="selectSection(item.key)"
          >
            <span>{{ item.label }}</span>
          </button>
        </nav>
      </section>
    </div>

  </aside>
</template>
