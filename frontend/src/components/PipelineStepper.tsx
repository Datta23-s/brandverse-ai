import type { StepPhase, StepView } from "@/lib/run-view";
import { formatDuration } from "@/lib/run-view";

/**
 * The 15 steps (Design.md §8 `PipelineStepper`). Every phase is distinguishable without colour:
 * the marker's shape, the label and the timing all change. Colour keeps its meaning (Design.md
 * §3): rose only for failure; nothing else is coloured decoratively. Skipped steps stay visible —
 * showing what was not done is part of the argument.
 */
export function PipelineStepper({ steps, now }: { steps: readonly StepView[]; now: number }) {
  return (
    <ol className="relative" aria-label="Pipeline steps">
      {steps.map((step, i) => (
        <StepRow key={step.number} step={step} now={now} isLast={i === steps.length - 1} />
      ))}
    </ol>
  );
}

const LABEL: Record<StepPhase, string> = {
  pending: "Waiting",
  active: "Running",
  completed: "Done",
  skipped: "Skipped",
  failed: "Failed",
  not_run: "Not run",
};

function StepRow({ step, now, isLast }: { step: StepView; now: number; isLast: boolean }) {
  const { phase } = step;
  const dim = phase === "pending" || phase === "not_run" || phase === "skipped";
  const timing =
    step.startedAt === null
      ? null
      : phase === "active"
        ? formatDuration(now - step.startedAt)
        : step.endedAt !== null && phase !== "skipped"
          ? formatDuration(step.endedAt - step.startedAt)
          : null;

  return (
    <li
      className="relative grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 sm:gap-x-4"
      data-step={step.number}
      data-phase={phase}
      aria-current={phase === "active" ? "step" : undefined}
    >
      {/* The rail between markers: solid once this step is finished. */}
      {!isLast && (
        <span
          aria-hidden="true"
          className={`absolute left-[1.25rem] top-10 bottom-0 w-px -translate-x-1/2 ${
            phase === "completed" || phase === "skipped" ? "bg-bv-muted/50" : "bg-bv-border"
          }`}
        />
      )}
      <div className="relative z-10 flex h-14 items-start pt-2">
        <Marker step={step} />
      </div>
      <div
        className={`mb-2 flex min-h-12 min-w-0 flex-col gap-1 rounded-bv-md px-3 py-2.5 transition-[background-color,border-color] duration-300 sm:flex-row sm:items-start sm:justify-between sm:gap-4 ${
          phase === "active"
            ? "border border-bv-glass-edge bg-bv-glass"
            : phase === "failed"
              ? "border border-bv-danger/40 bg-bv-danger/5"
              : "border border-transparent"
        }`}
      >
        <div className="min-w-0">
          <p
            className={`font-medium leading-snug ${
              phase === "failed" ? "text-bv-danger" : dim ? "text-bv-faint" : "text-bv-text"
            } ${phase === "skipped" ? "line-through decoration-bv-faint/60" : ""}`}
          >
            {step.title}
          </p>
          <p className="font-mono text-[0.8125rem] text-bv-faint [overflow-wrap:anywhere]">
            {step.nodes.join(" · ")}
            {step.detail !== null && (
              <span className={phase === "failed" ? "text-bv-danger/80" : "text-bv-muted"}>
                {"  —  "}
                {step.detail}
              </span>
            )}
          </p>
        </div>
        <div className="flex shrink-0 items-baseline gap-3 text-sm sm:flex-col sm:items-end sm:gap-0">
          <span
            className={`font-medium tracking-wide ${
              phase === "failed"
                ? "text-bv-danger"
                : phase === "active"
                  ? "text-bv-text"
                  : phase === "completed"
                    ? "text-bv-muted"
                    : "text-bv-faint"
            }`}
          >
            {LABEL[phase]}
          </span>
          <span className="tabular font-mono text-[0.8125rem] text-bv-faint">{timing ?? " "}</span>
        </div>
      </div>
    </li>
  );
}

function Marker({ step }: { step: StepView }) {
  const n = String(step.number).padStart(2, "0");
  const base =
    "flex size-10 items-center justify-center rounded-full font-mono text-[0.8125rem] transition-[opacity,transform] duration-500";
  switch (step.phase) {
    case "completed":
      return (
        <span className={`${base} bg-bv-muted/90 text-bv-void`} aria-hidden="true">
          <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.25">
            <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      );
    case "active":
      return (
        <span className={`${base} relative border-2 border-bv-text bg-bv-void text-bv-text`} aria-hidden="true">
          <span className="bv-breathe absolute inset-0 rounded-full border-2 border-bv-text/60" />
          {n}
        </span>
      );
    case "failed":
      return (
        <span className={`${base} bg-bv-danger text-bv-void`} aria-hidden="true">
          <svg viewBox="0 0 16 16" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.25">
            <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" strokeLinecap="round" />
          </svg>
        </span>
      );
    case "skipped":
      return (
        <span className={`${base} border border-dashed border-bv-faint bg-bv-void text-bv-faint`} aria-hidden="true">
          –
        </span>
      );
    default:
      return (
        <span
          className={`${base} border border-bv-border bg-bv-void text-bv-faint ${
            step.phase === "not_run" ? "opacity-50" : ""
          }`}
          aria-hidden="true"
        >
          {n}
        </span>
      );
  }
}
