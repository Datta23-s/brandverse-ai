/**
 * End-to-end support. Two environments, same specs:
 * - "supabase" (default, on the laptop): the real Supabase project, with the two fixed E2E users
 *   from the repository's git-ignored `.env.e2e` (backend/scripts/ensure_e2e_users.py).
 * - "stub" (CI): backend/scripts/e2e_fake_auth.py stands in for Supabase Auth; its users come
 *   from E2E_A_* / E2E_B_* environment variables.
 * Every sign-in goes through the real sign-in form.
 */
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page } from "@playwright/test";

export const AUTH_MODE = (process.env.E2E_AUTH_MODE ?? "supabase") as "supabase" | "stub";

const fileEnv: Record<string, string> = (() => {
  const path = resolve(__dirname, "..", "..", ".env.e2e");
  if (!existsSync(path)) return {};
  return Object.fromEntries(
    readFileSync(path, "utf8")
      .split(/\r?\n/)
      .filter((l) => /^E2E_[A-Z_]+=/.test(l))
      .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1).trim()]),
  );
})();

export type Who = "A" | "B";

export function credentials(who: Who): { email: string; password: string } {
  const email = process.env[`E2E_${who}_EMAIL`] ?? fileEnv[`E2E_${who}_EMAIL`];
  const password = process.env[`E2E_${who}_PASSWORD`] ?? fileEnv[`E2E_${who}_PASSWORD`];
  if (!email || !password) {
    throw new Error(
      `No credentials for E2E user ${who}: set E2E_${who}_EMAIL/PASSWORD, or run ` +
        "`uv run python -m scripts.ensure_e2e_users` (backend/) to create .env.e2e",
    );
  }
  return { email, password };
}

export async function signIn(page: Page, who: Who): Promise<void> {
  const { email, password } = credentials(who);
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => url.pathname === "/", { waitUntil: "commit" });
}

/** Signed in at "/": the user's organisation exists (created through the UI the first time). */
export async function ensureOrganisation(page: Page, who: Who): Promise<void> {
  const create = page.getByRole("heading", { name: "Create your organisation." });
  const roles = page.getByTestId("roles");
  await expect(create.or(roles)).toBeVisible({ timeout: 15_000 });
  if (await create.isVisible()) {
    await page.getByLabel("Organisation name").fill(`E2E Org ${who}`);
    await page.getByRole("button", { name: "Create organisation" }).click();
  }
  await expect(roles).toContainText("owner");
}

export const FIXTURE_BRAND = "E2E run fixture";
export const FIXTURE_PRODUCT = "E2E fixture shoe";

/**
 * Signed in at "/": a product that can start a run exists — a brand with a non-regulated sector,
 * and a product with an active verified claim (Phase 3 admission). Created through the UI the
 * first time and kept: claims are never deleted (migration 0017), so the fixture is reused rather
 * than re-created on every run.
 */
export async function ensureRunnableProduct(page: Page): Promise<void> {
  // Every count below waits for its list to have loaded first: counting while a query is still in
  // flight sees nothing, and creating then would leave a permanent duplicate on the live project
  // (it did once, 2026-10-17; the two empty duplicates were deleted).
  await expect(page.getByRole("button", { name: "Add brand" })).toBeVisible({ timeout: 15_000 });
  const brands = page.getByRole("list", { name: "Brands" });
  await expect(brands.or(page.getByText("No brands yet."))).toBeVisible();
  const brandButton = brands.getByRole("button", { name: FIXTURE_BRAND, exact: true });
  if ((await brandButton.count()) === 0) {
    await page.getByLabel("New brand").fill(FIXTURE_BRAND);
    await page.getByLabel("Sector", { exact: true }).selectOption({ label: "Footwear" });
    await page.getByRole("button", { name: "Add brand" }).click();
  }
  await expect(brandButton).toHaveCount(1); // a second fixture brand is a failure, never ignored
  await brandButton.click();
  const products = page.getByRole("list", { name: `Products of ${FIXTURE_BRAND}` });
  await expect(products.getByRole("listitem").first().or(page.getByText("No products yet."))).toBeVisible();
  if ((await products.getByText(FIXTURE_PRODUCT, { exact: true }).count()) === 0) {
    await page.getByLabel("New product").fill(FIXTURE_PRODUCT);
    await page.getByRole("button", { name: "Add product" }).click();
    await expect(products.getByText(FIXTURE_PRODUCT, { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: `Claims of product ${FIXTURE_PRODUCT}` }).click();
  const claims = page.getByRole("region", { name: `Claims of ${FIXTURE_PRODUCT}` });
  await expect(claims).toBeVisible();
  const verified = claims.locator('li[data-claim-type="verified"][data-retired="false"]');
  // Loaded: either the empty message or the list (counting before then would see nothing).
  await expect(claims.getByText(/^No claims yet/).or(claims.getByRole("list"))).toBeVisible();
  if ((await verified.count()) === 0) {
    await claims.getByLabel("Claim", { exact: true }).fill("Upper made of recycled mesh");
    await claims.getByLabel("Evidence link").fill("https://example.com/e2e-fixture-spec");
    await claims.getByRole("button", { name: "Add claim" }).click();
    await expect(verified.first()).toBeVisible();
  }
}
