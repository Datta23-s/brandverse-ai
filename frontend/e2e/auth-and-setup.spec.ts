/**
 * Phases.md Phase 1 exit criterion: "Sign up → create brand → create product works end to end",
 * and the auth behaviour around it.
 */
import { expect, test } from "@playwright/test";

import { AUTH_MODE, credentials, ensureOrganisation, signIn } from "./support";

test("a signed-out visitor is sent to sign in, then back where they were going", async ({ page }) => {
  const runPath = "/runs/00000000-0000-4000-8000-000000000000";
  await page.goto(runPath);
  await page.waitForURL(/\/sign-in\?next=/);
  const { email, password } = credentials("A");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL((url) => url.pathname === runPath, { waitUntil: "commit" });
});

test("a wrong password is refused with a message", async ({ page }) => {
  await page.goto("/sign-in");
  await page.getByLabel("Email").fill(credentials("A").email);
  await page.getByLabel("Password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText(/invalid login credentials/i);
  expect(new URL(page.url()).pathname).toBe("/sign-in");
});

test("sign up validates before sending anything", async ({ page }) => {
  await page.goto("/sign-up");
  await page.getByLabel("Email").fill("not-an-email");
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByLabel("Confirm password").fill("different");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Enter a valid email address.")).toBeVisible();
  await expect(page.getByText("At least 8 characters.")).toBeVisible();
  await expect(page.getByText("Passwords do not match.")).toBeVisible();
});

test("sign up asks the new user to confirm their email", async ({ page }) => {
  // Against the live project this would send a real email (only to team addresses on the
  // default SMTP), so it runs against the Auth stand-in; the live sign-up is done once by hand.
  test.skip(AUTH_MODE !== "stub", "live sign-up is demonstrated by hand, not automated");
  await page.goto("/sign-up");
  await page.getByLabel("Email").fill("new-user@ci.example.com");
  await page.getByLabel("Password", { exact: true }).fill("a-long-password");
  await page.getByLabel("Confirm password").fill("a-long-password");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("status")).toContainText("Check your email.");
  await expect(page.getByRole("status")).toContainText("new-user@ci.example.com");
});

test("signed in: create a brand, then a product, then remove both", async ({ page }) => {
  await signIn(page, "A");
  await ensureOrganisation(page, "A");
  await expect(page.getByTestId("signed-in-as")).toHaveText(credentials("A").email);

  // Remove what an earlier, aborted run left behind (deleting a brand removes its products).
  const leftovers = page.getByRole("button", { name: /^Delete brand E2E brand / });
  await expect(page.getByRole("button", { name: "Add brand" })).toBeVisible();
  for (let n = await leftovers.count(); n > 0; n--) {
    await leftovers.first().click();
    await expect(leftovers).toHaveCount(n - 1);
  }

  const brand = `E2E brand ${Date.now()}`;
  const brandButton = page.getByRole("list", { name: "Brands" }).getByRole("button", { name: brand, exact: true });
  await page.getByLabel("New brand").fill(brand);
  await page.getByLabel("Sector", { exact: true }).selectOption("footwear");
  await page.getByRole("button", { name: "Add brand" }).click();
  await expect(brandButton).toBeVisible();
  await expect(page.getByLabel(`Sector of ${brand}`)).toHaveValue("footwear");

  await page.getByLabel("New product").fill("Trail runner");
  await page.getByLabel("Category (optional)").fill("shoes");
  await page.getByRole("button", { name: "Add product" }).click();
  const products = page.getByRole("list", { name: `Products of ${brand}` });
  await expect(products).toContainText("Trail runner");
  await expect(products).toContainText("shoes");

  // Survives a reload: it is in the database, not only in the page.
  await page.reload();
  await brandButton.click();
  await expect(page.getByRole("list", { name: `Products of ${brand}` })).toContainText("Trail runner");

  // Clean up (owners may delete; nothing holds these rows).
  await page.getByRole("button", { name: "Delete product Trail runner" }).click();
  await expect(page.getByRole("list", { name: `Products of ${brand}` })).not.toContainText("Trail runner");
  await page.getByRole("button", { name: `Delete brand ${brand}` }).click();
  await expect(brandButton).toHaveCount(0);
});

test("signing out ends the session", async ({ page }) => {
  await signIn(page, "A");
  await ensureOrganisation(page, "A");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/sign-in/);
  await page.goto("/");
  await page.waitForURL(/\/sign-in\?next=/);
});

test("a product with no verified claim cannot start a run; a claim is entered with evidence, then retired", async ({ page }) => {
  // Phases.md Phase 3 exit criterion 1, in the browser.
  await signIn(page, "A");
  await ensureOrganisation(page, "A");
  await expect(page.getByRole("button", { name: "Add brand" })).toBeVisible();

  const brand = `E2E brand ${Date.now()}`;
  await page.getByLabel("New brand").fill(brand);
  await page.getByLabel("Sector", { exact: true }).selectOption("footwear");
  await page.getByRole("button", { name: "Add brand" }).click();
  await page.getByRole("list", { name: "Brands" }).getByRole("button", { name: brand, exact: true }).click();
  await page.getByLabel("New product").fill("Claimless runner");
  await page.getByRole("button", { name: "Add product" }).click();
  await expect(page.getByRole("list", { name: `Products of ${brand}` })).toContainText("Claimless runner");

  // Refused, with the reason, and nothing started.
  await page.getByLabel("Product", { exact: true }).selectOption({ label: "Claimless runner" });
  await page.getByRole("button", { name: "Start fake run" }).click();
  const refusal = page.getByRole("main").getByRole("alert").filter({ hasText: "Run blocked" });
  await expect(refusal).toContainText("Nothing was started.");
  await expect(refusal.locator('[data-reason="no_verified_claims"]')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/");

  // A verified claim needs its evidence link.
  await page.getByRole("button", { name: "Claims of product Claimless runner" }).click();
  const claims = page.getByRole("region", { name: "Claims of Claimless runner" });
  await claims.getByLabel("Claim", { exact: true }).fill("Weighs 240 g");
  await claims.getByRole("button", { name: "Add claim" }).click();
  await expect(claims.getByText("A verified claim needs its evidence link.")).toBeVisible();
  await claims.getByLabel("Evidence link").fill("https://example.com/e2e-spec");
  await claims.getByRole("button", { name: "Add claim" }).click();
  const row = claims.locator('li[data-claim-type="verified"]');
  await expect(row).toContainText("Weighs 240 g");
  await expect(row).toContainText("Verified");

  // Retired with a reason; it stays on record.
  await row.getByRole("button", { name: "Retire claim Weighs 240 g" }).click();
  await row.getByLabel("Why retire it").fill("spec sheet withdrawn");
  await row.getByRole("button", { name: "Retire claim", exact: true }).click();
  await expect(row).toHaveAttribute("data-retired", "true");
  await expect(row).toContainText("Retired: spec sheet withdrawn");

  // Clean up: the product has no runs, so deleting it removes its claims too (0017).
  await page.getByRole("button", { name: "Delete product Claimless runner" }).click();
  await page.getByRole("button", { name: `Delete brand ${brand}` }).click();
  await expect(page.getByRole("list", { name: "Brands" }).getByRole("button", { name: brand, exact: true })).toHaveCount(0);
});
