/**
 * Test support: compare a zod schema with the backend's exported JSON Schema (Pydantic) on what
 * validation depends on — fields, required fields, types, bounds, lengths, enums, formats, nested
 * objects and arrays, closed objects. Used by the contract tests only.
 *
 * The two generators spell the same thing differently, so both sides are normalised: zod writes
 * a nullable as `type: ["string", "null"]` where Pydantic writes `anyOf`; zod stamps the
 * safe-integer range on every `.int()`, which is no bound; Pydantic uses `$ref` where zod inlines.
 */
import { z } from "zod";

export type JsonSchema = {
  properties?: Record<string, JsonSchema>;
  required?: string[];
  type?: string | string[];
  enum?: unknown[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  anyOf?: JsonSchema[];
  items?: JsonSchema;
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  additionalProperties?: boolean;
  format?: string;
};

const bound = (n: number | undefined) =>
  n === undefined || Math.abs(n) >= Number.MAX_SAFE_INTEGER ? undefined : n;

function byJson(a: unknown, b: unknown): number {
  return JSON.stringify(a).localeCompare(JSON.stringify(b));
}

export function shape(s: JsonSchema, defs: Record<string, JsonSchema>): unknown {
  if (s.$ref !== undefined) return shape(defs[s.$ref.split("/").at(-1) ?? ""] ?? {}, defs);
  if (Array.isArray(s.type)) {
    return shape({ anyOf: s.type.map((t) => (t === "null" ? { type: "null" } : { ...s, type: t })) }, defs);
  }
  if (s.anyOf !== undefined) return { anyOf: s.anyOf.map((x) => shape(x, defs)).sort(byJson) };
  const type = s.type ?? (s.const !== undefined ? typeof s.const : undefined);
  const out: Record<string, unknown> = {
    type,
    enum: s.enum ?? (s.const !== undefined ? [s.const] : undefined),
    minimum: bound(s.minimum),
    maximum: bound(s.maximum),
    minLength: s.minLength,
    maxLength: s.maxLength,
    format: s.format === "date-time" || s.format === "uuid" ? s.format : undefined,
  };
  if (type === "object") {
    out.properties = Object.fromEntries(
      Object.entries(s.properties ?? {})
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => [k, shape(v, defs)]),
    );
    out.required = [...(s.required ?? [])].sort();
    out.additionalProperties = s.additionalProperties;
  }
  if (type === "array" && s.items !== undefined) out.items = shape(s.items, defs);
  return JSON.parse(JSON.stringify(out, (_k, x: unknown) => (x === null ? undefined : x)));
}

/**
 * The normalised shapes of both sides. `ignoreRequired`: request schemas whose backend fields
 * have defaults (optional on the wire) while the form states every field explicitly.
 */
export function shapes(
  zodSchema: z.ZodType,
  backend: JsonSchema,
  defs: Record<string, JsonSchema>,
  { ignoreRequired = false } = {},
): { ours: unknown; theirs: unknown } {
  const oursRaw = z.toJSONSchema(zodSchema, { io: "input" }) as JsonSchema;
  const ours = shape(oursRaw, oursRaw.$defs ?? {}) as Record<string, unknown>;
  const theirs = shape(backend, defs) as Record<string, unknown>;
  if (ignoreRequired) {
    delete ours.required;
    delete theirs.required;
  }
  return { ours, theirs };
}
