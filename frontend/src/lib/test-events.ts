/** Event fixtures for tests: what the backend emits, built from the shared contract. */
import { PIPELINE_STEPS, type RunEvent, type StepStatus } from "@/contracts/runs";

export const RUN_ID = "3f0b1d9e-6a4c-4e8a-9a51-2f3c4d5e6f70";

let clock = Date.parse("2026-10-16T10:00:00.000Z");
export function resetClock(): void {
  clock = Date.parse("2026-10-16T10:00:00.000Z");
}
function ts(advanceMs = 100): string {
  clock += advanceMs;
  return new Date(clock).toISOString();
}

export const ORG_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
export const queued = (): RunEvent => ({
  type: "run.queued",
  run_id: RUN_ID,
  ts: ts(0),
  mode: "fake",
  org_id: ORG_ID,
  total_steps: 15,
});
export const started = (): RunEvent => ({ type: "run.started", run_id: RUN_ID, ts: ts(), total_steps: 15 });
export const completed = (): RunEvent => ({ type: "run.completed", run_id: RUN_ID, ts: ts() });
export const failed = (step: number | null, error = "boom"): RunEvent => ({
  type: "run.failed",
  run_id: RUN_ID,
  ts: ts(),
  step,
  error,
});
export function step(n: number, status: StepStatus, detail: string | null = null, advanceMs = 100): RunEvent {
  const s = PIPELINE_STEPS[n - 1];
  if (s === undefined) throw new Error(`no step ${n}`);
  return { type: "step", run_id: RUN_ID, ts: ts(advanceMs), step: n, name: s.name, status, detail };
}

/** A whole successful run, as the fake run emits it. */
export function fullRun({ skipResearch = false } = {}): RunEvent[] {
  const events: RunEvent[] = [queued(), started()];
  for (const s of PIPELINE_STEPS) {
    if (skipResearch && s.number === 6) {
      events.push(step(6, "skipped", "fake: D0"));
      continue;
    }
    events.push(step(s.number, "started"), step(s.number, "completed", null, 300));
  }
  events.push(completed());
  return events;
}

/** The SSE wire form of events, with stream-style IDs. */
export function toWire(events: RunEvent[], firstSeq = 1, retry?: number): string {
  return events
    .map((e, i) => {
      const lines = [`event: ${e.type}`, `id: ${1000 + firstSeq + i}-0`, `data: ${JSON.stringify(e)}`];
      if (retry !== undefined && i === 0) lines.push(`retry: ${retry}`);
      return `${lines.join("\n")}\n\n`;
    })
    .join("");
}
