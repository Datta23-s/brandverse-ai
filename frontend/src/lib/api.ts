/**
 * The API client. Every call carries the signed-in user's access token, and every response is
 * validated against the shared contract, not trusted.
 */
import type { z } from "zod";

import {
  type ClaimRetire,
  type ProductClaim,
  type ProductClaimCreate,
  productClaimSchema,
} from "@/contracts/claims";
import {
  type FakeRunRequest,
  type RunAccepted,
  type RunBlocked,
  runAcceptedSchema,
  runBlockedSchema,
} from "@/contracts/runs";
import {
  type Brand,
  type BrandCreate,
  type BrandSectorUpdate,
  type Me,
  type Organisation,
  type OrganisationCreate,
  type Product,
  type ProductCreate,
  type Sector,
  brandSchema,
  meSchema,
  organisationSchema,
  productSchema,
  sectorSchema,
} from "@/contracts/tenancy";

import { API_URL } from "./config";
import { accessToken } from "./supabase";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Set when admission refused a run (409): every reason, as the API gave them. */
    readonly blocked: RunBlocked | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request(method: string, path: string, body?: unknown): Promise<Response> {
  const headers: Record<string, string> = { Authorization: `Bearer ${await accessToken()}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, `The API at ${API_URL} is not reachable. Is it running on the laptop?`);
  }
  if (!response.ok) throw await failure(response);
  return response;
}

async function failure(response: Response): Promise<ApiError> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      if (typeof body.detail === "string") return new ApiError(response.status, body.detail);
      const blocked = runBlockedSchema.safeParse(body.detail);
      if (blocked.success) return new ApiError(response.status, blocked.data.message, blocked.data);
    }
  } catch {
    // fall through
  }
  return new ApiError(response.status, `Request failed (HTTP ${response.status}).`);
}

async function json<T>(schema: z.ZodType<T>, method: string, path: string, body?: unknown): Promise<T> {
  return schema.parse(await (await request(method, path, body)).json());
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();

export const api = {
  me: (): Promise<Me> => json(meSchema, "GET", "/api/v1/me"),
  createOrganisation: (body: OrganisationCreate): Promise<Organisation> =>
    json(organisationSchema, "POST", "/api/v1/organisations", body),
  sectors: (): Promise<Sector[]> => json(sectorSchema.array(), "GET", "/api/v1/sectors"),
  brands: (orgId: string): Promise<Brand[]> =>
    json(brandSchema.array(), "GET", `/api/v1/brands?${q({ org_id: orgId })}`),
  createBrand: (body: BrandCreate): Promise<Brand> => json(brandSchema, "POST", "/api/v1/brands", body),
  setBrandSector: (brandId: string, body: BrandSectorUpdate): Promise<Brand> =>
    json(brandSchema, "PATCH", `/api/v1/brands/${brandId}/sector`, body),
  deleteBrand: async (orgId: string, brandId: string): Promise<void> => {
    await request("DELETE", `/api/v1/brands/${brandId}?${q({ org_id: orgId })}`);
  },
  /** The organisation's products; one brand's only when `brandId` is given. */
  products: (orgId: string, brandId?: string): Promise<Product[]> =>
    json(
      productSchema.array(),
      "GET",
      `/api/v1/products?${q(brandId === undefined ? { org_id: orgId } : { org_id: orgId, brand_id: brandId })}`,
    ),
  createProduct: (body: ProductCreate): Promise<Product> =>
    json(productSchema, "POST", "/api/v1/products", body),
  deleteProduct: async (orgId: string, productId: string): Promise<void> => {
    await request("DELETE", `/api/v1/products/${productId}?${q({ org_id: orgId })}`);
  },
  claims: (orgId: string, productId: string): Promise<ProductClaim[]> =>
    json(productClaimSchema.array(), "GET", `/api/v1/products/${productId}/claims?${q({ org_id: orgId })}`),
  createClaim: (productId: string, body: ProductClaimCreate): Promise<ProductClaim> =>
    json(productClaimSchema, "POST", `/api/v1/products/${productId}/claims`, body),
  retireClaim: (productId: string, claimId: string, body: ClaimRetire): Promise<ProductClaim> =>
    json(productClaimSchema, "POST", `/api/v1/products/${productId}/claims/${claimId}/retire`, body),
  startFakeRun: (body: FakeRunRequest): Promise<RunAccepted> =>
    json(runAcceptedSchema, "POST", "/api/v1/runs/fake", body),
};
