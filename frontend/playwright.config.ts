import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests of the run view against the real local stack (Architecture.md §11): the API
 * on 127.0.0.1:8000, the ARQ worker and Redis must be running. Playwright starts the built
 * Next.js app (`npm run build` first) unless one is already serving on 3000.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // One worker: the specs share fixed users (and, locally, the live project).
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run start",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
