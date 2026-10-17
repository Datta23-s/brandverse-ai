/**
 * Organisations, brands and products, mirroring backend/app/models/tenancy.py. `tenancy.test.ts`
 * checks these against the exported contract (api-contracts.json).
 */
import { z } from "zod";

export const roleSchema = z.enum(["owner", "editor", "approver", "viewer"]);
export type Role = z.infer<typeof roleSchema>;

/** 1–120 characters after trimming, as the backend (and app.create_organisation) enforce. */
const name = z.string().trim().min(1, "Required.").max(120, "At most 120 characters.");

export const organisationCreateSchema = z.strictObject({ name });
export type OrganisationCreate = z.infer<typeof organisationCreateSchema>;

export const organisationSchema = z.strictObject({
  id: z.uuid(),
  name: z.string(),
  roles: z.array(roleSchema),
});
export type Organisation = z.infer<typeof organisationSchema>;

export const meSchema = z.strictObject({
  user_id: z.uuid(),
  email: z.string().nullable(),
  organisations: z.array(organisationSchema),
});
export type Me = z.infer<typeof meSchema>;

export const brandCreateSchema = z.strictObject({ org_id: z.uuid(), name });
export type BrandCreate = z.infer<typeof brandCreateSchema>;

export const brandSchema = z.strictObject({
  id: z.uuid(),
  org_id: z.uuid(),
  name: z.string(),
  created_at: z.iso.datetime({ offset: true }),
});
export type Brand = z.infer<typeof brandSchema>;

export const productCreateSchema = z.strictObject({
  org_id: z.uuid(),
  brand_id: z.uuid(),
  name,
  category: z.string().trim().min(1).max(80, "At most 80 characters.").nullable(),
});
export type ProductCreate = z.infer<typeof productCreateSchema>;

export const productSchema = z.strictObject({
  id: z.uuid(),
  org_id: z.uuid(),
  brand_id: z.uuid(),
  name: z.string(),
  category: z.string().nullable(),
  created_at: z.iso.datetime({ offset: true }),
});
export type Product = z.infer<typeof productSchema>;

/** Roles that may create brands, products and runs (the RLS insert policies). */
export const canWrite = (roles: readonly Role[]) => roles.includes("owner") || roles.includes("editor");
export const canDelete = (roles: readonly Role[]) => roles.includes("owner");
