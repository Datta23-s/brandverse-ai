import { describe, expect, it } from "vitest";
import type { z } from "zod";

import contracts from "./api-contracts.json";
import { claimRetireSchema, productClaimCreateSchema, productClaimSchema } from "./claims";
import { shapes, type JsonSchema } from "./schema-compare";

const schemas = contracts.schemas as unknown as Record<string, JsonSchema>;
const ORG = "00000000-0000-4000-8000-000000000001";

describe("contract: claims zod agrees with the backend", () => {
  it.each([
    // evidence_url, evidence_note and confidence default to null on the backend; the form sends them.
    ["ProductClaimCreate", productClaimCreateSchema, true],
    ["ClaimRetire", claimRetireSchema, false],
    ["ProductClaim", productClaimSchema, false],
  ] as [string, z.ZodType, boolean][])("%s", (name, schema, ignoreRequired) => {
    const backend = schemas[name];
    expect(backend, name).toBeDefined();
    const { ours, theirs } = shapes(schema, backend!, backend!.$defs ?? {}, { ignoreRequired });
    expect(ours).toEqual(theirs);
  });
});

describe("each claim type carries its rule", () => {
  const base = { org_id: ORG, claim: "Weighs 240 g", evidence_url: null, evidence_note: null, confidence: null };
  const issues = (v: object) =>
    productClaimCreateSchema.safeParse({ ...base, ...v }).error?.issues.map((i) => i.path.join(".")) ?? [];

  it("verified needs its evidence link", () => {
    expect(issues({ claim_type: "verified" })).toEqual(["evidence_url"]);
    expect(issues({ claim_type: "verified", evidence_url: "https://example.com/spec" })).toEqual([]);
    expect(issues({ claim_type: "verified", evidence_url: "example.com" })).toEqual(["evidence_url"]);
  });

  it("deduced needs a confidence and a basis", () => {
    expect(issues({ claim_type: "deduced" })).toEqual(["confidence", "evidence_note"]);
    expect(issues({ claim_type: "deduced", confidence: 0.6, evidence_note: "from the spec" })).toEqual([]);
  });

  it("restricted needs the reason", () => {
    expect(issues({ claim_type: "restricted" })).toEqual(["evidence_note"]);
    expect(issues({ claim_type: "restricted", evidence_note: "superlative" })).toEqual([]);
  });

  it("who entered a claim cannot be sent", () => {
    const sent = { ...base, claim_type: "verified", evidence_url: "https://example.com/spec", created_by: "human" };
    expect(productClaimCreateSchema.safeParse(sent).success).toBe(false);
  });
});
