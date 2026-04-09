import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const cargoToml = readFileSync(resolve(__dirname, "../src-tauri/Cargo.toml"), "utf8");
const versionMatch = cargoToml.match(/^version\s*=\s*"([^"]+)"/m);
const appVersion = versionMatch?.[1] ?? "0.1.0";

export default defineConfig({
  plugins: [vue()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },
});
