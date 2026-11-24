import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import type { Plugin } from "vite";

const version = (JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string }).version;

/** Local-only previews (src/screens/home/*-local.*: third-party footage and memes) never reach a release build:
    the code only reads them in dev, but an eager import.meta.glob still copies its files into dist. */
function dropLocalMedia(): Plugin {
  const local = /[\\/]screens[\\/]home[\\/][^\\/]+-local\.[a-z0-9]+$/i;
  return {
    name: "drop-local-media",
    apply: "build",
    enforce: "pre",
    load: (id) => (local.test(id.split("?")[0]) ? "export default \"\";" : null),
  };
}

export default defineConfig({
  base: "./",
  define: { __APP_VERSION__: JSON.stringify(version) },
  plugins: [dropLocalMedia(), react()],
  // 5173: the engine allows this dev origin. src-tauri/target churns during tauri dev; Vite must not watch it.
  server: { port: 5173, strictPort: true, watch: { ignored: ["**/src-tauri/**"] } },
  clearScreen: false,
  test: { environment: "jsdom", globals: false, exclude: [...configDefaults.exclude, "tests/e2e/**"] },
});
