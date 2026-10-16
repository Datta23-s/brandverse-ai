/**
 * The zod schemas agree with the backend's exported contract (run-contracts.json, generated from
 * the Pydantic models). Compares what both sides accept: fields, required fields, bounds, enums.
 */
import { describe, expect, it } from "vitest";
import { z } from "zod";

import contracts from "./run-contracts.json";
import {
  PIPELINE_STEPS,
  fakeRunRequestSchema,
  runAcceptedSchema,
  runCompletedSchema,
  runFailedSchema,
  runQueuedSchema,
  runStartedSchema,
  stepEventSchema,
  stepStatusSchema,
} from "./runs";

type JsonSchema = {
  properties?: Record<string, JsonSchema>;
  required?: string[];
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  anyOf?: JsonSchema[];
  $ref?: string;
  additionalProperties?: boolean;
  format?: string;
};

const defs = contracts.schemas.RunEvent.$defs as Record<string, JsonSchema>;

/**
 * Reduce a property schema to what validation depends on, written the same way for both sides.
 * The two generators spell the same thing differently: zod writes a nullable as
 * `type: ["string", "null"]` where Pydantic writes `anyOf`, and stamps the safe-integer range on
 * every `.int()`, which is no bound at all.
 */
function shape(s: JsonSchema): unknown {
  if (s.$ref !== undefined) return shape(defs[s.$ref.split("/").at(-1) ?? ""] ?? {});
  if (Array.isArray(s.type)) {
    return shape({ anyOf: s.type.map((t) => (t === "null" ? { type: "null" } : { ...s, type: t })) });
  }
  if (s.anyOf !== undefined) {
    return { anyOf: s.anyOf.map(shape).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))) };
  }
  const bound = (n: number | undefined) => (n === undefined || Math.abs(n) >= Number.MAX_SAFE_INTEGER ? undefined : n);
  return {
    type: s.type ?? (s.const !== undefined ? typeof s.const : undefined),
    enum: s.enum ?? (s.const !== undefined ? [s.const] : undefined),
    minimum: bound(s.minimum),
    maximum: bound(s.maximum),
    format: s.format === "date-time" || s.format === "uuid" ? s.format : undefined,
  };
}

function compare(zodSchema: z.ZodType, backend: JsonSchema, { allOptional = false } = {}) {
  const ours = z.toJSONSchema(zodSchema, { io: "input" }) as JsonSchema;
  const ourProps = ours.properties ?? {};
  const theirProps = backend.properties ?? {};
  expect(Object.keys(ourProps).sort()).toEqual(Object.keys(theirProps).sort());
  for (const [name, prop] of Object.entries(theirProps)) {
    const mine = ourProps[name];
    expect(mine, name).toBeDefined();
    // zod emits "integer" for z.number().int(); its uuid/datetime formats normalise as above.
    expect(normalise(shape(mine!)), name).toEqual(normalise(shape(prop)));
  }
  if (!allOptional) expect([...(ours.required ?? [])].sort()).toEqual([...(backend.required ?? [])].sort());
  expect(ours.additionalProperties).toBe(false);
  expect(backend.additionalProperties).toBe(false);
}

function normalise(v: unknown): unknown {
  return JSON.parse(JSON.stringify(v, (_k, x: unknown) => (x === null ? undefined : x)));
}

describe("contract: zod agrees with the backend", () => {
  it("FakeRunRequest: same fields, bounds and enum (all optional on the backend)", () => {
    compare(fakeRunRequestSchema, contracts.schemas.FakeRunRequest as JsonSchema, { allOptional: true });
  });

  it("RunAccepted", () => {
    compare(runAcceptedSchema, contracts.schemas.RunAccepted as JsonSchema);
  });

  it.each([
    ["RunQueued", runQueuedSchema],
    ["RunStarted", runStartedSchema],
    ["StepEvent", stepEventSchema],
    ["RunCompleted", runCompletedSchema],
    ["RunFailed", runFailedSchema],
  ] as const)("%s", (name, schema) => {
    compare(schema, defs[name] ?? {});
  });

  it("step statuses", () => {
    expect(stepStatusSchema.options).toEqual(defs.StepStatus?.enum);
  });

  it("the step list is the backend's", () => {
    expect(PIPELINE_STEPS).toEqual(contracts.pipeline_steps);
    expect(PIPELINE_STEPS.map((s) => s.number)).toEqual([...Array(15).keys()].map((i) => i + 1));
  });
});
