"use client";

import Link from "next/link";

import { TOTAL_STEPS } from "@/contracts/runs";
import { RunStreamError, type ConnectionState } from "@/lib/run-stream";
import { formatDuration, isFinished, type RunView as RunViewModel } from "@/lib/run-view";
import { useNow, useRunStream } from "@/lib/use-run-stream";

import { PipelineStepper } from "./PipelineStepper";

export function RunView({ runId }: { runId: string }) {
  const { view, error, connection, restart } = useRunStream(runId);
  const finished = isFinished(view);
  const now = useNow(100, !finished && error === null);

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
      <section aria-labelledby="run-heading" className="min-w-0">
        <header className="mb-6">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {view.mode === "fake" && (
              <span className="rounded-full border border-bv-glass-edge px-2.5 py-0.5 text-xs font-medium tracking-wide text-bv-muted">
                Fake run · no model calls
              </span>
            )}
            <ConnectionBadge connection={connection} finished={finished} hasError={error !== null} />
          </div>
          <h1
            id="run-heading"
            className="font-display text-[clamp(2.25rem,5vw,3.5rem)] leading-tight tracking-[-0.02em]"
          >
            <RunTitle view={view} />
          </h1>
          <p className="mt-1 font-mono text-[0.8125rem] text-bv-faint">run {runId}</p>
        </header>

        <Progress view={view} />

        {error !== null && <StreamProblem error={error} onRetry={restart} />}
        {view.phase === "failed" && <FailureOutcome view={view} />}
        {view.phase === "completed" && <CompletedOutcome view={view} />}

        <PipelineStepper steps={view.steps} now={now} />
      </section>

      <aside className="glass p-5 lg:sticky lg:top-6" aria-label="Run summary">
        <Summary view={view} now={now} />
      </aside>

      {/* Design.md §10: progress announced politely to screen readers. */}
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {view.announcement}
      </p>
    </div>
  );
}

function RunTitle({ view }: { view: RunViewModel }) {
  switch (view.phase) {
    case "waiting":
      return <>Connecting to the run…</>;
    case "queued":
      return <>Queued — waiting for the worker</>;
    case "running":
      return <>Running the pipeline</>;
    case "completed":
      return <>Pipeline complete</>;
    case "failed":
      return <>Run failed</>;
  }
}

function Progress({ view }: { view: RunViewModel }) {
  const fraction = view.doneCount / TOTAL_STEPS;
  return (
    <div className="mb-6">
      <div className="mb-2 flex items-baseline justify-between text-sm">
        <span className="font-medium text-bv-muted">
          <span className="tabular text-bv-text">{view.doneCount}</span> of {TOTAL_STEPS} steps
        </span>
        <span className="tabular font-mono text-[0.8125rem] text-bv-faint">
          {Math.round(fraction * 100)}%
        </span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-bv-border"
        role="progressbar"
        aria-label="Pipeline progress"
        aria-valuemin={0}
        aria-valuemax={TOTAL_STEPS}
        aria-valuenow={view.doneCount}
      >
        {/* transform, not width (Design.md §6 rule 3). */}
        <div
          className={`h-full origin-left rounded-full transition-transform duration-[650ms] ease-[cubic-bezier(.16,1,.3,1)] ${
            view.phase === "failed" ? "bg-bv-danger/70" : "bg-bv-text/80"
          }`}
          style={{ transform: `scaleX(${fraction})` }}
        />
      </div>
    </div>
  );
}

function ConnectionBadge({
  connection,
  finished,
  hasError,
}: {
  connection: ConnectionState;
  finished: boolean;
  hasError: boolean;
}) {
  if (hasError) return null;
  let text: string;
  let live = false;
  switch (connection.kind) {
    case "connecting":
      text = "Connecting";
      break;
    case "live":
      text = finished ? "Stream closed" : "Live";
      live = !finished;
      break;
    case "reconnecting":
      text = `Connection lost — retrying in ${(connection.delayMs / 1000).toFixed(1)} s (attempt ${connection.attempt})`;
      break;
    case "ended":
      text = "Stream closed";
      break;
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium tracking-wide text-bv-muted" data-connection={connection.kind}>
      <span
        aria-hidden="true"
        className={`size-1.5 rounded-full ${live ? "bv-breathe bg-bv-text" : "bg-bv-faint"}`}
      />
      {text}
    </span>
  );
}

