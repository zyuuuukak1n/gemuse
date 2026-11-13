"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Cpu, Wrench } from "lucide-react";
import type { SSEToolCallData } from "@/types/gemuse";

interface ThinkingAccordionProps {
  thinkingText?: string | null;
  toolCalls?: SSEToolCallData[];
  isStreaming?: boolean;
}

export function ThinkingAccordion({
  thinkingText,
  toolCalls = [],
  isStreaming = false,
}: ThinkingAccordionProps) {
  const [open, setOpen] = useState(false);

  if (!thinkingText && toolCalls.length === 0) {
    return null;
  }

  return (
    <div className="mb-2.5 rounded-xl border border-slate-200 bg-slate-50/80 dark:border-slate-800 dark:bg-slate-900/50">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs font-medium text-slate-600 transition hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-200"
      >
        <span className="flex items-center gap-2">
          <Cpu
            className={`h-3.5 w-3.5 text-emerald-500 ${isStreaming ? "animate-pulse" : ""}`}
            strokeWidth={1.75}
          />
          <span>
            {isStreaming ? "思考・ツール連携を実行中..." : "思考プロセスと実行ツール"}
          </span>
          {toolCalls.length > 0 ? (
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400">
              ツール {toolCalls.length}件
            </span>
          ) : null}
        </span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
        ) : (
          <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} />
        )}
      </button>

      {open ? (
        <div className="space-y-2 border-t border-slate-200 px-3 py-2.5 text-xs dark:border-slate-800">
          {toolCalls.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {toolCalls.map((tool, idx) => (
                <span
                  key={`${tool.tool_name}-${idx}`}
                  className={`inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[11px] font-medium ${
                    tool.status === "completed"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                      : tool.status === "blocked_for_approval"
                      ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
                      : tool.status === "error"
                      ? "border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                      : "border-slate-300 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                  }`}
                >
                  <Wrench className="h-3 w-3" strokeWidth={1.75} />
                  <span>{tool.tool_name}</span>
                  <span className="opacity-70">({tool.status})</span>
                </span>
              ))}
            </div>
          ) : null}

          {thinkingText ? (
            <p className="whitespace-pre-wrap leading-relaxed text-slate-600 dark:text-slate-400">
              {thinkingText}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

