import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  reporter: "list",
  // a port of its own, so a dev server that is already running is never mistaken for the mock
  // every test starts past the first-run sign-in screen (signin.spec.ts opts back in)
  use: { baseURL: "http://localhost:5199", viewport: { width: 1440, height: 900 }, storageState: "tests/e2e/skip-signin.json" },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
  webServer: {
    command: "npm run dev:mock -- --port 5199",
    url: "http://localhost:5199",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
