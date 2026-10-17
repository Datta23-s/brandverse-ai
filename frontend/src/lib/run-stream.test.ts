import { beforeEach, describe, expect, it } from "vitest";

import type { RunEvent } from "@/contracts/runs";

import { RunStreamError, streamRunEvents, type ConnectionState } from "./run-stream";
import { RUN_ID, fullRun, resetClock, toWire } from "./test-events";

/** A scripted server: each call to fetch takes the next response from the script. */
type Scripted =
  | { status: 200; chunks: string[]; dropAfter?: boolean }
  | { status: number; body?: string }
  | { networkError: true };

function scriptedFetch(script: Scripted[]) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const fetchFn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    calls.push({ url: String(input), headers: { ...(init?.headers as Record<string, string>) } });
    const next = script.shift();
    if (next === undefined) throw new Error("unexpected extra request");
    if ("networkError" in next) throw new TypeError("Failed to fetch");
    if (next.status === 200 && "chunks" in next) {
      const { chunks, dropAfter } = next;
      // Pull-based, so every chunk is read before the drop: erroring a stream discards any
      // chunks still queued (Streams spec), which would drop before the events arrived.
      const enc = new TextEncoder();
      let i = 0;
      const body = new ReadableStream<Uint8Array>({
        pull(controller) {
          const chunk = chunks[i++];
          if (chunk !== undefined) controller.enqueue(enc.encode(chunk));
          else if (dropAfter) controller.error(new TypeError("network connection was lost"));
          else controller.close();
        },
      });
      return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    const body = "body" in next ? next.body : undefined;
    return new Response(next.status === 204 ? null : (body ?? ""), {
      status: next.status,
      headers: { "content-type": "application/json" },
    });
  };
  return { fetchFn: fetchFn as typeof fetch, calls };
}

async function collect(
  script: Scripted[],
  extra: { maxConsecutiveFailures?: number } = {},
): Promise<{ events: RunEvent[]; sleeps: number[]; states: ConnectionState[]; calls: ReturnType<typeof scriptedFetch>["calls"] }> {
  const { fetchFn, calls } = scriptedFetch(script);
  const sleeps: number[] = [];
  const states: ConnectionState[] = [];
  const events: RunEvent[] = [];
  for await (const e of streamRunEvents(RUN_ID, {
    apiUrl: "http://api.test",
    signal: new AbortController().signal,
    fetchFn,
    sleep: async (ms) => void sleeps.push(ms),
    onConnection: (s) => states.push(s),
    ...extra,
  })) {
    events.push(e);
  }
  return { events, sleeps, states, calls };
}

beforeEach(() => resetClock());

