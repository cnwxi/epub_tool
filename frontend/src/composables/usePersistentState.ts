import { ref, watch, type Ref } from "vue";

import { useTaskBridge } from "./useTaskBridge";

const { isTauriRuntime, loadPersistedState, savePersistedState } = useTaskBridge();

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

  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) {
        state.value = normalizeValue(JSON.parse(raw));
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

      saveQueue = saveQueue
        .then(() => savePersistedState(key, normalizedValue))
        .catch(() => undefined);
    },
    { deep: true },
  );

  return state;
}
