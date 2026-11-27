import { defineConfig } from "@playwright/test";

// Two builds are tested, each on a port of its own, so a dev server that is already running is never mistaken for the mock:
//   chromium: the desktop UI against the mock engine (5199)
//   web:      the website build (VITE_FLAVOR=web) against the mock engine (5198); web.spec.ts and sync.spec.ts run there
//             (sync.spec.ts runs in both: each of its describes skips the build it is not for)
// every test starts past the first-run sign-in screen (signin.spec.ts opts back in); the web project also starts signed in
const SHOWN = { viewport: { width: 1440, height: 900 } };

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  reporter: "list",
  projects: [
    {
      name: "chromium",
      testIgnore: "web.spec.ts",
      use: { browserName: "chromium", baseURL: "http://localhost:5199", ...SHOWN, storageState: "tests/e2e/skip-signin.json" },
    },
    {
      name: "web",
      testMatch: ["web.spec.ts", "sync.spec.ts"],
      use: { browserName: "chromium", baseURL: "http://localhost:5198", ...SHOWN, storageState: "tests/e2e/web-session.json" },
    },
  ],
  webServer: [
    { command: "npm run dev:mock -- --port 5199", url: "http://localhost:5199", reuseExistingServer: false, timeout: 60_000 },
    { command: "npm run dev:mock:web -- --port 5198", url: "http://localhost:5198", reuseExistingServer: false, timeout: 60_000 },
  ],
});