describe("streamRunEvents", () => {
  it("yields every event of a run and stops at the final one", async () => {
    const run = fullRun();
    const { events, calls, states } = await collect([{ status: 200, chunks: [toWire(run)] }]);
    expect(events).toEqual(run);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe(`http://api.test/api/v1/runs/${RUN_ID}/stream`);
    expect(calls[0]?.headers["Last-Event-ID"]).toBeUndefined();
    expect(states.map((s) => s.kind)).toEqual(["connecting", "live", "ended"]);
  });

  it("reconnects after a dropped connection, resuming after the last event it saw", async () => {
    const run = fullRun();
    const first = run.slice(0, 10);
    const rest = run.slice(10);
    const { events, calls, sleeps } = await collect([
      { status: 200, chunks: [toWire(first, 1, 2000)], dropAfter: true },
      { status: 200, chunks: [toWire(rest, 11)] },
    ]);
    expect(events).toEqual(run); // nothing lost, nothing repeated
    expect(calls[1]?.headers["Last-Event-ID"]).toBe("1010-0");
    expect(sleeps).toEqual([2000]); // the server's retry: delay
  });

  it("reconnects when the server ends the stream before the run's final event", async () => {
    const run = fullRun();
    const { events, calls } = await collect([
      { status: 200, chunks: [toWire(run.slice(0, 5))] },
      { status: 200, chunks: [toWire(run.slice(5), 6)] },
    ]);
    expect(events).toEqual(run);
    expect(calls[1]?.headers["Last-Event-ID"]).toBe("1005-0");
  });

  it("stops quietly on 204: the server has nothing more for this client", async () => {
    const run = fullRun();
    const { events, states } = await collect([
      { status: 200, chunks: [toWire(run.slice(0, 3))], dropAfter: true },
      { status: 204 },
    ]);
    expect(events).toEqual(run.slice(0, 3));
    expect(states.at(-1)).toEqual({ kind: "ended" });
  });

  it("backs off on repeated network errors and gives up as unreachable", async () => {
    const attempt = collect(
      [{ networkError: true }, { networkError: true }, { networkError: true }, { networkError: true }],
      { maxConsecutiveFailures: 3 },
    );
    await expect(attempt).rejects.toMatchObject({ code: "unreachable" });
  });

  it("doubles the delay on consecutive failures", async () => {
    const run = fullRun();
    const { sleeps } = await collect([
      { networkError: true },
      { status: 503 },
      { networkError: true },
      { status: 200, chunks: [toWire(run)] },
    ]);
    expect(sleeps).toEqual([2000, 4000, 8000]);
  });

  it("does not retry 404 or 403", async () => {
    await expect(collect([{ status: 404 }])).rejects.toMatchObject({ code: "not_found" });
    const forbidden = collect([{ status: 403, body: JSON.stringify({ detail: "fake runs only" }) }]);
    await expect(forbidden).rejects.toThrow(RunStreamError);
    await expect(collect([{ status: 403, body: JSON.stringify({ detail: "fake runs only" }) }])).rejects.toThrow(
      "fake runs only",
    );
  });

  it("rejects an event that breaks the contract", async () => {
    const bad = 'event: step\nid: 1-0\ndata: {"type":"step","run_id":"x"}\n\n';
    await expect(collect([{ status: 200, chunks: [bad] }])).rejects.toMatchObject({ code: "protocol" });
  });

  it("rejects an event whose SSE name disagrees with its type", async () => {
    const [e] = fullRun();
    const wire = `event: run.completed\nid: 1-0\ndata: ${JSON.stringify(e)}\n\n`;
    await expect(collect([{ status: 200, chunks: [wire] }])).rejects.toMatchObject({ code: "protocol" });
  });

  it("stops without error when aborted", async () => {
    const controller = new AbortController();
    const { fetchFn } = scriptedFetch([{ status: 200, chunks: [toWire(fullRun().slice(0, 2))], dropAfter: true }]);
    const seen: RunEvent[] = [];
    for await (const e of streamRunEvents(RUN_ID, {
      apiUrl: "http://api.test",
      signal: controller.signal,
      fetchFn,
      sleep: async () => controller.abort(),
    })) {
      seen.push(e);
    }
    expect(seen).toHaveLength(2);
  });
});

describe("streamRunEvents with authentication", () => {
  it("asks for a fresh token on every connection attempt", async () => {
    const run = fullRun();
    const { fetchFn, calls } = scriptedFetch([
      { status: 200, chunks: [toWire(run.slice(0, 4))], dropAfter: true },
      { status: 200, chunks: [toWire(run.slice(4), 5)] },
    ]);
    let n = 0;
    const events: RunEvent[] = [];
    for await (const e of streamRunEvents(RUN_ID, {
      apiUrl: "http://api.test",
      signal: new AbortController().signal,
      fetchFn,
      sleep: async () => undefined,
      authorization: async () => `token-${++n}`,
    })) {
      events.push(e);
    }
    expect(events).toEqual(run);
    expect(calls.map((c) => c.headers.Authorization)).toEqual(["Bearer token-1", "Bearer token-2"]);
  });

  it("a 401 ends the stream with a sign-in error, without retrying", async () => {
    await expect(collect([{ status: 401 }])).rejects.toMatchObject({ code: "unauthenticated" });
  });

  it("no session ends the stream before any request", async () => {
    const { fetchFn, calls } = scriptedFetch([]);
    const stream = streamRunEvents(RUN_ID, {
      apiUrl: "http://api.test",
      signal: new AbortController().signal,
      fetchFn,
      authorization: async () => {
        throw new Error("not signed in");
      },
    });
    await expect(stream.next()).rejects.toMatchObject({ code: "unauthenticated" });
    expect(calls).toHaveLength(0);
  });
});
