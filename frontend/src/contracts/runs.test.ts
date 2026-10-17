/**
 * The run contracts' zod schemas agree with the backend's exported contract (api-contracts.json,
 * generated from the Pydantic models): fields, required fields, bounds, enums.
 */
import { describe, expect, it } from "vitest";
import type { z } from "zod";

import contracts from "./api-contracts.json";
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
import { shapes, type JsonSchema } from "./schema-compare";

const schemas = contracts.schemas as unknown as Record<string, JsonSchema>;
const eventDefs = schemas.RunEvent?.$defs ?? {};

describe("contract: zod agrees with the backend", () => {
  it("FakeRunRequest: same fields, bounds and enum (defaults make most optional on the backend)", () => {
    const backend = schemas.FakeRunRequest!;
    const { ours, theirs } = shapes(fakeRunRequestSchema, backend, {}, { ignoreRequired: true });
    expect(ours).toEqual(theirs);
    expect(backend.required).toEqual(["org_id"]); // the one field without a default
  });

  it("RunAccepted", () => {
    const { ours, theirs } = shapes(runAcceptedSchema, schemas.RunAccepted!, {});
    expect(ours).toEqual(theirs);
  });

  it.each([
    ["RunQueued", runQueuedSchema],
    ["RunStarted", runStartedSchema],
    ["StepEvent", stepEventSchema],
    ["RunCompleted", runCompletedSchema],
    ["RunFailed", runFailedSchema],
  ] as [string, z.ZodType][])("%s", (name, schema) => {
    const { ours, theirs } = shapes(schema, eventDefs[name] ?? {}, eventDefs);
    expect(ours).toEqual(theirs);
  });

  it("step statuses", () => {
    expect(stepStatusSchema.options).toEqual(eventDefs.StepStatus?.enum);
  });

  it("the step list is the backend's", () => {
    expect(PIPELINE_STEPS).toEqual(contracts.pipeline_steps);
    expect(PIPELINE_STEPS.map((s) => s.number)).toEqual([...Array(15).keys()].map((i) => i + 1));
  });
});
