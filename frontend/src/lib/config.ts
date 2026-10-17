/**
 * Browser-visible configuration, read once (Rules.md §3.1's rule, applied to the frontend).
 *
 * Only NEXT_PUBLIC_ variables reach the browser, and nothing secret may ever be one (Rules.md P2).
 * The Supabase key here is the PUBLISHABLE key (row-level security applies to it); a secret key
 * is refused outright, and a build-time test checks the bundle (src/security/bundle.test.ts).
 * The API runs on the laptop (Architecture.md §11); 127.0.0.1, not localhost (see there).
 */
export const API_URL: string = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export const SUPABASE_URL: string = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
export const SUPABASE_PUBLISHABLE_KEY: string = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

/** True when a key must never be in a browser: a secret key, or a legacy service-role JWT. */
export function isSecretKey(key: string): boolean {
  if (key.startsWith("sb_secret_")) return true;
  const parts = key.split(".");
  if (parts.length !== 3 || parts[1] === undefined) return false;
  try {
    const payload: unknown = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload === "object" && payload !== null && "role" in payload && payload.role === "service_role";
  } catch {
    return false;
  }
}

if (isSecretKey(SUPABASE_PUBLISHABLE_KEY)) {
  throw new Error(
    "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY holds a secret key. Only the publishable key may reach the browser (Rules.md P2).",
  );
}
