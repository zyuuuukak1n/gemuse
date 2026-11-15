"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Check,
  Copy,
  ExternalLink,
  Paperclip,
  Pencil,
  RotateCcw,
  Sparkles,
  User,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import type { ApprovalCardData, ChatMessage } from "@/types/gemuse";
import { triggerHaptic } from "@/lib/haptics";
import { ApprovalCard } from "./ApprovalCard";
import { extractGoogleFileLinks, GoogleFileCard } from "./GoogleFileCard";
import { ThinkingAccordion } from "./ThinkingAccordion";

interface MessageBubbleProps {
  message: ChatMessage;
  isLastUserMessage?: boolean;
  onApprovalUpdate?: (messageId: string, updated: ApprovalCardData) => void;
  onRegenerate?: (messageId: string) => void;
  onEditResend?: (messageId: string, newContent: string) => Promise<void> | void;
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
      className="whitespace-nowrap border-b border-slate-200 bg-slate-100 px-3 py-2 font-semibold text-slate-800 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200"
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
          textLength > 24 ? "min-w-[160px] whitespace-normal" : "whitespace-nowrap"
        }`}
        {...props}
      >
        {children}
      </td>
    );
  },
};

export function MessageBubble({
  message,
  isLastUserMessage = false,
  onApprovalUpdate,
  onRegenerate,
  onEditResend,
}: MessageBubbleProps) {
  const isUser = message.role === "user";

  const [copied, setCopied] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState(message.content);
  const copyTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      if (speaking && typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, [speaking]);

  async function handleCopy() {
    triggerHaptic();
    if (!message.content) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(message.content);
      }
      setCopied(true);
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => {
        setCopied(false);
      }, 1500);
    } catch {
      // Ignore clipboard permission denials
    }
  }

  function handleSpeakToggle() {
    triggerHaptic();
    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      return;
    }
    if (speaking) {
      window.speechSynthesis.cancel();
      setSpeaking(false);
      return;
    }

    window.speechSynthesis.cancel();
    const Utterance = window.SpeechSynthesisUtterance;
    if (!Utterance) return;
    const utterance = new Utterance(message.content);
    utterance.lang = "ja-JP";
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  }

  function handleSaveEditResend() {
    const trimmed = editDraft.trim();
    if (!trimmed) return;
    triggerHaptic();
    setEditing(false);
    void onEditResend?.(message.id, trimmed);
  }

  if (isUser) {
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="flex w-full justify-end gap-3">
          {editing ? (
            <div className="w-full max-w-[85%] rounded-2xl border border-emerald-500/50 bg-white p-3 shadow-sm dark:bg-slate-900 md:max-w-[75%]">
              <textarea
                rows={3}
                value={editDraft}
                onChange={(e) => setEditDraft(e.target.value)}
                aria-label="メッセージを編集"
                autoFocus
                className="w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-sm text-slate-900 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
              <div className="mt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setEditing(false);
                    setEditDraft(message.content);
                  }}
                  className="inline-flex min-h-[36px] items-center gap-1 rounded-xl border border-slate-200 px-3 text-xs font-medium text-slate-600 transition-transform duration-100 hover:bg-slate-100 active:scale-95 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                >
                  <X className="h-3.5 w-3.5" />
                  <span>キャンセル</span>
                </button>
                <button
                  type="button"
                  disabled={!editDraft.trim()}
                  onClick={handleSaveEditResend}
                  className="inline-flex min-h-[36px] items-center gap-1 rounded-xl bg-emerald-600 px-3 text-xs font-semibold text-white transition-transform duration-100 hover:bg-emerald-500 active:scale-95 disabled:opacity-40"
                >
                  <Check className="h-3.5 w-3.5" />
                  <span>保存して再送信</span>
                </button>
              </div>
            </div>
          ) : (
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
          )}

          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
            <User className="h-4 w-4" strokeWidth={1.75} />
          </div>
        </div>

        {isLastUserMessage && !editing && onEditResend ? (
          <div className="mr-11 flex items-center">
            <button
              type="button"
              onClick={() => {
                triggerHaptic();
                setEditDraft(message.content);
                setEditing(true);
              }}
              aria-label="メッセージを編集して再送信"
              title="編集して再送信"
              className="inline-flex min-h-[36px] items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-slate-400 transition-transform duration-100 hover:bg-slate-100 hover:text-slate-700 active:scale-95 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <Pencil className="h-3 w-3" strokeWidth={1.75} />
              <span>編集</span>
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  const hasActiveTool = (message.tool_calls || []).some(
    (tc) => tc.status !== "completed" && tc.status !== "error"
  );
  const streamingStatusText = hasActiveTool ? "ツールを実行中..." : "思考中...";

  return (
    <div className="flex items-start gap-3">
      <div
        data-testid="ai-avatar"
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30 transition ${
          message.is_streaming ? "animate-pulse ring-2 ring-emerald-400/70" : ""
        }`}
      >
        <Sparkles className="h-4 w-4" strokeWidth={1.75} />
      </div>

      <div className="min-w-0 flex-1">
        <div className="space-y-2 rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 shadow-sm dark:border-slate-800 dark:bg-[#101815]">
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
            <div
              data-testid="ai-streaming-status"
              className="flex items-center gap-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400"
            >
              <Sparkles className="h-3.5 w-3.5 animate-pulse" strokeWidth={1.75} />
              <span className="animate-pulse">{streamingStatusText}</span>
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

        {/* Compact Assistant Message Action Bar */}
        {!message.is_streaming && message.content.trim() ? (
          <div
            data-testid="assistant-action-bar"
            className="mt-1.5 flex items-center gap-1 pl-1"
          >
            <button
              type="button"
              onClick={() => void handleCopy()}
              aria-label={copied ? "コピーしました" : "回答をコピー"}
              title="回答をコピー"
              className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl text-slate-400 transition-transform duration-100 hover:bg-slate-100 hover:text-emerald-600 active:scale-95 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
            >
              {copied ? (
                <Check className="h-4 w-4 text-emerald-500" strokeWidth={2} />
              ) : (
                <Copy className="h-4 w-4" strokeWidth={1.75} />
              )}
            </button>

            {onRegenerate ? (
              <button
                type="button"
                onClick={() => {
                  triggerHaptic();
                  onRegenerate(message.id);
                }}
                aria-label="回答を再生成"
                title="同じプロンプトで再生成"
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl text-slate-400 transition-transform duration-100 hover:bg-slate-100 hover:text-emerald-600 active:scale-95 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
              >
                <RotateCcw className="h-4 w-4" strokeWidth={1.75} />
              </button>
            ) : null}

            <button
              type="button"
              onClick={handleSpeakToggle}
              aria-label={speaking ? "読み上げを停止" : "音声で読み上げる"}
              aria-pressed={speaking}
              title={speaking ? "読み上げを停止" : "音声読み上げ"}
              className={`inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl transition-transform duration-100 active:scale-95 ${
                speaking
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "text-slate-400 hover:bg-slate-100 hover:text-emerald-600 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
              }`}
            >
              {speaking ? (
                <VolumeX className="h-4 w-4" strokeWidth={1.75} />
              ) : (
                <Volume2 className="h-4 w-4" strokeWidth={1.75} />
              )}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
