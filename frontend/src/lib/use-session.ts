"use client";

import type { Session } from "@supabase/supabase-js";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { supabase } from "./supabase";

export type SessionState =
  | { status: "loading" }
  | { status: "signed_out" }
  | { status: "signed_in"; session: Session };

/**
 * The browser's Supabase session (local UI state). Signing in or out clears the query cache, so
 * no server state of one user is ever shown to the next.
 */
export function useSession(): SessionState {
  const queryClient = useQueryClient();
  const [state, setState] = useState<SessionState>({ status: "loading" });
  useEffect(() => {
    const auth = supabase().auth;
    let userId: string | undefined;
    const apply = (session: Session | null) => {
      if (session?.user.id !== userId) queryClient.clear();
      userId = session?.user.id;
      setState(session === null ? { status: "signed_out" } : { status: "signed_in", session });
    };
    void auth.getSession().then(({ data }) => apply(data.session));
    const { data } = auth.onAuthStateChange((_event, session) => apply(session));
    return () => data.subscription.unsubscribe();
  }, [queryClient]);
  return state;
}
