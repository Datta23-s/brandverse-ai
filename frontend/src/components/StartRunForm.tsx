"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { runRequestSchema, type ResearchDepth } from "@/contracts/runs";
import { ApiError, api } from "@/lib/api";

import { Alert, Button } from "./ui";

/**
 * Starts a REAL run (`POST /api/v1/runs`) in `orgId`. Unlike the fake run it carries the audience,
 * which research is planned from. The run parks at the approval step; an approver decides there.
 * Admission refuses a run with every reason, as for a fake run.
 */
export function StartRunForm({ orgId }: { orgId: string }) {
  const router = useRouter();
  const products = useQuery({ queryKey: ["products", orgId, "all"], queryFn: () => api.products(orgId) });
  const [productId, setProductId] = useState("");
  const [brief, setBrief] = useState("");
  const [audience, setAudience] = useState("");
  const [depth, setDepth] = useState<ResearchDepth>("D0");
  const [invalid, setInvalid] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: () => {
      const parsed = runRequestSchema.safeParse({
        org_id: orgId,
        product_id: productId,
        brief: brief.trim() === "" ? null : brief,
        audience: audience.trim() === "" ? null : audience,
        depth,
      });
      if (!parsed.success) {
        const message = parsed.error.issues[0]?.message ?? "Check the form.";
        setInvalid(message);
        return Promise.reject(new Error(message));
      }
      setInvalid(null);
      return api.startRun(parsed.data);
    },
    onSuccess: (accepted) => router.push(`/runs/${accepted.run_id}`),
  });
  const blocked = start.error instanceof ApiError ? start.error.blocked : null;

  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        start.mutate();
      }}
      noValidate
    >
      <label className="grid gap-1.5 text-sm font-medium text-bv-muted">
        Product
        <select
          aria-label="Real run product"
          value={productId}
          onChange={(e) => setProductId(e.target.value)}
          className="min-h-11 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm font-normal"
        >
          <option value="">Choose a product</option>
          {(products.data ?? []).map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1.5 text-sm font-medium text-bv-muted">
        Audience (optional)
        <input
          aria-label="Audience"
          value={audience}
          onChange={(e) => setAudience(e.target.value)}
          className="min-h-11 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm font-normal"
        />
      </label>
      <label className="grid gap-1.5 text-sm font-medium text-bv-muted">
        Brief (optional)
        <textarea
          aria-label="Real run brief"
          rows={2}
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          className="rounded-bv-sm border border-bv-border bg-bv-deep px-3 py-2 text-sm font-normal"
        />
      </label>
      <label className="grid gap-1.5 text-sm font-medium text-bv-muted">
        Research depth
        <select
          aria-label="Real run depth"
          value={depth}
          onChange={(e) => setDepth(e.target.value as ResearchDepth)}
          className="min-h-11 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm font-normal"
        >
          <option value="D0">D0 · memory only (no search)</option>
          <option value="D1">D1 · focused research</option>
          <option value="D2">D2 · deep research</option>
        </select>
      </label>
      {invalid !== null && <Alert>{invalid}</Alert>}
      {start.error !== null && invalid === null && (
        <Alert>
          {blocked === null
            ? start.error.message
            : `Run blocked. Nothing was started: ${blocked.reasons.map((r) => r.message).join(" ")}`}
        </Alert>
      )}
      <div>
        <Button type="submit" disabled={start.isPending}>
          {start.isPending ? "Starting…" : "Start run"}
        </Button>
      </div>
    </form>
  );
}
