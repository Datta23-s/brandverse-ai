"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useSession } from "@/lib/use-session";

/**
 * Renders `children` for a signed-in user; sends anyone else to sign in, then back here.
 * A convenience, not a security boundary: the API refuses unauthenticated requests itself.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const state = useSession();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (state.status === "signed_out") router.replace(`/sign-in?next=${encodeURIComponent(path)}`);
  }, [state.status, router, path]);
  if (state.status !== "signed_in") {
    return (
      <p className="text-bv-faint" role="status">
        {state.status === "loading" ? "Checking your session…" : "Redirecting to sign in…"}
      </p>
    );
  }
  return <>{children}</>;
}
