"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  type ClaimType,
  type ProductClaim,
  claimRetireSchema,
  claimTypeRules,
  productClaimFields,
} from "@/contracts/claims";
import { type Organisation, type Product, canWrite } from "@/contracts/tenancy";
import { api } from "@/lib/api";

import { Alert, Button, Field } from "../ui";

// Colour carries meaning (Design.md §3): amber verified by evidence, violet generated or
// inferred, rose restricted. The word is always shown too; colour is never the only signal.
const TYPES: Record<ClaimType, { label: string; badge: string; explain: string }> = {
  verified: {
    label: "Verified",
    badge: "border-bv-accent/60 text-bv-accent",
    explain: "A fact with a source a reader can open.",
  },
  deduced: {
    label: "Deduced",
    badge: "border-bv-secondary/60 text-bv-secondary",
    explain: "Inferred, with a confidence and what it rests on. Shown as a deduction, never as fact.",
  },
  restricted: {
    label: "Restricted",
    badge: "border-bv-danger/60 text-bv-danger",
    explain: "Must never appear in generated copy. Say why.",
  },
};

/** An empty box is "not given" (null), as the contract expects. */
const blankToNull = (v: unknown) => (typeof v === "string" && v.trim() === "" ? null : v);

/** The contract schema without org_id, with form-only conversions of empty boxes; same rules. */
const createForm = productClaimFields
  .omit({ org_id: true })
  .extend({
    evidence_url: z.preprocess(blankToNull, productClaimFields.shape.evidence_url),
    evidence_note: z.preprocess(blankToNull, productClaimFields.shape.evidence_note),
    confidence: z.preprocess(
      (v) => (blankToNull(v) === null ? null : Number(v)),
      productClaimFields.shape.confidence,
    ),
  })
  .superRefine(claimTypeRules);
type CreateInput = z.input<typeof createForm>;
type CreateOutput = z.output<typeof createForm>;

const EMPTY = { claim: "", evidence_url: "", evidence_note: "", confidence: "" };

