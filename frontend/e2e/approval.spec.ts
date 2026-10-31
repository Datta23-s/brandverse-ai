/**
 * Phases.md Phase 6 chunk E: the approval path in a real browser, against the real API, the real
 * ARQ worker running the pipeline with the mock agents (PIPELINE_AGENTS=mock), Redis and Postgres.
 *
 * Stub mode only (CI): deciding needs the `approver` role, which the fixed user A gets from
 * backend/scripts/e2e_grant_approver.py on a throwaway database. The live Supabase project's
 * memberships are never written by a test.
 */
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { AUTH_MODE, FIXTURE_PRODUCT, ensureOrganisation, ensureRunnableProduct, signIn } from "./support";

test.skip(AUTH_MODE !== "stub", "needs the approver role, granted only on the CI database");

test.beforeEach(async ({ page }) => {
  await signIn(page, "A");
  await ensureOrganisation(page, "A");
  await ensureRunnableProduct(page);
  const url = process.env.DATABASE_URL;
  if (!url) throw new Error("DATABASE_URL is not set for the approver grant");
  execFileSync(
    "uv",
    ["--directory", resolve(__dirname, "..", "..", "backend"), "run", "python", "-m", "scripts.e2e_grant_approver", url],
    { stdio: "pipe" },
  );
});

async function startParkedRun(page: Page) {
  await page.goto("/");
  await page.getByLabel("Real run product").selectOption({ label: FIXTURE_PRODUCT });
  await page.getByLabel("Audience").fill("commuters");
  await page.getByRole("button", { name: "Start run" }).click();
  await page.waitForURL(/\/runs\/[0-9a-f-]{36}$/, { waitUntil: "commit" });
  await expect(page.getByTestId("approval-bar")).toBeVisible({ timeout: 45_000 });
}

const outcome = (page: Page) => page.getByTestId("campaign-outcome");

test("the approver reads the copy beside its findings, edits a statement out, and approves", async ({ page }) => {
  await startParkedRun(page);
  await expect(page.getByRole("heading", { name: "Waiting for your decision" })).toBeVisible();
  await expect(page.getByTestId("campaign-copy").locator('[data-part="headline"]')).not.toBeEmpty();
  await expect(page.getByTestId("gate-outcome")).toHaveText(/pass|flag/);
  // A7's advisory is stored on the backend and is nowhere in the page.
  await expect(page.getByTestId("campaign")).not.toContainText(/advisory/i);

  // Edit-out removes the words and re-runs the gates; the approver rewrites nothing.
  await page.getByLabel("Exact words to remove").fill(" in selected stores");
  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByTestId("edited-out")).toContainText("in selected stores", { timeout: 20_000 });
  await expect(page.getByTestId("campaign-copy").locator('[data-part="body"]')).toHaveText("Available now.");

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(outcome(page)).toHaveText("Campaign approved.", { timeout: 30_000 });
  await expect(page.getByRole("heading", { name: "Pipeline complete" })).toBeVisible();
  await expect(page.getByTestId("approval-bar")).toHaveCount(0);
});

test("a rejection needs a reason and ends the run completed with a rejected campaign", async ({ page }) => {
  await startParkedRun(page);
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Say why: a reason is required to revise or reject.")).toBeVisible();
  await page.getByLabel(/^Reason/).fill("The tone does not fit the brand.");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(outcome(page)).toContainText("Campaign rejected", { timeout: 30_000 });
  // Completed, not failed and not partial: a rejection is a result.
  await expect(page.getByRole("heading", { name: "Pipeline complete" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Run failed" })).toHaveCount(0);
});

test("a revision is a bounded round: two, then none left", async ({ page }) => {
  await startParkedRun(page);
  await expect(page.getByTestId("rounds-remaining")).toHaveText("2 revision rounds remaining");
  await page.getByLabel(/^Reason/).fill("Try again.");
  await page.getByRole("button", { name: "Request a revision" }).click();
  await expect(page.getByTestId("rounds-remaining")).toHaveText("1 revision round remaining", { timeout: 30_000 });
  await page.getByLabel(/^Reason/).fill("Once more.");
  await page.getByRole("button", { name: "Request a revision" }).click();
  await expect(page.getByTestId("rounds-remaining")).toHaveText("0 revision rounds remaining", { timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Request a revision" })).toBeDisabled();
});
