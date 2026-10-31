"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type { CampaignView, DecisionKind, FindingView } from "@/contracts/campaigns";
import { ApiError, api } from "@/lib/api";

import { Alert, Button, Panel } from "./ui";

const PART_ORDER = ["headline", "body", "call_to_action", "footer"];
const PART_LABEL: Record<string, string> = {
  headline: "Headline",
  body: "Body",
  call_to_action: "Call to action",
  footer: "Required disclaimer",
};

/**
 * The campaign view (Phases.md Phase 6 chunk E): the copy, and the gate findings beside it. The
 * approver does not rewrite text: a statement can be taken OUT, and the gates run again.
 *
 * A7's advisory is not here and cannot be: the API does not send it (ruled 2026-10-29).
 */
export function CampaignReview({ runId, awaiting, finished }: { runId: string; awaiting: boolean; finished: boolean }) {
  const client = useQueryClient();
  const key = ["campaign", runId];
  const campaign = useQuery({
    queryKey: key,
    queryFn: () => api.campaign(runId),
    enabled: awaiting || finished,
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 2,
  });
  // The run parks again after a revision round: read the campaign each time it is waiting.
  useEffect(() => {
    if (awaiting) void client.invalidateQueries({ queryKey: key });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaiting]);

  if (campaign.data === undefined) return null;
  const c = campaign.data;
  const waiting = awaiting && c.status === "awaiting_approval";

  return (
    <div className="mb-6 grid gap-4" data-testid="campaign">
      {c.status === "approved" && <Outcome tone="ok">Campaign approved.</Outcome>}
      {c.status === "rejected" && <Outcome tone="no">Campaign rejected. It ended the run; nothing was written to memory.</Outcome>}
      <Panel title="Campaign copy">
        <CopyAndFindings runId={runId} campaign={c} editable={waiting} onChanged={() => client.setQueryData(key, undefined)} />
      </Panel>
      {waiting && <ApprovalBar runId={runId} campaign={c} onDecided={() => void client.invalidateQueries({ queryKey: key })} />}
    </div>
  );
}

function Outcome({ tone, children }: { tone: "ok" | "no"; children: React.ReactNode }) {
  return (
    <p
      data-testid="campaign-outcome"
      role="status"
      className={`rounded-bv-md border p-4 font-medium ${tone === "ok" ? "border-bv-glass-edge bg-bv-glass" : "border-bv-danger/50 bg-bv-danger/5 text-bv-danger"}`}
    >
      {children}
    </p>
  );
}

function CopyAndFindings({
  runId,
  campaign,
  editable,
  onChanged,
}: {
  runId: string;
  campaign: CampaignView;
  editable: boolean;
  onChanged: () => void;
}) {
  const client = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const parts = PART_ORDER.filter((p) => campaign.copy_parts[p] !== undefined).concat(
    Object.keys(campaign.copy_parts).filter((p) => !PART_ORDER.includes(p)),
  );

  async function editOut(words: string) {
    setProblem(null);
    setPending(words);
    const before = campaign.edited_out.length;
    try {
      await api.editOut(runId, words);
      // The worker re-runs the gates; read the campaign until it shows one more edit-out.
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => setTimeout(r, 250));
        const fresh = await api.campaign(runId);
        if (fresh.edited_out.length > before) {
          client.setQueryData(["campaign", runId], fresh);
          setPending(null);
          return;
        }
      }
      setProblem("Those words could not be removed (they must appear exactly once, and the headline and body cannot be emptied).");
    } catch (e) {
      setProblem(e instanceof Error ? e.message : "The edit-out failed.");
    }
    setPending(null);
    onChanged();
    void client.invalidateQueries({ queryKey: ["campaign", runId] });
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="grid content-start gap-4" data-testid="campaign-copy">
        {parts.map((p) => (
          <div key={p}>
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-bv-faint">{PART_LABEL[p] ?? p}</p>
            <p data-part={p} className="mt-1 whitespace-pre-wrap text-[0.9375rem] leading-relaxed">
              {campaign.copy_parts[p]}
            </p>
          </div>
        ))}
        {campaign.edited_out.length > 0 && (
          <p className="text-sm text-bv-muted" data-testid="edited-out">
            Removed by the reviewer: {campaign.edited_out.map((e) => `“${e.trim()}”`).join(", ")}
          </p>
        )}
      </div>
      <div className="grid content-start gap-4" data-testid="campaign-findings">
        <p className="text-sm font-medium">
          Gates: <span data-testid="gate-outcome" className={campaign.gate_outcome === "block" ? "text-bv-danger" : ""}>{campaign.gate_outcome ?? "not run"}</span>
        </p>
        {campaign.findings.length === 0 ? (
          <p className="text-sm text-bv-muted">No findings.</p>
        ) : (
          <ul className="grid gap-2">
            {campaign.findings.map((f, i) => (
              <Finding key={`${f.kind}-${i}`} finding={f} editable={editable && !pending} onEditOut={editOut} />
            ))}
          </ul>
        )}
        {campaign.statements.length > 0 && (
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-bv-faint">Factual statements</p>
            <ul className="mt-1 grid gap-1">
              {campaign.statements.map((s, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2 text-sm" data-statement>
                  <span>“{s.text}”</span>
                  <span className="text-bv-faint">{s.cited ? "cites a claim" : "no claim"}</span>
                  {editable && (
                    <Button variant="quiet" className="min-h-9 px-3" disabled={pending !== null} onClick={() => void editOut(s.text)}>
                      Edit out
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {editable && <EditOutForm disabled={pending !== null} onSubmit={editOut} />}
        {pending !== null && <p className="text-sm text-bv-muted" role="status">Removing and re-running the gates…</p>}
        {problem !== null && <Alert>{problem}</Alert>}
      </div>
    </div>
  );
}

function Finding({ finding, editable, onEditOut }: { finding: FindingView; editable: boolean; onEditOut: (w: string) => void }) {
  return (
    <li
      data-finding={finding.kind}
      data-outcome={finding.outcome}
      className={`rounded-bv-sm border p-3 text-sm ${finding.outcome === "block" ? "border-bv-danger/50 bg-bv-danger/5" : "border-bv-glass-edge"}`}
    >
      <p className="font-medium">
        <span className={finding.outcome === "block" ? "text-bv-danger" : "text-bv-muted"}>{finding.outcome}</span> · {finding.gate} · {finding.kind}
      </p>
      <p className="mt-1 text-bv-muted">{finding.message}</p>
      {finding.span !== null && editable && (
        <Button variant="quiet" className="mt-2 min-h-9 px-3" onClick={() => onEditOut(finding.span as string)}>
          Edit out “{finding.span}”
        </Button>
      )}
    </li>
  );
}

function EditOutForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (w: string) => void }) {
  const [words, setWords] = useState("");
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (words !== "") onSubmit(words);
        setWords("");
      }}
    >
      <label className="grid gap-1 text-sm font-medium text-bv-muted">
        Exact words to remove
        <input
          value={words}
          onChange={(e) => setWords(e.target.value)}
          className="min-h-11 w-64 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm font-normal"
        />
      </label>
      <Button type="submit" variant="quiet" disabled={disabled || words === ""}>
        Remove
      </Button>
    </form>
  );
}

