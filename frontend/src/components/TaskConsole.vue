<script setup lang="ts">
import { computed } from "vue";
import type { TaskEvent, TaskResult } from "../types";

const props = defineProps<{
  logs: TaskEvent[];
  result: TaskResult | null;
}>();

const visibleLogs = computed(() => [...props.logs].reverse());

const formatFileName = (path: string): string => path.split(/[\\/]/).pop() ?? path;

const emit = defineEmits<{
  (event: "open-log"): void;
  (event: "clear-log"): void;
  (event: "open-output-folder", path: string): void;
}>();
</script>

<template>
  <section class="console-grid">
    <article class="panel panel-console">
      <div class="panel-head">
        <div>
          <p class="eyebrow">过程</p>
          <h3>处理日志</h3>
        </div>
        <div class="panel-actions">
          <button class="ghost-btn" type="button" @click="emit('open-log')">
            打开处理日志
          </button>
          <button class="ghost-btn" type="button" @click="emit('clear-log')">
            清空面板
          </button>
        </div>
      </div>
      <div class="log-list">
        <div v-if="logs.length === 0" class="log-empty">尚未执行任务。</div>
        <div
          v-for="(log, index) in visibleLogs"
          :key="`${log.event}-${index}`"
          class="log-row"
          :class="log.level ?? log.status"
        >
          <span class="log-event">{{ log.event }}</span>
          <span class="log-message">{{ log.message }}</span>
        </div>
      </div>
    </article>

    <article class="panel panel-result">
      <div class="panel-head">
        <div>
          <p class="eyebrow">结果</p>
          <h3>最近一次执行摘要</h3>
        </div>
      </div>
      <div v-if="!result" class="result-empty">还没有可展示的执行结果。</div>
      <div v-else class="result-block">
        <div class="result-metrics">
          <div>
            <strong>{{ result.summary.total }}</strong>
            <span>总文件</span>
          </div>
          <div>
            <strong>{{ result.summary.success }}</strong>
            <span>成功</span>
          </div>
          <div>
            <strong>{{ result.summary.failed }}</strong>
            <span>失败</span>
          </div>
          <div>
            <strong>{{ result.summary.skipped }}</strong>
            <span>跳过</span>
          </div>
        </div>

        <div v-if="result.outputs.length > 0" class="result-detail-block">
          <div class="result-detail-head">
            <strong>成功</strong>
            <span>{{ result.outputs.length }} 项</span>
          </div>
          <div class="result-output-list">
            <button
              v-for="output in result.outputs"
              :key="output"
              class="result-output-row success"
              type="button"
              @click="emit('open-output-folder', output)"
            >
              <div class="result-row-head">
                <strong>{{ formatFileName(output) }}</strong>
                <span class="result-status-tag success">成功</span>
              </div>
              <span>{{ output }}</span>
            </button>
          </div>
        </div>

        <div v-if="result.errors.length > 0" class="result-detail-block">
          <div class="result-detail-head">
            <strong>失败</strong>
            <span>{{ result.errors.length }} 项</span>
          </div>
          <div class="result-detail-list">
            <div
              v-for="item in result.errors"
              :key="`${item.input_file}-${item.message}`"
              class="result-detail-row error"
            >
              <div class="result-row-head">
                <strong>{{ formatFileName(item.input_file) }}</strong>
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
            <span>{{ result.skipped.length }} 项</span>
          </div>
          <div class="result-detail-list">
            <div
              v-for="item in result.skipped"
              :key="`${item.input_file}-${item.message}`"
              class="result-detail-row skip"
            >
              <div class="result-row-head">
                <strong>{{ formatFileName(item.input_file) }}</strong>
                <span class="result-status-tag skip">跳过</span>
              </div>
              <p>{{ item.message }}</p>
              <span>{{ item.input_file }}</span>
            </div>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>
