import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  // 5173: the engine allows this dev origin. src-tauri/target churns during tauri dev; Vite must not watch it.
  server: { port: 5173, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  clearScreen: false,
  test: { environment: "jsdom", globals: false, exclude: [...configDefaults.exclude, "tests/e2e/**"] },
});
