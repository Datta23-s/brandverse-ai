import { Suspense } from "react";

import { SignInForm } from "@/components/auth/AuthForms";

export default function SignInPage() {
  return (
    <div className="mx-auto max-w-md">
      <h1 className="font-display text-[clamp(2.25rem,5vw,3.25rem)] leading-tight tracking-[-0.02em]">Sign in</h1>
      <p className="mt-2 mb-6 text-bv-muted">Campaign intelligence that reuses what it already knows.</p>
      {/* useSearchParams (the ?next= return path) needs a Suspense boundary. */}
      <Suspense>
        <SignInForm />
      </Suspense>
    </div>
  );
}
