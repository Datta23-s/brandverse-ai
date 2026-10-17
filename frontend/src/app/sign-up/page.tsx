import { SignUpForm } from "@/components/auth/AuthForms";

export default function SignUpPage() {
  return (
    <div className="mx-auto max-w-md">
      <h1 className="font-display text-[clamp(2.25rem,5vw,3.25rem)] leading-tight tracking-[-0.02em]">
        Create an account
      </h1>
      <p className="mt-2 mb-6 text-bv-muted">We will email you a link to confirm your address.</p>
      <SignUpForm />
    </div>
  );
}
