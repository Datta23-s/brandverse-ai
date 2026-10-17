/**
 * Product claims, mirroring backend/app/models/claims.py. `claims.test.ts` checks these against
 * the exported contract (api-contracts.json).
 *
 * Who entered or retired a claim is never sent: the request schemas have no such field, and the
 * database derives both from the signed-in user (migration 0017).
 */
import { z } from "zod";

export const claimTypeSchema = z.enum(["verified", "deduced", "restricted"]);
export type ClaimType = z.infer<typeof claimTypeSchema>;

const text = z.string().trim().min(1, "Required.").max(500, "At most 500 characters.");
const url = z
  .string()
  .trim()
  .max(2048, "At most 2048 characters.")
  .regex(/^https?:\/\/\S+$/, "An http(s) link a reader can open.");
const note = z.string().trim().min(1, "Required.").max(2000, "At most 2000 characters.");

/** The fields of a new claim, before the per-type rules. */
export const productClaimFields = z.strictObject({
  org_id: z.uuid(),
  claim: text,
  claim_type: claimTypeSchema,
  evidence_url: url.nullable(),
  evidence_note: note.nullable(),
  confidence: z.number().min(0).max(1).nullable(),
});

type ClaimRuleInput = Pick<z.output<typeof productClaimFields>, "claim_type" | "evidence_url" | "evidence_note" | "confidence">;

/** The rules each type carries (the same as the backend's and the database's CHECKs). */
export function claimTypeRules(c: ClaimRuleInput, ctx: z.RefinementCtx): void {
  if (c.claim_type === "verified" && c.evidence_url === null) {
    ctx.addIssue({ code: "custom", path: ["evidence_url"], message: "A verified claim needs its evidence link." });
  }
  if (c.claim_type === "deduced") {
    if (c.confidence === null) {
      ctx.addIssue({ code: "custom", path: ["confidence"], message: "A deduced claim needs a confidence." });
    }
    if (c.evidence_url === null && c.evidence_note === null) {
      ctx.addIssue({ code: "custom", path: ["evidence_note"], message: "Give a link or a note it is based on." });
    }
  }
  if (c.claim_type === "restricted" && c.evidence_note === null) {
    ctx.addIssue({ code: "custom", path: ["evidence_note"], message: "Say why it may not be claimed." });
  }
}

export const productClaimCreateSchema = productClaimFields.superRefine(claimTypeRules);
export type ProductClaimCreate = z.infer<typeof productClaimCreateSchema>;

export const claimRetireSchema = z.strictObject({
  org_id: z.uuid(),
  reason: z.string().trim().min(1, "Required.").max(500, "At most 500 characters."),
});
export type ClaimRetire = z.infer<typeof claimRetireSchema>;

export const productClaimSchema = z.strictObject({
  id: z.uuid(),
  org_id: z.uuid(),
  product_id: z.uuid(),
  claim: z.string(),
  claim_type: claimTypeSchema,
  evidence_url: z.string().nullable(),
  evidence_note: z.string().nullable(),
  confidence: z.number().nullable(),
  created_by: z.string(),
  entered_by: z.uuid().nullable(),
  created_at: z.iso.datetime({ offset: true }),
  retired_at: z.iso.datetime({ offset: true }).nullable(),
  retired_by: z.uuid().nullable(),
  retired_reason: z.string().nullable(),
});
export type ProductClaim = z.infer<typeof productClaimSchema>;