export function ProductClaims({ org, product }: { org: Organisation; product: Product }) {
  const queryClient = useQueryClient();
  const key = ["claims", org.id, product.id];
  const claims = useQuery({ queryKey: key, queryFn: () => api.claims(org.id, product.id) });
  const form = useForm<CreateInput, unknown, CreateOutput>({
    resolver: zodResolver(createForm),
    defaultValues: { ...EMPTY, claim_type: "verified" },
  });
  const create = useMutation({
    mutationFn: (v: CreateOutput) => api.createClaim(product.id, { org_id: org.id, ...v }),
    onSuccess: () => {
      form.reset({ ...EMPTY, claim_type: form.getValues("claim_type") });
      return queryClient.invalidateQueries({ queryKey: key });
    },
  });
  const type = form.watch("claim_type");
  const errors = form.formState.errors;
  const list = claims.data ?? [];
  const writable = canWrite(org.roles);
  const id = (field: string) => `claim-${product.id}-${field}`;

  return (
    <div className="mt-3 grid gap-3 rounded-bv-sm border border-bv-border p-3" aria-label={`Claims of ${product.name}`} role="region">
      {claims.isError && <Alert>{claims.error.message}</Alert>}
      {list.length === 0 && !claims.isPending && (
        <p className="text-sm text-bv-faint">No claims yet. A run needs at least one verified claim.</p>
      )}
      {list.length > 0 && (
        <ul className="grid gap-2" aria-label={`Claim list of ${product.name}`}>
          {list.map((c) => (
            <ClaimRow key={c.id} org={org} product={product} claim={c} writable={writable} />
          ))}
        </ul>
      )}
      {writable && (
        <form onSubmit={form.handleSubmit((v) => create.mutate(v))} noValidate className="grid gap-3 border-t border-bv-border pt-3">
          <Field label="Claim" id={id("text")} {...form.register("claim")} error={errors.claim?.message} />
          <div>
            <label htmlFor={id("type")} className="mb-1.5 block text-sm font-medium text-bv-muted">
              Claim type
            </label>
            <select
              id={id("type")}
              {...form.register("claim_type")}
              aria-describedby={id("type-help")}
              className="min-h-11 w-full rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm"
            >
              {(Object.keys(TYPES) as ClaimType[]).map((t) => (
                <option key={t} value={t}>
                  {TYPES[t].label}
                </option>
              ))}
            </select>
            <p id={id("type-help")} className="mt-1 text-sm text-bv-faint">
              {TYPES[type].explain}
            </p>
          </div>
          <Field
            label={type === "verified" ? "Evidence link" : "Evidence link (optional)"}
            id={id("url")}
            type="url"
            inputMode="url"
            placeholder="https://"
            {...form.register("evidence_url")}
            error={errors.evidence_url?.message}
          />
          <Field
            label={type === "restricted" ? "Why it is restricted" : "Evidence note (optional)"}
            id={id("note")}
            {...form.register("evidence_note")}
            error={errors.evidence_note?.message}
          />
          {type === "deduced" && (
            <Field
              label="Confidence (0 to 1)"
              id={id("confidence")}
              type="number"
              inputMode="decimal"
              min={0}
              max={1}
              step={0.05}
              {...form.register("confidence")}
              error={errors.confidence?.message}
            />
          )}
          {create.error && <Alert>{create.error.message}</Alert>}
          <div>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Adding…" : "Add claim"}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

const retireForm = claimRetireSchema.omit({ org_id: true });

function ClaimRow({
  org,
  product,
  claim,
  writable,
}: {
  org: Organisation;
  product: Product;
  claim: ProductClaim;
  writable: boolean;
}) {
  const queryClient = useQueryClient();
  const [retiring, setRetiring] = useState(false);
  const form = useForm<z.input<typeof retireForm>, unknown, z.output<typeof retireForm>>({
    resolver: zodResolver(retireForm),
  });
  const retire = useMutation({
    mutationFn: (v: z.output<typeof retireForm>) => api.retireClaim(product.id, claim.id, { org_id: org.id, ...v }),
    onSuccess: () => {
      setRetiring(false);
      return queryClient.invalidateQueries({ queryKey: ["claims", org.id, product.id] });
    },
  });
  const t = TYPES[claim.claim_type];
  const retired = claim.retired_at !== null;

  return (
    <li className="grid gap-1 rounded-bv-sm border border-bv-border px-3 py-2 text-sm" data-claim-type={claim.claim_type} data-retired={retired}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${t.badge}`}>{t.label}</span>
        <span className={retired ? "text-bv-faint line-through" : ""}>{claim.claim}</span>
        {claim.confidence !== null && (
          <span className="tabular font-mono text-[0.8125rem] text-bv-faint">confidence {claim.confidence.toFixed(2)}</span>
        )}
      </div>
      {claim.evidence_url && (
        <a href={claim.evidence_url} target="_blank" rel="noopener noreferrer" className="break-all text-bv-muted underline underline-offset-2">
          {claim.evidence_url}
        </a>
      )}
      {claim.evidence_note && <p className="text-bv-faint">{claim.evidence_note}</p>}
      {retired && (
        <p className="text-bv-faint">
          Retired{claim.retired_reason ? `: ${claim.retired_reason}` : ""}
        </p>
      )}
      {writable && !retired && !retiring && (
        <div>
          <Button variant="quiet" onClick={() => setRetiring(true)} aria-label={`Retire claim ${claim.claim}`}>
            Retire
          </Button>
        </div>
      )}
      {retiring && (
        <form onSubmit={form.handleSubmit((v) => retire.mutate(v))} noValidate className="flex flex-wrap items-start gap-2">
          <Field
            label="Why retire it"
            id={`retire-${claim.id}`}
            className="min-w-0 flex-1"
            {...form.register("reason")}
            error={form.formState.errors.reason?.message}
            hint="Claims are never edited or deleted; a retired claim stays on record."
          />
          <Button type="submit" variant="danger" className="mt-7" disabled={retire.isPending}>
            Retire claim
          </Button>
          <Button variant="quiet" className="mt-7" onClick={() => setRetiring(false)}>
            Cancel
          </Button>
          {retire.error && <Alert>{retire.error.message}</Alert>}
        </form>
      )}
    </li>
  );
}
