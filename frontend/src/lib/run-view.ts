/**
 * Folds a run's events into what the run view renders. Pure: the same events always give the
 * same view, so a reconnect that replays from the start rebuilds it exactly.
 */
import { PIPELINE_STEPS, RunEvent, TOTAL_STEPS } from "@/contracts/runs";

/** `not_run`: the run ended (failed) before reaching this step. */
export type StepPhase = "pending" | "active" | "completed" | "skipped" | "failed" | "not_run";
export type RunPhase = "waiting" | "queued" | "running" | "completed" | "failed";

export interface StepView {
  number: number;
  name: string;
  title: string;
  nodes: readonly string[];
  phase: StepPhase;
  /** Epoch milliseconds, from the server's event timestamps. */
  startedAt: number | null;
  endedAt: number | null;
  detail: string | null;
}

export interface RunView {
  runId: string | null;
  mode: "fake" | "pipeline" | null;
  phase: RunPhase;
  steps: readonly StepView[];
  queuedAt: number | null;
  startedAt: number | null;
  endedAt: number | null;
  error: string | null;
  failedStep: number | null;
  /** Steps finished, whether completed or skipped. */
  doneCount: number;
  /** One sentence for screen readers about the latest change (Design.md §10). */
  announcement: string;
  eventCount: number;
}

export const initialRunView: RunView = {
  runId: null,
  mode: null,
  phase: "waiting",
  steps: PIPELINE_STEPS.map((s) => ({
    number: s.number,
    name: s.name,
    title: s.title,
    nodes: s.nodes,
    phase: "pending",
    startedAt: null,
    endedAt: null,
    detail: null,
  })),
  queuedAt: null,
  startedAt: null,
  endedAt: null,
  error: null,
  failedStep: null,
  doneCount: 0,
  announcement: "",
  eventCount: 0,
};

const PHASE_WORDS: Record<StepPhase, string> = {
  pending: "pending",
  active: "running",
  completed: "completed",
  skipped: "skipped",
  failed: "failed",
  not_run: "not run",
};

export function reduceRunView(view: RunView, event: RunEvent): RunView {
  const at = Date.parse(event.ts);
  const next: RunView = { ...view, runId: event.run_id, eventCount: view.eventCount + 1 };

  switch (event.type) {
    case "run.queued":
      return { ...next, mode: event.mode, phase: "queued", queuedAt: at, announcement: "Run queued." };
    case "run.started":
      return { ...next, phase: "running", startedAt: at, announcement: "Run started." };
    case "run.completed":
      return {
        ...next,
        phase: "completed",
        endedAt: at,
        announcement: `Run completed: ${TOTAL_STEPS} of ${TOTAL_STEPS} steps.`,
      };
    case "run.failed": {
      const steps = view.steps.map((s): StepView => {
        if (s.phase === "active") return { ...s, phase: "failed", endedAt: at };
        if (s.phase === "pending") return { ...s, phase: "not_run" };
        return s;
      });
      return {
        ...next,
        phase: "failed",
        endedAt: at,
        steps,
        error: event.error,
        failedStep: event.step,
        doneCount: countDone(steps),
        announcement: `Run failed${event.step === null ? "" : ` at step ${event.step}`}: ${event.error}`,
      };
    }
    case "step": {
      const index = event.step - 1;
      const current = view.steps[index];
      if (current === undefined) return next;
      const updated: StepView = { ...current };
      switch (event.status) {
        case "started":
          updated.phase = "active";
          updated.startedAt = at;
          updated.detail = event.detail;
          break;
        case "completed":
          updated.phase = "completed";
          updated.endedAt = at;
          updated.detail = event.detail ?? current.detail;
          break;
        case "skipped":
          updated.phase = "skipped";
          updated.startedAt = at;
          updated.endedAt = at;
          updated.detail = event.detail;
          break;
        case "failed":
          updated.phase = "failed";
          updated.endedAt = at;
          updated.detail = event.detail ?? current.detail;
          break;
      }
      const steps = view.steps.map((s, i) => (i === index ? updated : s));
      return {
        ...next,
        phase: view.phase === "waiting" || view.phase === "queued" ? "running" : view.phase,
        steps,
        doneCount: countDone(steps),
        announcement: `Step ${updated.number} of ${TOTAL_STEPS}, ${updated.title}: ${PHASE_WORDS[updated.phase]}.`,
      };
    }
  }
}

function countDone(steps: readonly StepView[]): number {
  return steps.filter((s) => s.phase === "completed" || s.phase === "skipped").length;
}

export function isFinished(view: RunView): boolean {
  return view.phase === "completed" || view.phase === "failed";
}

/** "0.31 s", "12.4 s", "2 min 05 s". */
export function formatDuration(ms: number): string {
  if (ms < 0) return "0.00 s";
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)} s`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes} min ${String(seconds).padStart(2, "0")} s`;
}
