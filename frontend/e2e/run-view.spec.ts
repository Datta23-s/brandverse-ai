/**
 * Phases.md Phase 1 exit criterion: "The fake pipeline animates in the browser". Each test starts
 * a real fake run through the form, against the real API, worker and Redis.
 */
import { expect, test, type Page } from "@playwright/test";

import { ensureOrganisation, signIn } from "./support";

const SHOTS = process.env.E2E_SCREENSHOT_DIR;

// Runs belong to an organisation and need a signed-in owner or editor (B2).
test.beforeEach(async ({ page }) => {
  await signIn(page, "A");
  await ensureOrganisation(page, "A");
});

async function startRun(page: Page, opts: { depth?: "D0" | "D1" | "D2"; delayMs: number; failAt?: number }) {
  await page.goto("/");
  if (opts.depth) await page.getByLabel(new RegExp(`^${opts.depth} ·`)).check();
  await page.getByLabel("Time per step (ms)").fill(String(opts.delayMs));
  if (opts.failAt !== undefined) {
    await page.getByLabel("Simulate a failure").selectOption(String(opts.failAt));
  }
  await page.getByRole("button", { name: "Start fake run" }).click();
  // Client-side navigation: there is no load event to wait for.
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/, { waitUntil: "commit" });
}

const row = (page: Page, n: number) => page.locator(`li[data-step="${n}"]`);
// Scoped to <main>: Next.js mounts its own role="alert" route announcer.
const outcome = (page: Page) => page.getByRole("main").getByRole("alert");

/** Every row's phase, in step order. */
const phases = (page: Page) =>
  page.locator("li[data-step]").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-phase") ?? ""));

test("the fake pipeline animates live, step by step, to completion", async ({ page }) => {
  await startRun(page, { depth: "D0", delayMs: 250 });

  // Sample the stepper throughout the run. Animating live means the browser shows each step
  // running in turn — earlier steps done, later ones still waiting — not a finished list at once.
  // A plain loop, not toPass: an out-of-order sample must fail the test, not be retried away.
  const seenActive: number[] = [];
  const done = (p: string) => p === "completed" || p === "skipped";
  let shot = false;
  for (const deadline = Date.now() + 20_000; ; ) {
    const now = await phases(page);
    const active = now.indexOf("active") + 1;
    if (active > 0) {
      expect(now.slice(0, active - 1).every(done), `before step ${active}: ${now}`).toBe(true);
      expect(now.slice(active).every((p) => p === "pending"), `after step ${active}: ${now}`).toBe(true);
      if (seenActive.at(-1) !== active) seenActive.push(active);
      if (SHOTS && !shot && active === 4) {
        shot = true;
        await page.screenshot({ path: `${SHOTS}/run-midway.png`, fullPage: true });
      }
    }
    if (now.length === 15 && now.every(done)) break;
    expect(Date.now(), "run did not finish within 20 s").toBeLessThan(deadline);
    await page.waitForTimeout(40);
  }

  // Seen running in order, across most of the pipeline (one sample may straddle a step).
  expect(seenActive).toEqual([...seenActive].sort((a, b) => a - b));
  expect(seenActive.length).toBeGreaterThanOrEqual(10);

  await expect(page.getByRole("heading", { name: "Pipeline complete" })).toBeVisible();
  for (let n = 1; n <= 15; n++) {
    await expect(row(page, n)).toHaveAttribute("data-phase", n === 6 ? "skipped" : "completed");
  }
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "15");
  await expect(page.getByText("All 15 steps finished in")).toBeVisible();
  // Each completed step shows how long it took (~250 ms).
  await expect(row(page, 1)).toContainText(/0\.\d\d s/);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/run-complete.png`, fullPage: true });
});

test("a failed run shows where it stopped and what did not run", async ({ page }) => {
  await startRun(page, { delayMs: 120, failAt: 4 });

  await expect(page.getByRole("heading", { name: "Run failed" })).toBeVisible({ timeout: 20_000 });
  const alert = outcome(page);
  await expect(alert).toContainText("Stopped at step 4, Retrieve memory.");
  await expect(alert).toContainText("simulated failure at step 4 (retrieve_memory)");
  await expect(row(page, 3)).toHaveAttribute("data-phase", "completed");
  await expect(row(page, 4)).toHaveAttribute("data-phase", "failed");
  for (let n = 5; n <= 15; n++) await expect(row(page, n)).toHaveAttribute("data-phase", "not_run");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/run-failed.png`, fullPage: true });
});

test("reloading mid-run replays what happened and keeps following", async ({ page }) => {
  await startRun(page, { delayMs: 300 });
  // Some way in, not finished (15 steps x 300 ms leaves seconds of margin either side).
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", /^([3-9]|1[0-2])$/);
  await page.reload();
  // Earlier steps are rebuilt from the stream, then the run continues live.
  await expect(row(page, 1)).toHaveAttribute("data-phase", "completed");
  await expect(page.getByRole("heading", { name: "Pipeline complete" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "15");
});

test("an unknown run says so instead of waiting forever", async ({ page }) => {
  await page.goto("/runs/00000000-0000-4000-8000-000000000000");
  await expect(outcome(page)).toContainText("This run does not exist, or you are not a member");
});

test("reduced motion stops the in-progress animation (Design.md §6)", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Long steps, and whichever step is running: the check must not race a short window.
  await startRun(page, { delayMs: 2000 });
  const marker = page.locator('li[data-phase="active"] .bv-breathe').first();
  await expect(marker).toBeAttached();
  expect(await marker.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  // Control: without the preference the same marker does animate.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect
    .poll(() => page.locator('li[data-phase="active"] .bv-breathe').first().evaluate((el) => getComputedStyle(el).animationName))
    .toBe("bv-breathe");
});

test("on a phone-width screen the run view fits without horizontal scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await startRun(page, { delayMs: 60, failAt: 11 });
  await expect(page.getByRole("heading", { name: "Run failed" })).toBeVisible({ timeout: 20_000 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/run-mobile.png`, fullPage: true });
});
