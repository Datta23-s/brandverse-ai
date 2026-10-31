/**
 * Campaign and decision contracts, mirroring backend/app/models/campaigns.py.
 *
 * The approver sees the copy and its gate findings. A7's advisory is stored with the campaign on the
 * backend and is not in any of these schemas (ruled 2026-10-29: the approver does not see it, in
 * calibration and evaluation alike); `campaigns.test.ts` fails if a field of that name appears.
 */
import { z } from "zod";

export const campaignStatusSchema = z.enum(["draft", "awaiting_approval", "approved", "rejected"]);
export type CampaignStatus = z.infer<typeof campaignStatusSchema>;

export const findingViewSchema = z.strictObject({
  gate: z.string(),
  kind: z.string(),
  outcome: z.enum(["flag", "block"]),
  message: z.string(),
  /** The exact words the finding is about, when it names some. */
  span: z.string().nullable(),
  statement: z.string().nullable(),
});
export type FindingView = z.infer<typeof findingViewSchema>;

export const statementViewSchema = z.strictObject({ text: z.string(), cited: z.boolean() });

export const decisionViewSchema = z.strictObject({
  outcome: z.enum(["approve", "revise", "reject", "undecided"]),
  reason: z.string().nullable(),
  failed_gate: z.string().nullable(),
  revision_round: z.number().int(),
  decided_at: z.iso.datetime({ offset: true }),
});
export type DecisionView = z.infer<typeof decisionViewSchema>;

export const campaignViewSchema = z.strictObject({
  campaign_id: z.uuid(),
  run_id: z.uuid(),
  status: campaignStatusSchema,
  revision_round: z.number().int(),
  rounds_remaining: z.number().int(),
  gate_outcome: z.enum(["pass", "flag", "block"]).nullable(),
  copy_parts: z.record(z.string(), z.string()),
  findings: z.array(findingViewSchema),
  statements: z.array(statementViewSchema),
  edited_out: z.array(z.string()),
  decisions: z.array(decisionViewSchema),
});
export type CampaignView = z.infer<typeof campaignViewSchema>;

export type DecisionKind = "approve" | "revise" | "reject";

/** POST /api/v1/runs/{id}/approve | revise | reject. A reason is required for anything but approve. */
export const decisionRequestSchema = z.strictObject({
  reason: z.string().max(2000).nullable(),
  failed_gate: z.string().max(40).nullable(),
  seconds_spent: z.number().int().min(0).max(86_400),
});
export type DecisionRequest = z.infer<typeof decisionRequestSchema>;

export const decisionAcceptedSchema = z.strictObject({
  run_id: z.uuid(),
  outcome: z.enum(["approve", "revise", "reject"]),
  revision_round: z.number().int(),
  /** False when this exact decision was already on record for the round. */
  recorded: z.boolean(),
});
export type DecisionAccepted = z.infer<typeof decisionAcceptedSchema>;

export const editOutRequestSchema = z.strictObject({ statement: z.string().min(1).max(1500) });
export const editOutAcceptedSchema = z.strictObject({ run_id: z.uuid() });
