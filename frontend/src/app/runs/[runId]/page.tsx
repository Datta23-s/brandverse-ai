import { notFound } from "next/navigation";
import { z } from "zod";

import { AuthGate } from "@/components/auth/AuthGate";
import { RunView } from "@/components/RunView";

export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  // Run IDs are UUIDs; anything else is not a run.
  if (!z.uuid().safeParse(runId).success) notFound();
  return (
    <AuthGate>
      <RunView runId={runId} />
    </AuthGate>
  );
}
