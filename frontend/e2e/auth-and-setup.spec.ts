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
  await page.getByRole("button", { name: "Add brand" }).click();
  await expect(brandButton).toBeVisible();

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
