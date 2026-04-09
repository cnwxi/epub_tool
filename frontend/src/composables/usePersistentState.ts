import { ref, watch, type Ref } from "vue";

export function usePersistentState<T>(key: string, fallback: T): Ref<T> {
  const state = ref(fallback) as Ref<T>;

  if (typeof window !== "undefined") {
    try {
      const raw = window.localStorage.getItem(key);
      if (raw != null) {
        state.value = JSON.parse(raw) as T;
      }
    } catch {
      state.value = fallback;
    }
  }

  watch(
    state,
    (value) => {
      if (typeof window === "undefined") {
        return;
      }
      window.localStorage.setItem(key, JSON.stringify(value));
    },
    { deep: true },
  );

  return state;
}

