"use client";

import { MessageSquarePlus, RefreshCw } from "lucide-react";
import type { ConversationSummary, JobStatus } from "@/types/gemuse";

interface ConversationSidebarProps {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  loading: boolean;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onRefresh: () => void;
}

function statusBadgeClass(status: JobStatus | null): string {
  switch (status) {
    case "queued":
      return "bg-slate-500/20 text-slate-600 dark:text-slate-300";
    case "running":
      return "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300";
    case "waiting_approval":
    case "paused_manual":
      return "bg-amber-500/20 text-amber-700 dark:text-amber-300";
    case "failed":
      return "bg-rose-500/20 text-rose-600 dark:text-rose-300";
    case "completed":
      return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400";
    default:
      return "bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
  }
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  loading,
  onSelectConversation,
  onNewConversation,
  onRefresh,
}: ConversationSidebarProps) {
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-slate-200 bg-white/70 dark:border-emerald-950/60 dark:bg-[#0b110e]">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-3 dark:border-slate-800">
        <button
          type="button"
          onClick={onNewConversation}
          className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-2 text-xs font-medium text-white transition hover:bg-emerald-500"
        >
          <MessageSquarePlus className="h-3.5 w-3.5" strokeWidth={1.75} />
          <span>新しい会話</span>
        </button>
        <button
          type="button"
          onClick={onRefresh}
          aria-label="履歴を更新"
          className="ml-2 rounded-xl border border-slate-200 p-2 text-slate-500 transition hover:text-emerald-500 dark:border-slate-800 dark:text-slate-400"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} strokeWidth={1.75} />
        </button>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto p-2">
        {conversations.length === 0 ? (
          <p className="px-2 py-4 text-center text-xs text-slate-400">
            会話履歴はまだありません
          </p>
        ) : (
          conversations.map((conv) => {
            const active = conv.id === activeConversationId;
            return (
              <button
                key={conv.id}
                type="button"
                onClick={() => onSelectConversation(conv.id)}
                className={`w-full rounded-xl px-3 py-2.5 text-left transition ${
                  active
                    ? "bg-emerald-500/15 ring-1 ring-emerald-500/30"
                    : "hover:bg-slate-100 dark:hover:bg-slate-800/50"
                }`}
              >
                <div className="truncate text-xs font-medium text-slate-900 dark:text-slate-100">
                  {conv.title}
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  {conv.latest_job_status ? (
                    <span
                      className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${statusBadgeClass(
                        conv.latest_job_status
                      )}`}
                    >
                      {conv.latest_job_status}
                    </span>
                  ) : (
                    <span className="text-[10px] text-slate-400">idle</span>
                  )}
                  <span className="text-[10px] text-slate-400">{conv.thinking_level}</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

