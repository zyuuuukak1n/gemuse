"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen, ExternalLink, Paperclip, Sparkles, User } from "lucide-react";
import type { ApprovalCardData, ChatMessage } from "@/types/gemuse";
import { ApprovalCard } from "./ApprovalCard";
import { extractGoogleFileLinks, GoogleFileCard } from "./GoogleFileCard";
import { ThinkingAccordion } from "./ThinkingAccordion";

interface MessageBubbleProps {
  message: ChatMessage;
  onApprovalUpdate?: (messageId: string, updated: ApprovalCardData) => void;
}

function extractNodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") {
    return String(node);
  }
  if (Array.isArray(node)) {
    return node.map(extractNodeText).join("");
  }
  if (node && typeof node === "object" && "props" in node) {
    const props = (node as { props?: { children?: ReactNode } }).props;
    return props?.children ? extractNodeText(props.children) : "";
  }
  return "";
}

const markdownComponents: Components = {
  a: ({ href, children, ...props }) => {
    const safeHref = typeof href === "string" ? href : undefined;
    const isExternal = Boolean(safeHref && /^https?:\/\//i.test(safeHref));
    return (
      <a
        href={safeHref}
        target={isExternal ? "_blank" : undefined}
        rel={isExternal ? "noopener noreferrer" : undefined}
        className="inline-flex items-center gap-1 font-medium text-emerald-700 underline decoration-emerald-500/40 underline-offset-2 transition hover:text-emerald-600 dark:text-emerald-400 dark:hover:text-emerald-300"
        {...props}
      >
        <span>{children}</span>
        {isExternal ? <ExternalLink className="inline h-3 w-3 shrink-0" strokeWidth={1.75} /> : null}
      </a>
    );
  },
  table: ({ children, ...props }) => (
    <div
      data-testid="markdown-table-scroll"
      className="my-3 w-full overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800"
    >
      <table
        className="w-max min-w-full border-collapse text-left text-xs sm:text-sm"
        {...props}
      >
        {children}
      </table>
    </div>
  ),
  th: ({ children, ...props }) => (
    <th
      className="whitespace-nowrap border-b border-slate-200 bg-slate-100/80 px-3 py-2 font-semibold text-slate-800 dark:border-slate-800 dark:bg-slate-900/80 dark:text-slate-200"
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ children, ...props }) => {
    const textLength = extractNodeText(children).trim().length;
    return (
      <td
        className={`border-b border-slate-100 px-3 py-2 align-top text-slate-700 dark:border-slate-800/70 dark:text-slate-300 ${
          textLength > 24
            ? "min-w-[160px] whitespace-normal"
            : "whitespace-nowrap"
        }`}
        {...props}
      >
        {children}
      </td>
    );
  },
};

export function MessageBubble({ message, onApprovalUpdate }: MessageBubbleProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end gap-3">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-emerald-600 px-4 py-3 text-sm text-white shadow-sm md:max-w-[75%]">
          <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>
          {message.attachments && message.attachments.length > 0 ? (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {message.attachments.map((file) => (
                <span
                  key={file}
                  className="inline-flex items-center gap-1 rounded-lg bg-emerald-700/70 px-2 py-0.5 text-[11px]"
                >
                  <Paperclip className="h-3 w-3" strokeWidth={1.75} />
                  {file}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
          <User className="h-4 w-4" strokeWidth={1.75} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30">
        <Sparkles className="h-4 w-4" strokeWidth={1.75} />
      </div>
      <div className="min-w-0 flex-1 space-y-2 rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-[#101815]">
        <ThinkingAccordion
          thinkingText={message.thinking_summary}
          toolCalls={message.tool_calls}
          isStreaming={message.is_streaming}
        />

        {message.content ? (
          <div className="prose prose-sm max-w-none break-words text-slate-800 dark:prose-invert dark:text-slate-100 prose-pre:overflow-x-auto prose-pre:rounded-xl prose-pre:border prose-pre:border-slate-800 prose-pre:bg-slate-950 prose-pre:p-3 prose-code:text-emerald-600 dark:prose-code:text-emerald-400">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          </div>
        ) : message.is_streaming ? (
          <div className="flex items-center gap-1.5 py-1 text-xs text-slate-400">
            <span className="h-2 w-2 animate-bounce rounded-full bg-emerald-500" />
            <span>応答を生成しています...</span>
          </div>
        ) : null}

        {(() => {
          const googleFiles = message.content ? extractGoogleFileLinks(message.content) : [];
          if (googleFiles.length === 0) return null;
          return (
            <div className="space-y-2 pt-1" data-testid="google-file-card-list">
              {googleFiles.map((item) => (
                <GoogleFileCard
                  key={item.url}
                  url={item.url}
                  title={item.title}
                  accountLabel={item.accountLabel}
                />
              ))}
            </div>
          );
        })()}

        {message.approval ? (
          <ApprovalCard
            approval={message.approval}
            onUpdated={(updated) => onApprovalUpdate?.(message.id, updated)}
          />
        ) : null}

        {message.citations && message.citations.length > 0 ? (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2 dark:border-slate-800/80">
            <BookOpen className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
            {message.citations.map((cite) => (
              <span
                key={cite}
                className="rounded-lg border border-emerald-500/25 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300"
              >
                {cite}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

