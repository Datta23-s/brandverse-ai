"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { supabase } from "@/lib/supabase";

import { Alert, Button, Field } from "../ui";

const email = z.email("Enter a valid email address.");

const signInSchema = z.object({ email, password: z.string().min(1, "Enter your password.") });
type SignIn = z.infer<typeof signInSchema>;

const signUpSchema = z
  .object({
    email,
    password: z.string().min(8, "At least 8 characters."),
    confirm: z.string(),
  })
  .refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match." });
type SignUp = z.infer<typeof signUpSchema>;

/** Only same-site paths: `?next=https://evil.example` must not redirect off the app. */
function safeNext(next: string | null): string {
  return next !== null && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export function SignInForm() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignIn>({ resolver: zodResolver(signInSchema) });

  const submit = handleSubmit(async (values) => {
    setError(null);
    const { error: authError } = await supabase().auth.signInWithPassword(values);
    if (authError) {
      setError(authError.message);
      return;
    }
    router.replace(next);
  });

  return (
    <form onSubmit={submit} noValidate className="glass grid gap-4 p-5 sm:p-6">
      <Field label="Email" type="email" autoComplete="email" {...register("email")} error={errors.email?.message} />
      <Field
        label="Password"
        type="password"
        autoComplete="current-password"
        {...register("password")}
        error={errors.password?.message}
      />
      {error && <Alert>{error}</Alert>}
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Signing in…" : "Sign in"}
      </Button>
      <p className="text-sm text-bv-faint">
        No account?{" "}
        <Link href="/sign-up" className="text-bv-text underline underline-offset-4">
          Sign up
        </Link>
      </p>
    </form>
  );
}

export function SignUpForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [confirmTo, setConfirmTo] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignUp>({ resolver: zodResolver(signUpSchema) });

  const submit = handleSubmit(async ({ email: address, password }) => {
    setError(null);
    const { data, error: authError } = await supabase().auth.signUp({
      email: address,
      password,
      options: { emailRedirectTo: `${window.location.origin}/sign-in` },
    });
    if (authError) {
      setError(authError.message);
      return;
    }
    if (data.session) {
      router.replace("/"); // the project confirms without email
      return;
    }
    setConfirmTo(address); // email confirmation is on (it is, on this project)
  });

  if (confirmTo !== null) {
    return (
      <div className="glass p-5 sm:p-6" role="status">
        <p className="font-medium">Check your email.</p>
        <p className="mt-2 text-sm text-bv-muted">
          We sent a confirmation link to <span className="text-bv-text">{confirmTo}</span>. Open it, then{" "}
          <Link href="/sign-in" className="text-bv-text underline underline-offset-4">
            sign in
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} noValidate className="glass grid gap-4 p-5 sm:p-6">
      <Field label="Email" type="email" autoComplete="email" {...register("email")} error={errors.email?.message} />
      <Field
        label="Password"
        type="password"
        autoComplete="new-password"
        {...register("password")}
        error={errors.password?.message}
        hint="At least 8 characters."
      />
      <Field
        label="Confirm password"
        type="password"
        autoComplete="new-password"
        {...register("confirm")}
        error={errors.confirm?.message}
      />
      {error && <Alert>{error}</Alert>}
      <Button type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Creating account…" : "Create account"}
      </Button>
      <p className="text-sm text-bv-faint">
        Have an account?{" "}
        <Link href="/sign-in" className="text-bv-text underline underline-offset-4">
          Sign in
        </Link>
      </p>
    </form>
  );
}
