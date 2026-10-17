/**
 * The API client. Every call carries the signed-in user's access token, and every response is
 * validated against the shared contract, not trusted.
 */
import type { z } from "zod";

import { FakeRunRequest, RunAccepted, runAcceptedSchema } from "@/contracts/runs";
import {
  type Brand,
  type BrandCreate,
  type Me,
  type Organisation,
  type OrganisationCreate,
  type Product,
  type ProductCreate,
  brandSchema,
  meSchema,
  organisationSchema,
  productSchema,
} from "@/contracts/tenancy";

import { API_URL } from "./config";
import { accessToken } from "./supabase";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
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
  if (!response.ok) throw new ApiError(response.status, await detail(response));
  return response;
}

async function detail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // fall through
  }
  return `Request failed (HTTP ${response.status}).`;
}

async function json<T>(schema: z.ZodType<T>, method: string, path: string, body?: unknown): Promise<T> {
  return schema.parse(await (await request(method, path, body)).json());
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();

export const api = {
  me: (): Promise<Me> => json(meSchema, "GET", "/api/v1/me"),
  createOrganisation: (body: OrganisationCreate): Promise<Organisation> =>
    json(organisationSchema, "POST", "/api/v1/organisations", body),
  brands: (orgId: string): Promise<Brand[]> =>
    json(brandSchema.array(), "GET", `/api/v1/brands?${q({ org_id: orgId })}`),
  createBrand: (body: BrandCreate): Promise<Brand> => json(brandSchema, "POST", "/api/v1/brands", body),
  deleteBrand: async (orgId: string, brandId: string): Promise<void> => {
    await request("DELETE", `/api/v1/brands/${brandId}?${q({ org_id: orgId })}`);
  },
  products: (orgId: string, brandId: string): Promise<Product[]> =>
    json(productSchema.array(), "GET", `/api/v1/products?${q({ org_id: orgId, brand_id: brandId })}`),
  createProduct: (body: ProductCreate): Promise<Product> =>
    json(productSchema, "POST", "/api/v1/products", body),
  deleteProduct: async (orgId: string, productId: string): Promise<void> => {
    await request("DELETE", `/api/v1/products/${productId}?${q({ org_id: orgId })}`);
  },
  startFakeRun: (body: FakeRunRequest): Promise<RunAccepted> =>
    json(runAcceptedSchema, "POST", "/api/v1/runs/fake", body),
};
