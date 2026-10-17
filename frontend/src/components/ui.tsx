import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { forwardRef } from "react";

/** A labelled input with its error announced; ≥44px target (Design.md §10). */
export const Field = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string }
>(function Field({ label, error, hint, id, className, ...input }, ref) {
  const fieldId = id ?? input.name;
  const describedBy = error || hint ? `${fieldId}-help` : undefined;
  return (
    <div className={className}>
      <label htmlFor={fieldId} className="mb-1.5 block text-sm font-medium text-bv-muted">
        {label}
      </label>
      <input
        ref={ref}
        id={fieldId}
        aria-invalid={error !== undefined}
        aria-describedby={describedBy}
        className="min-h-11 w-full rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm aria-[invalid=true]:border-bv-danger/70"
        {...input}
      />
      {describedBy && (
        <p id={describedBy} className={`mt-1 text-sm ${error ? "text-bv-danger" : "text-bv-faint"}`}>
          {error ?? hint}
        </p>
      )}
    </div>
  );
});

export function Button({
  variant = "primary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "quiet" | "danger" }) {
  const look = {
    primary: "bg-bv-text text-bv-void hover:opacity-90",
    quiet: "border border-bv-glass-edge text-bv-text hover:bg-bv-glass",
    danger: "border border-bv-danger/50 text-bv-danger hover:bg-bv-danger/10",
  }[variant];
  return (
    <button
      type="button"
      className={`min-h-11 rounded-bv-sm px-4 text-sm font-medium transition-opacity duration-150 disabled:opacity-50 ${look} ${className}`}
      {...props}
    />
  );
}

export function Alert({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-bv-sm border border-bv-danger/50 bg-bv-danger/5 p-3 text-sm text-bv-danger">
      {children}
    </p>
  );
}

export function Panel({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="glass p-5 sm:p-6" aria-label={title}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium uppercase tracking-[0.08em] text-bv-faint">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}
