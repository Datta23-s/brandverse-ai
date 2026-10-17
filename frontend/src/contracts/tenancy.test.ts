import { describe, expect, it } from "vitest";
import type { z } from "zod";

import contracts from "./api-contracts.json";
import { shapes, type JsonSchema } from "./schema-compare";
import {
  brandCreateSchema,
  brandSchema,
  meSchema,
  organisationCreateSchema,
  organisationSchema,
  productCreateSchema,
  productSchema,
} from "./tenancy";

const schemas = contracts.schemas as unknown as Record<string, JsonSchema>;

describe("contract: tenancy zod agrees with the backend", () => {
  it.each([
    ["OrganisationCreate", organisationCreateSchema, false],
    ["Organisation", organisationSchema, false],
    ["Me", meSchema, false],
    ["BrandCreate", brandCreateSchema, false],
    ["Brand", brandSchema, false],
    // category defaults to null on the backend; the form always sends it.
    ["ProductCreate", productCreateSchema, true],
    ["Product", productSchema, false],
  ] as [string, z.ZodType, boolean][])("%s", (name, schema, ignoreRequired) => {
    const backend = schemas[name];
    expect(backend, name).toBeDefined();
    const { ours, theirs } = shapes(schema, backend!, backend!.$defs ?? {}, { ignoreRequired });
    expect(ours).toEqual(theirs);
  });

  it("names are trimmed, then 1 to 120 characters", () => {
    expect(organisationCreateSchema.parse({ name: "  Acme  " })).toEqual({ name: "Acme" });
    expect(organisationCreateSchema.safeParse({ name: "   " }).success).toBe(false);
    expect(organisationCreateSchema.safeParse({ name: "x".repeat(121) }).success).toBe(false);
  });
});
