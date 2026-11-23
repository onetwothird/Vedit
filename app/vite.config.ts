import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const version = (JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string }).version;

export default defineConfig({
  base: "./",
  define: { __APP_VERSION__: JSON.stringify(version) },
  plugins: [react()],
  // 5173: the engine allows this dev origin. src-tauri/target churns during tauri dev; Vite must not watch it.
  server: { port: 5173, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  clearScreen: false,
  test: { environment: "jsdom", globals: false, exclude: [...configDefaults.exclude, "tests/e2e/**"] },
});
