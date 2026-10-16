"use client";

import { experimental_streamedQuery as streamedQuery, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { RunEvent } from "@/contracts/runs";

import { API_URL } from "./config";
import { ConnectionState, streamRunEvents } from "./run-stream";
import { RunView, initialRunView, reduceRunView } from "./run-view";

/**
 * A run's live view. The stream is server state, so it lives in TanStack Query: each event is
 * folded into the cached RunView as it arrives. The connection's own state (live, reconnecting)
 * is local UI state.
 */
export function useRunStream(runId: string) {
  const [connection, setConnection] = useState<ConnectionState>({ kind: "connecting" });
  const query = useQuery({
    queryKey: ["run-stream", runId],
    queryFn: streamedQuery<RunEvent, RunView>({
      streamFn: ({ signal }) =>
        streamRunEvents(runId, { apiUrl: API_URL, signal, onConnection: setConnection }),
      reducer: reduceRunView,
      initialValue: initialRunView,
    }),
    // The stream client reconnects by itself; a refetch would replay a run that is still live.
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  return {
    view: query.data ?? initialRunView,
    error: query.error,
    connection,
    restart: () => void query.refetch(),
  };
}

/** The current time, ticking every `intervalMs` while `enabled` (for live elapsed timers). */
export function useNow(intervalMs: number, enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, enabled]);
  return now;
}
