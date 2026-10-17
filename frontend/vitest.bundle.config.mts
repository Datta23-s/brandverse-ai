import { defineConfig } from "vitest/config";

/** The bundle scan (security/bundle.test.ts): runs after `next build`, not with the unit tests. */
export default defineConfig({
  test: { include: ["security/**/*.test.ts"], environment: "node" },
});
