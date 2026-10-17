/**
 * Run contracts, mirroring backend/app/models/runs.py.
 *
 * `api-contracts.json` is generated from the Pydantic models (backend/scripts/export_contracts.py)
 * and checked there; `runs.test.ts` checks these zod schemas against it, so a change on either
 * side that the other has not followed fails a test. The step list is read from the JSON, never
 * retyped.
 */
import { z } from "zod";

import contracts from "./api-contracts.json";

// ── The 15 pipeline steps (backend/app/core/pipeline.py) ─────────────────────────────────────

const pipelineStepSchema = z.object({
  number: z.number().int(),
  name: z.string(),
  title: z.string(),
  nodes: z.array(z.string()),
});
export type PipelineStep = z.infer<typeof pipelineStepSchema>;

export const PIPELINE_STEPS: readonly PipelineStep[] = z
  .array(pipelineStepSchema)
  .length(15)
  .parse(contracts.pipeline_steps);
export const TOTAL_STEPS = PIPELINE_STEPS.length;

// ── API in / out ─────────────────────────────────────────────────────────────────────────────

export const researchDepthSchema = z.enum(["D0", "D1", "D2"]);
export type ResearchDepth = z.infer<typeof researchDepthSchema>;

/** POST /api/v1/runs/fake. Bounds match FakeRunRequest; every field is explicit here. */
export const fakeRunRequestSchema = z.strictObject({
  org_id: z.uuid(),
  /** Admitted only with an active verified claim and a brand sector the registry covers. */
  product_id: z.uuid(),
  brief: z.string().max(2000, "At most 2000 characters.").nullable(),
  depth: researchDepthSchema,
  step_delay_ms: z.number().int().min(0).max(5000),
  fail_at_step: z.number().int().min(1).max(15).nullable(),
});
export type FakeRunRequest = z.infer<typeof fakeRunRequestSchema>;

export const runAcceptedSchema = z.strictObject({
  run_id: z.uuid(),
  stream_url: z.string(),
});
export type RunAccepted = z.infer<typeof runAcceptedSchema>;

/** The 409 body when admission refuses a run: blocked and logged (backend/app/core/admission.py). */
export const runBlockedSchema = z.strictObject({
  message: z.string(),
  reasons: z.array(
    z.strictObject({
      code: z.enum([
        "no_verified_claims",
        "registry_coverage",
        "registry_rule_invalid",
        "restricted_claim_requested",
        "prohibited_phrase_requested",
      ]),
      message: z.string(),
      refs: z.array(z.string()),
    }),
  ),
});
export type RunBlocked = z.infer<typeof runBlockedSchema>;

// ── Events (the SSE stream) ──────────────────────────────────────────────────────────────────

export const stepStatusSchema = z.enum(["started", "completed", "skipped", "failed"]);
export type StepStatus = z.infer<typeof stepStatusSchema>;

const base = { run_id: z.uuid(), ts: z.iso.datetime({ offset: true }) };

export const runQueuedSchema = z.strictObject({
  ...base,
  type: z.literal("run.queued"),
  mode: z.enum(["fake", "pipeline"]),
  org_id: z.uuid(),
  total_steps: z.number().int(),
});
export const runStartedSchema = z.strictObject({
  ...base,
  type: z.literal("run.started"),
  total_steps: z.number().int(),
});
export const stepEventSchema = z.strictObject({
  ...base,
  type: z.literal("step"),
  step: z.number().int().min(1).max(15),
  name: z.string(),
  status: stepStatusSchema,
  detail: z.string().nullable(),
});
export const runCompletedSchema = z.strictObject({ ...base, type: z.literal("run.completed") });
export const runFailedSchema = z.strictObject({
  ...base,
  type: z.literal("run.failed"),
  step: z.number().int().min(1).max(15).nullable(),
  error: z.string(),
});

export const runEventSchema = z.discriminatedUnion("type", [
  runQueuedSchema,
  runStartedSchema,
  stepEventSchema,
  runCompletedSchema,
  runFailedSchema,
]);
export type RunEvent = z.infer<typeof runEventSchema>;
export type StepEvent = z.infer<typeof stepEventSchema>;

export const TERMINAL_EVENT_TYPES: ReadonlySet<RunEvent["type"]> = new Set([
  "run.completed",
  "run.failed",
]);
