import { StartFakeRunForm } from "@/components/StartFakeRunForm";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="font-display text-[clamp(2.75rem,7vw,4.5rem)] leading-[1.05] tracking-[-0.02em]">
        Watch the 15-step pipeline run.
      </h1>
      <p className="mt-4 max-w-2xl text-bv-muted">
        This starts a <strong className="font-medium text-bv-text">fake run</strong> on the
        laptop&apos;s worker. No model is called and nothing is researched or written: each step
        waits, then reports. It exists so the live view can be built and checked now, before the
        real pipeline arrives in Phase 6.
      </p>
      <div className="mt-8">
        <StartFakeRunForm />
      </div>
    </div>
  );
}
