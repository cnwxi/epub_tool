import { ref, watch, type Ref } from "vue";

import { useTaskBridge } from "./useTaskBridge";

const { isTauriRuntime, loadPersistedState, savePersistedState } = useTaskBridge();

const SAVE_DEBOUNCE_MS = 300;

export function usePersistentState<T>(
  key: string,
  fallback: T,
  normalize?: (value: unknown) => T,
): Ref<T> {
  const normalizeValue = (value: unknown): T =>
    normalize ? normalize(value) : (value as T);
  const state = ref(normalizeValue(fallback)) as Ref<T>;
  let nativeStoreReady = !isTauriRuntime();
  let saveQueue = Promise.resolve();
  let saveDebounceTimer = 0;
  let pendingSaveValue: T | undefined;

  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) {
        const parsed = JSON.parse(raw);
        const normalized = normalizeValue(parsed);
        state.value = normalized;
        if (JSON.stringify(parsed) !== JSON.stringify(normalized)) {
          window.localStorage.setItem(key, JSON.stringify(normalized));
        }
      }
    } catch {
      state.value = normalizeValue(fallback);
    }
  }

  if (typeof window !== "undefined" && isTauriRuntime()) {
    void loadPersistedState<T>(key)
      .then(({ found, value }) => {
        if (found) {
          state.value = normalizeValue(value);
        }
        nativeStoreReady = true;
        if (!found) {
          saveQueue = saveQueue
            .then(() => savePersistedState(key, state.value))
            .catch(() => undefined);
        }
      })
      .catch(() => {
        nativeStoreReady = true;
      });
  }

  const flushPendingSave = () => {
    if (!saveDebounceTimer) {
      return;
    }
    window.clearTimeout(saveDebounceTimer);
    saveDebounceTimer = 0;
    const snapshot = pendingSaveValue;
    pendingSaveValue = undefined;
    if (snapshot === undefined) {
      return;
    }
    saveQueue = saveQueue
      .then(() => savePersistedState(key, snapshot))
      .catch(() => undefined);
  };

  watch(
    state,
    (value) => {
      if (typeof window === "undefined") {
        return;
      }
      const normalizedValue = normalizeValue(value);
      window.localStorage.setItem(key, JSON.stringify(normalizedValue));

      if (!nativeStoreReady || !isTauriRuntime()) {
        return;
      }

      pendingSaveValue = normalizedValue;
      if (saveDebounceTimer) {
        window.clearTimeout(saveDebounceTimer);
      }
      saveDebounceTimer = window.setTimeout(flushPendingSave, SAVE_DEBOUNCE_MS);
    },
    { deep: true },
  );

  if (typeof window !== "undefined" && isTauriRuntime()) {
    window.addEventListener("pagehide", flushPendingSave);
    window.addEventListener("beforeunload", flushPendingSave);
  }

  return state;
}