/** Approve, request a revision, or reject (Design.md §8, `ApprovalBar`). */
function ApprovalBar({ runId, campaign, onDecided }: { runId: string; campaign: CampaignView; onDecided: () => void }) {
  const [reason, setReason] = useState("");
  const [gate, setGate] = useState("");
  const [needReason, setNeedReason] = useState(false);
  const started = useRef(Date.now());
  const decide = useMutation({
    mutationFn: (kind: DecisionKind) =>
      api.decide(runId, kind, {
        reason: reason.trim() === "" ? null : reason.trim(),
        failed_gate: gate.trim() === "" ? null : gate.trim(),
        seconds_spent: Math.min(86_400, Math.round((Date.now() - started.current) / 1000)),
      }),
    onSuccess: onDecided,
  });
  const go = (kind: DecisionKind) => {
    if (kind !== "approve" && reason.trim() === "") {
      setNeedReason(true);
      return;
    }
    setNeedReason(false);
    decide.mutate(kind);
  };
  const blocked = campaign.gate_outcome === "block";
  return (
    <Panel title="Your decision">
      <div className="grid gap-4" data-testid="approval-bar">
        {blocked && (
          <p className="text-sm text-bv-danger" role="status">
            The gates blocked this copy. You can still decide; approving a blocked draft is recorded as such.
          </p>
        )}
        <div className="grid gap-4 sm:grid-cols-[1fr_12rem]">
          <label className="grid gap-1 text-sm font-medium text-bv-muted">
            Reason (required to revise or reject)
            <textarea
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-invalid={needReason}
              className="rounded-bv-sm border border-bv-border bg-bv-deep px-3 py-2 text-sm font-normal"
            />
          </label>
          <label className="grid gap-1 text-sm font-medium text-bv-muted">
            Rubric gate (optional)
            <input
              value={gate}
              onChange={(e) => setGate(e.target.value)}
              placeholder="T1.3"
              className="min-h-11 rounded-bv-sm border border-bv-border bg-bv-deep px-3 text-sm font-normal"
            />
          </label>
        </div>
        {needReason && <p className="text-sm text-bv-danger">Say why: a reason is required to revise or reject.</p>}
        {decide.error !== null && (
          <Alert>
            {decide.error instanceof ApiError && decide.error.status === 403
              ? "Only a member with the approver role can decide a campaign."
              : decide.error.message}
          </Alert>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={decide.isPending} onClick={() => go("approve")}>
            Approve
          </Button>
          <Button variant="quiet" disabled={decide.isPending || campaign.rounds_remaining === 0} onClick={() => go("revise")}>
            Request a revision
          </Button>
          <Button variant="danger" disabled={decide.isPending} onClick={() => go("reject")}>
            Reject
          </Button>
          <span className="text-sm text-bv-faint" data-testid="rounds-remaining">
            {campaign.rounds_remaining} revision {campaign.rounds_remaining === 1 ? "round" : "rounds"} remaining
          </span>
        </div>
        {decide.isSuccess && <p className="text-sm text-bv-muted" role="status">Decision recorded. The run is continuing.</p>}
      </div>
    </Panel>
  );
}
