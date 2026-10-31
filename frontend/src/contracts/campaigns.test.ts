/**
 * The campaign contracts' zod schemas agree with the backend's exported contract, and the approver's
 * view has no field that could carry A7's advisory (ruled 2026-10-29).
 */
import { describe, expect, it } from "vitest";

import contracts from "./api-contracts.json";
import {
  campaignViewSchema,
  decisionAcceptedSchema,
  decisionRequestSchema,
  decisionViewSchema,
  editOutAcceptedSchema,
  editOutRequestSchema,
  findingViewSchema,
  statementViewSchema,
} from "./campaigns";
import { runRequestSchema } from "./runs";
import { shapes, type JsonSchema } from "./schema-compare";

const schemas = contracts.schemas as unknown as Record<string, JsonSchema>;

describe("contract: campaigns agree with the backend", () => {
  it("CampaignView: same fields (copy parts compared as a string map)", () => {
    const backend = schemas.CampaignView!;
    expect(Object.keys(campaignViewSchema.shape).sort()).toEqual(Object.keys(backend.properties ?? {}).sort());
    expect([...(backend.required ?? [])].sort()).toEqual(Object.keys(campaignViewSchema.shape).sort());
    expect(backend.properties?.copy_parts?.additionalProperties).toEqual({ type: "string" });
  });

  it.each([
    ["FindingView", findingViewSchema],
    ["StatementView", statementViewSchema],
    ["DecisionView", decisionViewSchema],
  ] as const)("%s", (name, schema) => {
    const defs = schemas.CampaignView!.$defs ?? {};
    const { ours, theirs } = shapes(schema, defs[name] ?? {}, defs);
    expect(ours).toEqual(theirs);
  });

  it("DecisionRequest, DecisionAccepted, EditOutRequest, EditOutAccepted", () => {
    for (const [name, schema, opts] of [
      ["DecisionRequest", decisionRequestSchema, { ignoreRequired: true }],
      ["DecisionAccepted", decisionAcceptedSchema, {}],
      ["EditOutRequest", editOutRequestSchema, {}],
      ["EditOutAccepted", editOutAcceptedSchema, {}],
    ] as const) {
      const backend = schemas[name]!;
      const { ours, theirs } = shapes(schema, backend, backend.$defs ?? {}, opts);
      expect(ours, name).toEqual(theirs);
    }
  });

  it("RunRequest carries the audience the fake-run contract does not", () => {
    const backend = schemas.RunRequest!;
    const { ours, theirs } = shapes(runRequestSchema, backend, {}, { ignoreRequired: true });
    expect(ours).toEqual(theirs);
    expect(Object.keys(backend.properties ?? {})).toContain("audience");
    expect(Object.keys(schemas.FakeRunRequest!.properties ?? {})).not.toContain("audience");
  });

  it("nothing the approver is sent can carry the advisory", () => {
    expect(JSON.stringify(schemas.CampaignView)).not.toMatch(/advisory/i);
    expect(JSON.stringify(Object.keys(campaignViewSchema.shape))).not.toMatch(/advisory/i);
  });
});
