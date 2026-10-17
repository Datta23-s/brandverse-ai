"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config";

let client: SupabaseClient | undefined;

/** The browser's one Supabase client: Auth only (data goes through our API). */
export function supabase(): SupabaseClient {
  if (client === undefined) {
    if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
      throw new Error("NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY must be set.");
    }
    client = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return client;
}

export class NotSignedIn extends Error {
  constructor() {
    super("Sign in to continue.");
    this.name = "NotSignedIn";
  }
}

/** The current access token, refreshed by the client if it is about to expire. */
export async function accessToken(): Promise<string> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  if (token === undefined) throw new NotSignedIn();
  return token;
}
