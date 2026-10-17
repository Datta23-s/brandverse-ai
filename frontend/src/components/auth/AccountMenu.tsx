"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { supabase } from "@/lib/supabase";
import { useSession } from "@/lib/use-session";

export function AccountMenu() {
  const state = useSession();
  const router = useRouter();
  if (state.status === "loading") return null;
  if (state.status === "signed_out") {
    return (
      <Link href="/sign-in" className="inline-flex min-h-11 items-center text-sm font-medium text-bv-muted hover:text-bv-text">
        Sign in
      </Link>
    );
  }
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="hidden text-bv-faint sm:inline" data-testid="signed-in-as">
        {state.session.user.email}
      </span>
      <button
        type="button"
        className="min-h-11 rounded-bv-sm px-3 font-medium text-bv-muted hover:text-bv-text"
        onClick={async () => {
          await supabase().auth.signOut();
          router.replace("/sign-in");
        }}
      >
        Sign out
      </button>
    </div>
  );
}
