/**
 * Rules.md P2: the Supabase secret key must never reach client code. This scans what is actually
 * shipped to browsers — the built `.next/static` assets and the prerendered pages — after
 * `next build`. Run with `npm run test:bundle` (CI runs it after the build step).
 *
 * Positive control: the PUBLISHABLE key must be found, or the scan is looking in the wrong place
 * and its silence proves nothing.
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const FRONTEND = resolve(__dirname, "..");
const NEXT = join(FRONTEND, ".next");
const SHIPPED_DIRS = [join(NEXT, "static"), join(NEXT, "server", "app")];
const SHIPPED_EXT = /\.(js|css|html|rsc|json|txt|map)$/;

function walk(dir: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : SHIPPED_EXT.test(name) ? [path] : [];
  });
}

/** Values from a dotenv file, without a dependency. */
function dotenv(path: string): Record<string, string> {
  if (!existsSync(path)) return {};
  return Object.fromEntries(
    readFileSync(path, "utf8")
      .split(/\r?\n/)
      .filter((l) => /^[A-Z0-9_]+=/.test(l))
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i), l.slice(i + 1).trim().replace(/^["']|["']$/g, "")];
      }),
  );
}

const files = walk(SHIPPED_DIRS[0]!).concat(walk(SHIPPED_DIRS[1]!));
const contents = files.map((f) => ({ f, text: readFileSync(f, "utf8") }));
const rootEnv = dotenv(join(FRONTEND, "..", ".env"));
const localEnv = dotenv(join(FRONTEND, ".env.local"));

describe("the built client bundle (Rules.md P2)", () => {
  it("exists: run `npm run build` first", () => {
    expect(existsSync(join(NEXT, "static")), ".next/static missing").toBe(true);
    expect(files.length).toBeGreaterThan(10);
  });

  it("contains the publishable key (positive control: the scan reads what ships)", () => {
    const publishable =
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? localEnv.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
    expect(publishable, "no publishable key configured for the build").toBeTruthy();
    expect(contents.some(({ text }) => text.includes(publishable!))).toBe(true);
  });

  it("contains no secret key: no key-shaped value, not its name, not its value", () => {
    const secret = process.env.SUPABASE_SECRET_KEY ?? rootEnv.SUPABASE_SECRET_KEY;
    const leaks = contents.flatMap(({ f, text }) => {
      const found: string[] = [];
      // A key, not the bare prefix: supabase-js (and our own guard) test `startsWith("sb_secret_")`.
      if (/sb_secret_[A-Za-z0-9_-]{16,}/.test(text)) found.push("an sb_secret_ key");
      if (text.includes("SUPABASE_SECRET_KEY")) found.push("SUPABASE_SECRET_KEY name");
      if (secret && text.includes(secret)) found.push("the secret key's value");
      return found.map((what) => `${what} in ${f.slice(FRONTEND.length + 1)}`);
    });
    expect(leaks).toEqual([]);
  });

  it("contains no service_role JWT (the legacy form of a secret key)", () => {
    const jwt = /eyJ[A-Za-z0-9_-]{10,}\.(eyJ[A-Za-z0-9_-]{10,})\.[A-Za-z0-9_-]{10,}/g;
    const serviceRole = contents.flatMap(({ f, text }) =>
      [...text.matchAll(jwt)]
        .filter((m) => {
          try {
            return JSON.parse(Buffer.from(m[1]!, "base64url").toString("utf8")).role === "service_role";
          } catch {
            return false;
          }
        })
        .map(() => f),
    );
    expect(serviceRole).toEqual([]);
  });

  it("no NEXT_PUBLIC_ variable is named like a secret", () => {
    const names = [...Object.keys(localEnv), ...Object.keys(process.env)].filter((n) =>
      n.startsWith("NEXT_PUBLIC_"),
    );
    expect(names.filter((n) => /SECRET|SERVICE_ROLE|PRIVATE/.test(n))).toEqual([]);
  });
});
