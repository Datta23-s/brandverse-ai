/**
 * The org check, in the browser: a member of org B cannot watch org A's run, and is told nothing
 * about whether it exists.
 */
import { expect, test } from "@playwright/test";

import { FIXTURE_PRODUCT, ensureOrganisation, ensureRunnableProduct, signIn } from "./support";

test("a member of another organisation cannot watch the run", async ({ browser }) => {
  const a = await browser.newContext();
  const pageA = await a.newPage();
  await signIn(pageA, "A");
  await ensureOrganisation(pageA, "A");
  await ensureRunnableProduct(pageA);
  await pageA.getByLabel("Product", { exact: true }).selectOption({ label: FIXTURE_PRODUCT });
  await pageA.getByLabel("Time per step (ms)").fill("50");
  await pageA.getByRole("button", { name: "Start fake run" }).click();
  await pageA.waitForURL(/\/runs\/[0-9a-f-]{36}$/, { waitUntil: "commit" });
  const runPath = new URL(pageA.url()).pathname;
  await expect(pageA.getByRole("heading", { name: "Pipeline complete" })).toBeVisible({ timeout: 20_000 });

  const b = await browser.newContext();
  const pageB = await b.newPage();
  await signIn(pageB, "B");
  await ensureOrganisation(pageB, "B");
  await pageB.goto(runPath);
  const refusal = pageB.getByRole("main").getByRole("alert");
  await expect(refusal).toContainText("This run does not exist, or you are not a member of its organisation.");
  await expect(pageB.locator("li[data-step]").first()).toHaveAttribute("data-phase", "pending");

  await a.close();
  await b.close();
});