function FailureOutcome({ view }: { view: RunViewModel }) {
  const step = view.failedStep === null ? undefined : view.steps[view.failedStep - 1];
  return (
    <div role="alert" className="bv-enter mb-6 rounded-bv-md border border-bv-danger/50 bg-bv-danger/5 p-4">
      <p className="font-medium text-bv-danger">
        {step === undefined ? "The run stopped." : `Stopped at step ${step.number}, ${step.title}.`}
      </p>
      <p className="mt-1 text-sm text-bv-muted">{view.error}</p>
      <p className="mt-2 text-sm text-bv-faint">
        Later steps did not run. Nothing after the failure was attempted.
      </p>
    </div>
  );
}

function CompletedOutcome({ view }: { view: RunViewModel }) {
  const total =
    view.startedAt !== null && view.endedAt !== null ? formatDuration(view.endedAt - view.startedAt) : null;
  const skipped = view.steps.filter((s) => s.phase === "skipped").length;
  return (
    <div className="bv-enter mb-6 rounded-bv-md border border-bv-glass-edge bg-bv-glass p-4" role="status">
      <p className="font-medium">
        All {TOTAL_STEPS} steps finished{total === null ? "" : ` in ${total}`}.
      </p>
      {skipped > 0 && (
        <p className="mt-1 text-sm text-bv-muted">
          {skipped} skipped by design — shown greyed, not hidden.
        </p>
      )}
    </div>
  );
}

function StreamProblem({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const canRetry = !(error instanceof RunStreamError) || error.code === "unreachable";
  return (
    <div role="alert" className="mb-6 rounded-bv-md border border-bv-danger/50 bg-bv-danger/5 p-4">
      <p className="font-medium text-bv-danger">
        {error instanceof RunStreamError && error.code === "not_found"
          ? "This run does not exist, or its events have expired."
          : error instanceof RunStreamError && error.code === "forbidden"
            ? "You are not allowed to watch this run."
            : "Lost the connection to the run."}
      </p>
      <p className="mt-1 text-sm text-bv-muted">{error.message}</p>
      <div className="mt-3 flex gap-3">
        {canRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="min-h-11 rounded-bv-sm border border-bv-glass-edge px-4 text-sm font-medium hover:bg-bv-glass"
          >
            Reconnect
          </button>
        )}
        <Link href="/" className="inline-flex min-h-11 items-center rounded-bv-sm px-4 text-sm font-medium text-bv-muted hover:text-bv-text">
          Start another run
        </Link>
      </div>
    </div>
  );
}

function Summary({ view, now }: { view: RunViewModel; now: number }) {
  const active = view.steps.find((s) => s.phase === "active");
  const elapsed =
    view.startedAt === null ? null : (view.endedAt ?? Math.max(now, view.startedAt)) - view.startedAt;
  const rows: [string, string][] = [
    ["Status", STATUS_WORD[view.phase]],
    ["Current step", active === undefined ? "—" : `${active.number} · ${active.title}`],
    ["Elapsed", elapsed === null ? "—" : formatDuration(elapsed)],
    ["Done", `${view.doneCount} / ${TOTAL_STEPS}`],
    ["Skipped", String(view.steps.filter((s) => s.phase === "skipped").length)],
    ["Events received", String(view.eventCount)],
  ];
  return (
    <>
      <h2 className="mb-3 text-sm font-medium uppercase tracking-[0.08em] text-bv-faint">Summary</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-bv-faint">{label}</dt>
            <dd className={`tabular text-right ${label === "Status" && view.phase === "failed" ? "text-bv-danger" : "text-bv-text"}`}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {view.phase !== "waiting" && (
        <Link
          href="/"
          className="mt-5 inline-flex min-h-11 w-full items-center justify-center rounded-bv-sm border border-bv-glass-edge text-sm font-medium hover:bg-bv-glass"
        >
          Start another run
        </Link>
      )}
    </>
  );
}

const STATUS_WORD: Record<RunViewModel["phase"], string> = {
  waiting: "Connecting",
  queued: "Queued",
  running: "Running",
  completed: "Completed",
  failed: "Failed",
};
