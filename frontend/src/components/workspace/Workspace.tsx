"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  type Brand,
  type Organisation,
  brandCreateSchema,
  canDelete,
  canWrite,
  organisationCreateSchema,
  productCreateSchema,
} from "@/contracts/tenancy";
import { api } from "@/lib/api";

import { StartFakeRunForm } from "../StartFakeRunForm";
import { Alert, Button, Field, Panel } from "../ui";

/** The signed-in home: create the first organisation, then brands, products and runs. */
export function Workspace() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);

  if (me.isPending) return <p className="text-bv-faint" role="status">Loading your organisations…</p>;
  if (me.isError) return <Alert>{me.error.message}</Alert>;

  const orgs = me.data.organisations;
  if (orgs.length === 0) return <FirstOrganisation />;
  const org = orgs.find((o) => o.id === selectedOrg) ?? orgs[0]!;

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-bv-faint">Organisation</p>
          <h1 className="font-display text-[clamp(2.25rem,5vw,3.25rem)] leading-tight tracking-[-0.02em]">
            {org.name}
          </h1>
          <p className="text-sm text-bv-muted" data-testid="roles">
            Your role: {org.roles.join(", ")}
          </p>
        </div>
        {orgs.length > 1 && (
          <label className="text-sm text-bv-muted">
            <span className="mb-1.5 block">Switch organisation</span>
            <select
              className="min-h-11 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm text-bv-text"
              value={org.id}
              onChange={(e) => setSelectedOrg(e.target.value)}
            >
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <BrandsAndProducts org={org} />
        <Panel title="Watch the pipeline">
          {canWrite(org.roles) ? (
            <>
              <p className="mb-4 text-sm text-bv-muted">
                A fake run: no model is called and nothing is researched or written. Each step waits,
                then reports, so the live view can be checked before the real pipeline (Phase 6).
              </p>
              <StartFakeRunForm orgId={org.id} />
            </>
          ) : (
            <p className="text-sm text-bv-muted">Starting a run needs the owner or editor role.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}

function FirstOrganisation() {
  const queryClient = useQueryClient();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<z.input<typeof organisationCreateSchema>, unknown, z.output<typeof organisationCreateSchema>>({
    resolver: zodResolver(organisationCreateSchema),
  });
  const create = useMutation({
    mutationFn: api.createOrganisation,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
  });
  return (
    <div className="mx-auto max-w-xl">
      <h1 className="font-display text-[clamp(2.25rem,5vw,3.25rem)] leading-tight tracking-[-0.02em]">
        Create your organisation.
      </h1>
      <p className="mt-3 text-bv-muted">
        Brands, products and runs belong to an organisation. You will be its owner; you can add others later.
      </p>
      <form onSubmit={handleSubmit((v) => create.mutate(v))} noValidate className="glass mt-6 grid gap-4 p-5 sm:p-6">
        <Field label="Organisation name" {...register("name")} error={errors.name?.message} />
        {create.error && <Alert>{create.error.message}</Alert>}
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create organisation"}
        </Button>
      </form>
    </div>
  );
}

const brandForm = brandCreateSchema.omit({ org_id: true });
const productForm = productCreateSchema.omit({ org_id: true, brand_id: true }).extend({
  // An empty text box means "no category".
  category: z.preprocess((v) => (typeof v === "string" && v.trim() === "" ? null : v), productCreateSchema.shape.category),
});

function BrandsAndProducts({ org }: { org: Organisation }) {
  const queryClient = useQueryClient();
  const brands = useQuery({ queryKey: ["brands", org.id], queryFn: () => api.brands(org.id) });
  const [selectedBrand, setSelectedBrand] = useState<string | null>(null);
  const brandFormState = useForm<z.input<typeof brandForm>, unknown, z.output<typeof brandForm>>({
    resolver: zodResolver(brandForm),
  });
  const createBrand = useMutation({
    mutationFn: (v: z.output<typeof brandForm>) => api.createBrand({ org_id: org.id, ...v }),
    onSuccess: (brand) => {
      brandFormState.reset();
      setSelectedBrand(brand.id);
      return queryClient.invalidateQueries({ queryKey: ["brands", org.id] });
    },
  });
  const deleteBrand = useMutation({
    mutationFn: (brandId: string) => api.deleteBrand(org.id, brandId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["brands", org.id] }),
  });

  const list = brands.data ?? [];
  const brand = list.find((b) => b.id === selectedBrand) ?? list[0];
  const writable = canWrite(org.roles);

  return (
    <Panel title="Brands and products">
      {brands.isError && <Alert>{brands.error.message}</Alert>}
      {list.length === 0 && !brands.isPending && <p className="mb-4 text-sm text-bv-muted">No brands yet.</p>}
      {list.length > 0 && (
        <ul className="mb-4 grid gap-2" aria-label="Brands">
          {list.map((b) => (
            <li key={b.id} className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setSelectedBrand(b.id)}
                aria-pressed={b.id === brand?.id}
                className="min-h-11 flex-1 rounded-bv-sm border border-bv-border px-3 text-left text-sm aria-[pressed=true]:border-bv-text/60 aria-[pressed=true]:bg-bv-glass"
              >
                {b.name}
              </button>
              {canDelete(org.roles) && (
                <Button variant="danger" aria-label={`Delete brand ${b.name}`} onClick={() => deleteBrand.mutate(b.id)}>
                  Delete
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {deleteBrand.error && <Alert>{deleteBrand.error.message}</Alert>}
      {writable && (
        <form
          onSubmit={brandFormState.handleSubmit((v) => createBrand.mutate(v))}
          noValidate
          className="mb-6 flex flex-wrap items-start gap-2"
        >
          <Field
            label="New brand"
            className="min-w-0 flex-1"
            {...brandFormState.register("name")}
            id="brand-name"
            error={brandFormState.formState.errors.name?.message}
          />
          <Button type="submit" className="mt-7" disabled={createBrand.isPending}>
            Add brand
          </Button>
          {createBrand.error && <Alert>{createBrand.error.message}</Alert>}
        </form>
      )}
      {brand && <Products org={org} brand={brand} />}
    </Panel>
  );
}

function Products({ org, brand }: { org: Organisation; brand: Brand }) {
  const queryClient = useQueryClient();
  const key = ["products", org.id, brand.id];
  const products = useQuery({ queryKey: key, queryFn: () => api.products(org.id, brand.id) });
  const form = useForm<z.input<typeof productForm>, unknown, z.output<typeof productForm>>({
    resolver: zodResolver(productForm),
  });
  const create = useMutation({
    mutationFn: (v: z.output<typeof productForm>) =>
      api.createProduct({ org_id: org.id, brand_id: brand.id, ...v }),
    onSuccess: () => {
      form.reset();
      return queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const remove = useMutation({
    mutationFn: (productId: string) => api.deleteProduct(org.id, productId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const list = products.data ?? [];

  return (
    <div className="border-t border-bv-border pt-4">
      <h3 className="mb-3 text-sm font-medium text-bv-muted">Products of {brand.name}</h3>
      {products.isError && <Alert>{products.error.message}</Alert>}
      {list.length === 0 && !products.isPending && <p className="mb-3 text-sm text-bv-faint">No products yet.</p>}
      <ul className="mb-4 grid gap-2" aria-label={`Products of ${brand.name}`}>
        {list.map((p) => (
          <li key={p.id} className="flex items-center justify-between gap-2 rounded-bv-sm border border-bv-border px-3 py-2 text-sm">
            <span>
              {p.name}
              {p.category && <span className="ml-2 font-mono text-[0.8125rem] text-bv-faint">{p.category}</span>}
            </span>
            {canDelete(org.roles) && (
              <Button variant="danger" aria-label={`Delete product ${p.name}`} onClick={() => remove.mutate(p.id)}>
                Delete
              </Button>
            )}
          </li>
        ))}
      </ul>
      {remove.error && <Alert>{remove.error.message}</Alert>}
      {canWrite(org.roles) && (
        <form onSubmit={form.handleSubmit((v) => create.mutate(v))} noValidate className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-start">
          <Field label="New product" {...form.register("name")} id="product-name" error={form.formState.errors.name?.message} />
          <Field
            label="Category (optional)"
            {...form.register("category")}
            id="product-category"
            error={form.formState.errors.category?.message}
          />
          <Button type="submit" className="sm:mt-7" disabled={create.isPending}>
            Add product
          </Button>
          {create.error && <Alert>{create.error.message}</Alert>}
        </form>
      )}
    </div>
  );
}
