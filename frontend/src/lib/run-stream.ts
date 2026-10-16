/**
 * The run-progress stream (GET /api/v1/runs/{id}/stream), as an async iterable of validated
 * events that survives dropped connections.
 *
 * Streamed with fetch rather than EventSource: the client must send `Last-Event-ID` itself to
 * resume, and authenticated streaming (chunk B) needs an Authorization header, which EventSource
 * cannot send. It therefore implements EventSource's reconnect behaviour explicitly:
 *
 * - a dropped connection, or a stream that ends before the run's final event, is reconnected
 *   after the server's `retry:` delay (doubling on consecutive failures, capped), resuming with
 *   `Last-Event-ID` so no event is lost or repeated;
 * - `204 No Content` means the server has nothing more for this client: the stream ends quietly;
 * - `404` and `403` end it with an error — reconnecting cannot help;
 * - the run's final event (`run.completed` / `run.failed`) ends it.
 */
import { RunEvent, TERMINAL_EVENT_TYPES, runEventSchema } from "@/contracts/runs";

import { SseParser } from "./sse";

export type ConnectionState =
  | { kind: "connecting" }
  | { kind: "live" }
  | { kind: "reconnecting"; attempt: number; delayMs: number }
  | { kind: "ended" };

export type RunStreamErrorCode = "not_found" | "forbidden" | "unreachable" | "protocol";

export class RunStreamError extends Error {
  constructor(
    readonly code: RunStreamErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "RunStreamError";
  }
}

export interface RunStreamOptions {
  apiUrl: string;
  signal: AbortSignal;
  onConnection?: (state: ConnectionState) => void;
  /** Consecutive failed attempts (no event received) before giving up. */
  maxConsecutiveFailures?: number;
  /** Reconnect delay until the server sends `retry:`. */
  initialRetryMs?: number;
  fetchFn?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

const MAX_BACKOFF_MS = 30_000;

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

export function streamUrl(apiUrl: string, runId: string): string {
  return `${apiUrl.replace(/\/$/, "")}/api/v1/runs/${encodeURIComponent(runId)}/stream`;
}

export async function* streamRunEvents(
  runId: string,
  options: RunStreamOptions,
): AsyncGenerator<RunEvent, void, undefined> {
  const {
    apiUrl,
    signal,
    onConnection,
    maxConsecutiveFailures = 8,
    initialRetryMs = 2000,
    fetchFn = fetch,
    sleep = abortableSleep,
  } = options;
  const url = streamUrl(apiUrl, runId);
  let lastEventId: string | undefined;
  let retryMs = initialRetryMs;
  let failures = 0;

  const failed = async (why: string): Promise<void> => {
    failures += 1;
    if (failures > maxConsecutiveFailures) {
      throw new RunStreamError("unreachable", `stream lost after ${failures} attempts: ${why}`);
    }
    const delayMs = Math.min(retryMs * 2 ** (failures - 1), MAX_BACKOFF_MS);
    onConnection?.({ kind: "reconnecting", attempt: failures, delayMs });
    await sleep(delayMs, signal);
  };

  onConnection?.({ kind: "connecting" });
  while (!signal.aborted) {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (lastEventId !== undefined) headers["Last-Event-ID"] = lastEventId;

    let response: Response;
    try {
      response = await fetchFn(url, { headers, signal, cache: "no-store" });
    } catch (error) {
      if (signal.aborted) return;
      await failed(error instanceof Error ? error.message : "network error");
      continue;
    }

    if (response.status === 204) {
      onConnection?.({ kind: "ended" });
      return;
    }
    if (response.status === 404) {
      throw new RunStreamError("not_found", "No such run, or its events have expired.");
    }
    if (response.status === 403) {
      throw new RunStreamError("forbidden", await errorDetail(response));
    }
    if (!response.ok || response.body === null) {
      await response.body?.cancel();
      await failed(`HTTP ${response.status}`);
      continue;
    }
    if (!(response.headers.get("content-type") ?? "").startsWith("text/event-stream")) {
      await response.body.cancel();
      throw new RunStreamError("protocol", "The server did not answer with an event stream.");
    }

    onConnection?.({ kind: "live" });
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    const parser = new SseParser();
    try {
      for (;;) {
        let chunk: ReadableStreamReadResult<string>;
        try {
          chunk = await reader.read();
        } catch (error) {
          if (signal.aborted) return;
          throw new DroppedConnection(error instanceof Error ? error.message : "read failed");
        }
        if (chunk.done) break;
        for (const message of parser.push(chunk.value)) {
          if (message.retry !== undefined) retryMs = message.retry;
          if (message.id !== undefined) lastEventId = message.id;
          if (message.data === "") continue;
          const event = parseEvent(message.event, message.data, runId);
          failures = 0;
          yield event;
          if (TERMINAL_EVENT_TYPES.has(event.type)) {
            onConnection?.({ kind: "ended" });
            return;
          }
        }
      }
      // The server closed the stream before the run's final event: resume where we were.
      await failed("stream closed before the run finished");
    } catch (error) {
      if (!(error instanceof DroppedConnection)) throw error;
      await failed(error.message);
    } finally {
      await reader.cancel().catch(() => undefined);
    }
  }
}

class DroppedConnection extends Error {}

function parseEvent(name: string, data: string, runId: string): RunEvent {
  let json: unknown;
  try {
    json = JSON.parse(data);
  } catch {
    throw new RunStreamError("protocol", "An event's data is not JSON.");
  }
  const parsed = runEventSchema.safeParse(json);
  if (!parsed.success) {
    throw new RunStreamError("protocol", `An event does not match the contract: ${parsed.error.message}`);
  }
  if (parsed.data.type !== name) {
    throw new RunStreamError("protocol", `Event named ${name} carries a ${parsed.data.type}.`);
  }
  if (parsed.data.run_id !== runId) {
    throw new RunStreamError("protocol", "An event belongs to a different run.");
  }
  return parsed.data;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      return String(body.detail);
    }
  } catch {
    // fall through to the generic message
  }
  return "Not allowed to stream this run.";
}
