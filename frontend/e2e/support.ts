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
