"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  PIPELINE_STEPS,
  fakeRunRequestSchema,
  type FakeRunRequest,
  type ResearchDepth,
} from "@/contracts/runs";
import { startFakeRun } from "@/lib/api";

// DepthBadge colours carry their Design.md §8 meaning: D0 aqua (memory), D1 violet, D2 amber.
const DEPTHS: { value: ResearchDepth; label: string; explain: string; dot: string }[] = [
  { value: "D0", label: "D0 · memory only", explain: "Step 6 is skipped: memory was enough.", dot: "bg-bv-primary" },
  { value: "D1", label: "D1 · focused research", explain: "Step 6 runs focused research.", dot: "bg-bv-secondary" },
  { value: "D2", label: "D2 · deep research", explain: "Step 6 runs deep research.", dot: "bg-bv-accent" },
];

/**
 * The shared contract schema, with one form-only conversion: a <select> holds strings, and
 * "No failure" is "". The bounds still come from the contract (fakeRunRequestSchema).
 */
const formSchema = fakeRunRequestSchema.extend({
  fail_at_step: z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? null : Number(v)),
    fakeRunRequestSchema.shape.fail_at_step,
  ),
});
type FormInput = z.input<typeof formSchema>;

const DEFAULTS: FormInput = { depth: "D1", step_delay_ms: 400, fail_at_step: "" };

export function StartFakeRunForm() {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormInput, unknown, FakeRunRequest>({
    resolver: zodResolver(formSchema),
    defaultValues: DEFAULTS,
  });
  const start = useMutation({
    mutationFn: startFakeRun,
    onSuccess: (accepted) => router.push(`/runs/${accepted.run_id}`),
  });

  return (
    <form onSubmit={handleSubmit((values) => start.mutate(values))} className="glass grid gap-6 p-5 sm:p-6" noValidate>
      <fieldset>
        <legend className="mb-3 text-sm font-medium tracking-wide text-bv-muted">Research depth to simulate</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {DEPTHS.map((d) => (
            <label
              key={d.value}
              className="flex min-h-11 cursor-pointer gap-3 rounded-bv-md border border-bv-border p-3 transition-colors duration-150 hover:border-bv-glass-edge has-[:checked]:border-bv-text/60 has-[:checked]:bg-bv-glass"
            >
              <input type="radio" value={d.value} {...register("depth")} className="mt-1.5 accent-bv-text" />
              <span>
                <span className="flex items-center gap-2 text-sm font-medium">
                  <span aria-hidden="true" className={`size-2 rounded-full ${d.dot}`} />
                  {d.label}
                </span>
                <span className="mt-0.5 block text-sm text-bv-faint">{d.explain}</span>
              </span>
            </label>
          ))}
        </div>
        {errors.depth && <p className="mt-2 text-sm text-bv-danger">Choose D0, D1 or D2.</p>}
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="step_delay_ms" className="mb-1.5 block text-sm font-medium text-bv-muted">
            Time per step (ms)
          </label>
          <input
            id="step_delay_ms"
            type="number"
            inputMode="numeric"
            min={0}
            max={5000}
            step={50}
            {...register("step_delay_ms", { valueAsNumber: true })}
            aria-invalid={errors.step_delay_ms !== undefined}
            aria-describedby="step_delay_ms-help"
            className="tabular min-h-11 w-full rounded-bv-sm border border-bv-border bg-bv-deep px-3 font-mono text-sm"
          />
          <p id="step_delay_ms-help" className="mt-1 text-sm text-bv-faint">
            {errors.step_delay_ms ? <span className="text-bv-danger">Between 0 and 5000.</span> : "0 to 5000."}
          </p>
        </div>
        <div>
          <label htmlFor="fail_at_step" className="mb-1.5 block text-sm font-medium text-bv-muted">
            Simulate a failure
          </label>
          <select
            id="fail_at_step"
            {...register("fail_at_step")}
            aria-invalid={errors.fail_at_step !== undefined}
            className="min-h-11 w-full rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm"
          >
            <option value="">No failure</option>
            {PIPELINE_STEPS.map((s) => (
              <option key={s.number} value={s.number}>
                At step {s.number} · {s.title}
              </option>
            ))}
          </select>
          {errors.fail_at_step && (
            <p className="mt-1 text-sm text-bv-danger">Choose a step from 1 to 15, or no failure.</p>
          )}
        </div>
      </div>

      {start.error !== null && (
        <p role="alert" className="rounded-bv-sm border border-bv-danger/50 bg-bv-danger/5 p-3 text-sm text-bv-danger">
          {start.error.message}
        </p>
      )}

      <div>
        <button
          type="submit"
          disabled={start.isPending}
          className="min-h-11 rounded-bv-sm bg-bv-text px-5 text-sm font-medium text-bv-void transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
        >
          {start.isPending ? "Starting…" : "Start fake run"}
        </button>
      </div>
    </form>
  );
}
