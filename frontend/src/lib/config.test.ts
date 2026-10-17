import { describe, expect, it } from "vitest";

import { isSecretKey } from "./config";

const jwt = (payload: object) =>
  `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.c2lnbmF0dXJl`;

describe("isSecretKey (the browser refuses to start with one)", () => {
  it.each([
    ["sb_secret_abc123", true],
    [jwt({ role: "service_role" }), true],
    ["sb_publishable_abc123", false],
    [jwt({ role: "anon" }), false],
    ["", false],
  ])("%s → %s", (key, secret) => expect(isSecretKey(key)).toBe(secret));
});
