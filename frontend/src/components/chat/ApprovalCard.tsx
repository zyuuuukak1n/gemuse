"use client";

import { useState } from "react";
import { Check, Loader2, ShieldAlert, X } from "lucide-react";
import type { ApprovalCardData } from "@/types/gemuse";

interface ApprovalCardProps {
  approval: ApprovalCardData;
  onUpdated?: (updated: ApprovalCardData) => void;
}

export function ApprovalCard({ approval, onUpdated }: ApprovalCardProps) {
  const [busy, setBusy] = useState<"approved" | "rejected" | null>(null);
  const [error, setError] = useState<string | null>(approval.error_message || null);

  const resolved = approval.decision_status !== "pending";

  async function handleDecision(decision: "approved" | "rejected") {
    setBusy(decision);
    setError(null);
    try {
      const resolveRes = await fetch(`/api/proxy/approval/${approval.approval_id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          token: approval.one_time_token,
        }),
      });

      if (!resolveRes.ok) {
        const body = (await resolveRes.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || `承認APIエラー (${resolveRes.status})`);
      }

      if (decision === "rejected") {
        onUpdated?.({
          ...approval,
          decision_status: "rejected",
        });
        return;
      }

      const execRes = await fetch("/api/proxy/mcp/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: approval.action_type,
          arguments: approval.payload,
          approval_id: approval.approval_id,
        }),
      });

      if (!execRes.ok) {
        const body = (await execRes.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || `ツール実行エラー (${execRes.status})`);
      }

      const execData = (await execRes.json()) as { wrapped_output?: string };
      onUpdated?.({
        ...approval,
        decision_status: "executed",
        execution_output: execData.wrapped_output || "実行が完了しました。",
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "処理に失敗しました";
      setError(msg);
      onUpdated?.({
        ...approval,
        error_message: msg,
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      data-testid="approval-card"
      className="mt-2 rounded-xl border border-amber-500/40 bg-amber-50/70 p-3.5 dark:border-amber-500/30 dark:bg-amber-950/20"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-amber-800 dark:text-amber-300">
          <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={1.75} />
          <span>承認ゲート待機中: {approval.action_type}</span>
        </div>
        <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium uppercase text-amber-800 dark:text-amber-300">
          {approval.decision_status}
        </span>
      </div>

      <pre className="mt-2 max-h-40 overflow-auto rounded-lg border border-amber-500/20 bg-white/80 p-2 text-[11px] text-slate-700 dark:bg-slate-950/70 dark:text-slate-300">
        {JSON.stringify(approval.payload, null, 2)}
      </pre>

      {error ? (
        <p role="alert" className="mt-2 text-xs text-rose-600 dark:text-rose-400">
          {error}
        </p>
      ) : null}

      {approval.execution_output ? (
        <p className="mt-2 rounded-lg bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-700 dark:text-emerald-300">
          実行完了: {approval.execution_output.slice(0, 240)}
        </p>
      ) : null}

      {!resolved ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => handleDecision("approved")}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy === "approved" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} />
            ) : (
              <Check className="h-3.5 w-3.5" strokeWidth={1.75} />
            )}
            承認して実行
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => handleDecision("rejected")}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition hover:border-rose-400 hover:text-rose-600 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:text-rose-400"
          >
            {busy === "rejected" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} />
            ) : (
              <X className="h-3.5 w-3.5" strokeWidth={1.75} />
            )}
            却下
          </button>
        </div>
      ) : null}
    </div>
  );
}

