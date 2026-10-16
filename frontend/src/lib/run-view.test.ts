import { beforeEach, describe, expect, it } from "vitest";

import type { RunEvent } from "@/contracts/runs";

import { formatDuration, initialRunView, reduceRunView, type RunView } from "./run-view";
import { failed, fullRun, queued, resetClock, started, step } from "./test-events";

const fold = (events: RunEvent[]): RunView => events.reduce(reduceRunView, initialRunView);

beforeEach(() => resetClock());

describe("reduceRunView", () => {
  it("starts with all 15 steps waiting", () => {
    expect(initialRunView.steps).toHaveLength(15);
    expect(initialRunView.steps.every((s) => s.phase === "pending")).toBe(true);
    expect(initialRunView.phase).toBe("waiting");
  });

  it("marks the running step active and earlier ones done", () => {
    const view = fold([queued(), started(), step(1, "started"), step(1, "completed"), step(2, "started")]);
    expect(view.phase).toBe("running");
    expect(view.steps.map((s) => s.phase).slice(0, 3)).toEqual(["completed", "active", "pending"]);
    expect(view.doneCount).toBe(1);
    expect(view.announcement).toBe("Step 2 of 15, Load product context: running.");
  });

  it("times each step from the server's timestamps", () => {
    const view = fold([queued(), started(), step(1, "started"), step(1, "completed", null, 310)]);
    const s1 = view.steps[0];
    expect(s1?.endedAt !== null && s1?.startedAt !== null && s1!.endedAt! - s1!.startedAt!).toBe(310);
  });

  it("completes a full run with every step done", () => {
    const view = fold(fullRun());
    expect(view.phase).toBe("completed");
    expect(view.doneCount).toBe(15);
    expect(view.steps.every((s) => s.phase === "completed")).toBe(true);
    expect(view.eventCount).toBe(33);
  });

  it("keeps a skipped step visible as skipped, and counts it as done", () => {
    const view = fold(fullRun({ skipResearch: true }));
    expect(view.steps[5]).toMatchObject({ number: 6, phase: "skipped", detail: "fake: D0" });
    expect(view.doneCount).toBe(15);
  });

  it("keeps the start detail when the completion carries none", () => {
    const view = fold([queued(), started(), step(5, "started", "fake: depth D1"), step(5, "completed")]);
    expect(view.steps[4]?.detail).toBe("fake: depth D1");
  });

  it("on failure: the failed step is failed, later steps not run, the error kept", () => {
    const view = fold([
      queued(),
      started(),
      step(1, "started"),
      step(1, "completed"),
      step(2, "started"),
      step(2, "failed", "simulated failure at step 2"),
      failed(2, "simulated failure at step 2 (load_product_context)"),
    ]);
    expect(view.phase).toBe("failed");
    expect(view.failedStep).toBe(2);
    expect(view.error).toBe("simulated failure at step 2 (load_product_context)");
    expect(view.steps.map((s) => s.phase).slice(0, 4)).toEqual(["completed", "failed", "not_run", "not_run"]);
    expect(view.steps.slice(2).every((s) => s.phase === "not_run")).toBe(true);
    expect(view.announcement).toContain("Run failed at step 2");
  });

  it("a crash with no failed step event marks the active step failed", () => {
    const view = fold([queued(), started(), step(1, "started"), failed(null, "internal error: RuntimeError")]);
    expect(view.steps[0]?.phase).toBe("failed");
    expect(view.failedStep).toBeNull();
  });

  it("is pure: the input view is not changed", () => {
    const before = fold([queued(), started()]);
    const snapshot = JSON.stringify(before);
    reduceRunView(before, step(1, "started"));
    expect(JSON.stringify(before)).toBe(snapshot);
  });
});

describe("formatDuration", () => {
  it.each([
    [310, "0.31 s"],
    [9_999, "10.00 s"],
    [12_400, "12.4 s"],
    [125_000, "2 min 05 s"],
    [-5, "0.00 s"],
  ])("%i ms → %s", (ms, text) => expect(formatDuration(ms)).toBe(text));
});
