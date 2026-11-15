"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  Bot,
  Brain,
  Compass,
  Loader2,
  Mail,
  Sparkles,
  X,
} from "lucide-react";
import { consumeSSEStream } from "@/lib/sse-client";
import type {
  ApprovalCardData,
  ChatMessage,
  ConversationDetail,
  JobSummary,
  SSEEvent,
  ThinkingLevel,
} from "@/types/gemuse";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { LiveBrowserPanel } from "@/components/browser/LiveBrowserPanel";

interface ChatWorkspaceProps {
  activeConversationId: string | null;
  newChatTrigger?: number;
  thinkingLevel: ThinkingLevel;
  backgroundMode: boolean;
  showBrowserPane: boolean;
  onChangeShowBrowserPane: (open: boolean) => void;
  onConversationChange: (conversationId: string | null) => void;
  onConversationsUpdated: () => void;
  onBackgroundJobStatusChange?: (status: string | null) => void;
  initialPrompt?: string | null;
  onConsumeInitialPrompt?: () => void;
  onPendingApprovalCountChange?: (count: number) => void;
  onBrowserPauseStateChange?: (paused: boolean) => void;
}

const SUGGESTION_CARDS = [
  {
    icon: Mail,
    title: "連絡・予定の整理",
    prompt: "未読メールと参加Discordの直近メッセージ、今日の予定を要約してください。",
  },
  {
    icon: Compass,
    title: "自律ブラウザ調査",
    prompt: "ブラウザで最新のWeb開発ドキュメントを開き、要点を整理してください。",
  },
  {
    icon: Brain,
    title: "記憶との統合",
    prompt: "コアメモリと直近のエピソード記憶を踏まえて、今週の優先タスクを提案して。",
  },
  {
    icon: Bot,
    title: "スキル手順の確認",
    prompt: "登録済みの自動化スキル一覧と活用可能なツールを教えてください。",
  },
];

function createTempId(prefix: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function ChatWorkspace({
  activeConversationId,
  newChatTrigger = 0,
  thinkingLevel,
  backgroundMode,
  showBrowserPane,
  onChangeShowBrowserPane,
  onConversationChange,
  onConversationsUpdated,
  onBackgroundJobStatusChange,
  initialPrompt = null,
  onConsumeInitialPrompt,
  onPendingApprovalCountChange,
  onBrowserPauseStateChange,
}: ChatWorkspaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [activeBackgroundJob, setActiveBackgroundJob] = useState<JobSummary | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);
  const [browserSessionId, setBrowserSessionId] = useState("default");
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const consumedPromptRef = useRef<string | null>(null);
  const skipNextDetailLoadForIdRef = useRef<string | null>(null);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setShowScrollToBottom(distanceFromBottom > 120);
  }, []);

  // Reset when user explicitly triggers a new chat
  useEffect(() => {
    if (newChatTrigger === 0) return;
    setMessages([]);
    setActiveBackgroundJob(null);
    setErrorBanner(null);
    setBrowserSessionId("default");
    setShowScrollToBottom(false);
    onBackgroundJobStatusChange?.(null);
  }, [newChatTrigger, onBackgroundJobStatusChange]);

  // Load selected conversation history when activeConversationId changes externally
  useEffect(() => {
    if (!activeConversationId) {
      return;
    }
    if (skipNextDetailLoadForIdRef.current === activeConversationId) {
      skipNextDetailLoadForIdRef.current = null;
      return;
    }

    let cancelled = false;
    async function loadDetail(convId: string) {
      setLoadingMessages(true);
      setErrorBanner(null);
      setBrowserSessionId(convId);
      setShowScrollToBottom(false);
      try {
        const res = await fetch(
          `/api/proxy/chat/conversations/${encodeURIComponent(convId)}`,
          { cache: "no-store" }
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = (await res.json()) as ConversationDetail;
        if (!cancelled) {
          setMessages(data.messages || []);
        }
      } catch (err) {
        if (!cancelled) {
          setErrorBanner(
            err instanceof Error ? err.message : "会話履歴の取得に失敗しました。"
          );
        }
      } finally {
        if (!cancelled) {
          setLoadingMessages(false);
        }
      }
    }

    void loadDetail(activeConversationId);
    return () => {
      cancelled = true;
    };
  }, [activeConversationId]);

  useEffect(() => {
    if (!showScrollToBottom) {
      scrollToBottom("smooth");
    }
  }, [messages.length, sending, showScrollToBottom, scrollToBottom]);

  const pendingApprovals = useMemo(
    () =>
      messages.filter(
        (m) => m.approval && m.approval.decision_status === "pending"
      ).length,
    [messages]
  );

  useEffect(() => {
    onPendingApprovalCountChange?.(pendingApprovals);
  }, [pendingApprovals, onPendingApprovalCountChange]);

  useEffect(() => {
    onBackgroundJobStatusChange?.(activeBackgroundJob?.status || null);
  }, [activeBackgroundJob, onBackgroundJobStatusChange]);

  // Poll active background job until completion
  useEffect(() => {
    if (
      !activeBackgroundJob ||
      activeBackgroundJob.status === "completed" ||
      activeBackgroundJob.status === "failed"
    ) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const res = await fetch(
          `/api/proxy/jobs/${encodeURIComponent(activeBackgroundJob.job_id)}`,
          { cache: "no-store" }
        );
        if (!res.ok) {
          return;
        }
        const job = (await res.json()) as JobSummary;
        setActiveBackgroundJob(job);

        if (job.status === "completed" || job.status === "failed") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === `bg-${job.job_id}`
                ? {
                    ...m,
                    is_streaming: false,
                    content:
                      job.status === "completed"
                        ? job.result_summary || "バックグラウンド処理が完了しました。"
                        : `バックグラウンドジョブでエラーが発生しました: ${
                            job.error_message || "unknown error"
                          }`,
                    citations: job.latest_citations || [],
                  }
                : m
            )
          );
          onConversationsUpdated();
        }
      } catch {
        // Ignore polling jitter
      }
    }, 2500);

    return () => {
      window.clearInterval(timer);
    };
  }, [activeBackgroundJob, onConversationsUpdated]);

  const handleApprovalUpdate = useCallback(
    (messageId: string, updated: ApprovalCardData) => {
      setMessages((prev) =>
        prev.map((msg) => (msg.id === messageId ? { ...msg, approval: updated } : msg))
      );
    },
    []
  );

  const handleSend = useCallback(
    async (text: string, attachments: string[]) => {
      setErrorBanner(null);
      setSending(true);
      setShowScrollToBottom(false);

      const userMsg: ChatMessage = {
        id: createTempId("user"),
        role: "user",
        content: text,
        attachments,
        created_at: new Date().toISOString(),
      };

      const assistantMsgId = createTempId("assistant");
      const assistantPlaceholder: ChatMessage = {
        id: assistantMsgId,
        role: "assistant",
        content: "",
        thinking_summary: "",
        tool_calls: [],
        citations: [],
        is_streaming: !backgroundMode,
        created_at: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, userMsg, assistantPlaceholder]);

      try {
        const res = await fetch("/api/proxy/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message: text,
            conversation_id: activeConversationId,
            thinking_level: thinkingLevel,
            mode: backgroundMode ? "background" : "sse",
            attachments,
          }),
        });

        if (!res.ok) {
          const detailText = await res.text();
          throw new Error(detailText || `HTTP ${res.status}`);
        }

        const contentType = res.headers.get("content-type") || "";

        if (backgroundMode || !contentType.includes("text/event-stream")) {
          const bgData = (await res.json()) as {
            job_id: string;
            conversation_id: string;
            status: JobSummary["status"];
          };
          if (bgData.conversation_id) {
            skipNextDetailLoadForIdRef.current = bgData.conversation_id;
            onConversationChange(bgData.conversation_id);
            setBrowserSessionId(bgData.conversation_id);
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgId
                ? {
                    ...m,
                    id: `bg-${bgData.job_id}`,
                    content: `バックグラウンドジョブ \`${bgData.job_id}\` をキューに投入しました。完了まで自動でステータスを監視します…`,
                    is_streaming: true,
                  }
                : m
            )
          );
          setActiveBackgroundJob({
            job_id: bgData.job_id,
            conversation_id: bgData.conversation_id,
            status: bgData.status || "queued",
            thinking_level: thinkingLevel,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          });
          onConversationsUpdated();
          return;
        }

        await consumeSSEStream(res, (event: SSEEvent) => {
          switch (event.event) {
            case "metadata": {
              if (event.data.conversation_id) {
                skipNextDetailLoadForIdRef.current = event.data.conversation_id;
                onConversationChange(event.data.conversation_id);
                setBrowserSessionId(event.data.conversation_id);
              }
              break;
            }
            case "thinking": {
              const snippet =
                event.data.summary || event.data.text || event.data.stage || "";
              if (!snippet) break;
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? {
                        ...m,
                        thinking_summary: m.thinking_summary
                          ? `${m.thinking_summary}\n${snippet}`
                          : snippet,
                      }
                    : m
                )
              );
              break;
            }
            case "tool_call": {
              if (event.data.tool_name?.startsWith("browser_")) {
                onChangeShowBrowserPane(true);
              }
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== assistantMsgId) return m;
                  const existing = m.tool_calls || [];
                  const idx = existing.findIndex(
                    (tc) => tc.tool_name === event.data.tool_name
                  );
                  const nextTools =
                    idx >= 0
                      ? existing.map((tc, i) => (i === idx ? event.data : tc))
                      : [...existing, event.data];
                  return { ...m, tool_calls: nextTools };
                })
              );
              break;
            }
            case "delta": {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? { ...m, content: `${m.content}${event.data.text || ""}` }
                    : m
                )
              );
              break;
            }
            case "approval_required": {
              const cardData: ApprovalCardData = {
                ...event.data,
                decision_status: "pending",
              };
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId ? { ...m, approval: cardData } : m
                )
              );
              break;
            }
            case "done": {
              if (event.data.conversation_id) {
                skipNextDetailLoadForIdRef.current = event.data.conversation_id;
                onConversationChange(event.data.conversation_id);
                setBrowserSessionId(event.data.conversation_id);
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? {
                        ...m,
                        content: m.content.trim()
                          ? m.content
                          : event.data.answer || "",
                        citations: event.data.citations || [],
                        is_streaming: false,
                      }
                    : m
                )
              );
              onConversationsUpdated();
              break;
            }
            case "error": {
              const errMsg =
                event.data.message || "ストリーム処理中にエラーが発生しました。";
              setErrorBanner(errMsg);
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantMsgId
                    ? {
                        ...m,
                        content: m.content
                          ? `${m.content}\n\n> エラー: ${errMsg}`
                          : `エラーが発生しました: ${errMsg}`,
                        is_streaming: false,
                      }
                    : m
                )
              );
              break;
            }
          }
        });
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "メッセージ送信に失敗しました。";
        setErrorBanner(message);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: `送信エラー: ${message}`,
                  is_streaming: false,
                }
              : m
          )
        );
      } finally {
        setSending(false);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantMsgId && !backgroundMode
              ? { ...m, is_streaming: false }
              : m
          )
        );
      }
    },
    [
      activeConversationId,
      backgroundMode,
      onChangeShowBrowserPane,
      onConversationChange,
      onConversationsUpdated,
      thinkingLevel,
    ]
  );

  // Trigger handoff prompt passed from CommandDashboard
  useEffect(() => {
    if (
      initialPrompt &&
      initialPrompt.trim() &&
      consumedPromptRef.current !== initialPrompt &&
      !sending
    ) {
      consumedPromptRef.current = initialPrompt;
      onConsumeInitialPrompt?.();
      void handleSend(initialPrompt, []);
    }
  }, [initialPrompt, handleSend, onConsumeInitialPrompt, sending]);

  return (
    <div className="relative flex h-full w-full overflow-hidden">
      {/* Center Chat Area */}
      <div className="relative flex min-w-0 flex-1 flex-col bg-slate-50/40 dark:bg-slate-950">
        {errorBanner ? (
          <div className="mx-3 mt-2.5 flex shrink-0 items-center justify-between rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2 text-xs text-rose-700 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-200 sm:mx-4">
            <span>{errorBanner}</span>
            <button
              type="button"
              onClick={() => setErrorBanner(null)}
              aria-label="エラーを閉じる"
              className="rounded p-1 hover:bg-rose-100 dark:hover:bg-rose-900/40"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : null}

        {/* Messages Scroll Wrapper + Floating Scroll-To-Bottom Button */}
        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollContainerRef}
            onScroll={handleMessagesScroll}
            data-testid="chat-scroll-container"
            className="h-full overflow-y-auto px-3 py-4 sm:px-5 sm:py-5"
          >
            {loadingMessages ? (
              <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500 dark:text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
                会話を読み込み中...
              </div>
            ) : messages.length === 0 ? (
              <div className="mx-auto flex max-w-2xl flex-col items-center justify-center py-8 text-center sm:py-12">
                <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-600 ring-1 ring-emerald-500/30 dark:text-emerald-400">
                  <Sparkles className="h-6 w-6" />
                </div>
                <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  Gemuse Personal Orchestrator
                </h3>
                <p className="mt-1.5 max-w-md text-xs leading-relaxed text-slate-500 dark:text-slate-400">
                  3層メモリ・MCPツール・隔離ブラウザを統合したパーソナルAIエージェント。
                  外部送信や決裁にはワンタイム承認カードが作動します。
                </p>

                <div className="mt-6 grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {SUGGESTION_CARDS.map((card) => {
                    const Icon = card.icon;
                    return (
                      <button
                        key={card.title}
                        type="button"
                        onClick={() => void handleSend(card.prompt, [])}
                        className="group flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-3.5 text-left transition-all hover:border-emerald-400 hover:shadow-sm dark:border-slate-800 dark:bg-slate-900/70 dark:hover:border-emerald-500/60"
                      >
                        <div className="mt-0.5 rounded-lg bg-emerald-50 p-2 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
                          <Icon className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <div className="text-xs font-semibold text-slate-800 group-hover:text-emerald-600 dark:text-slate-100 dark:group-hover:text-emerald-400">
                            {card.title}
                          </div>
                          <div className="mt-0.5 line-clamp-2 text-[11px] text-slate-500 dark:text-slate-400">
                            {card.prompt}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((msg) => (
                  <MessageBubble
                    key={msg.id}
                    message={msg}
                    onApprovalUpdate={handleApprovalUpdate}
                  />
                ))}
                <div ref={messagesEndRef} />
              </div>
            )}
          </div>

          {showScrollToBottom ? (
            <button
              type="button"
              onClick={() => {
                setShowScrollToBottom(false);
                scrollToBottom("smooth");
              }}
              aria-label="最新へスクロール"
              title="最新メッセージへ移動"
              className="absolute bottom-3 right-4 z-20 flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white/95 text-slate-700 shadow-lg backdrop-blur-sm transition hover:border-emerald-500 hover:text-emerald-600 dark:border-slate-700 dark:bg-slate-900/95 dark:text-slate-200 dark:hover:border-emerald-500 dark:hover:text-emerald-400"
            >
              <ArrowDown className="h-4 w-4" strokeWidth={2} />
            </button>
          ) : null}
        </div>

        {/* Slim Composer */}
        <ChatComposer sending={sending} onSend={handleSend} />
      </div>

      {/* Split Live Browser Panel */}
      {showBrowserPane ? (
        <aside className="fixed inset-0 z-40 flex flex-col bg-white dark:bg-slate-950 xl:static xl:z-auto xl:w-[460px] xl:flex-shrink-0 xl:border-l xl:border-slate-200 xl:dark:border-slate-800">
          <div className="flex items-center justify-between border-b border-slate-200 px-3 pt-[max(0.5rem,env(safe-area-inset-top))] pb-2 dark:border-slate-800 xl:hidden">
            <span className="text-xs font-semibold text-slate-800 dark:text-slate-200">
              Live Browser Split View
            </span>
            <button
              type="button"
              onClick={() => onChangeShowBrowserPane(false)}
              className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              aria-label="ブラウザパネルを閉じる"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <LiveBrowserPanel
              sessionId={browserSessionId}
              onSessionIdChange={setBrowserSessionId}
              onPauseStateChange={onBrowserPauseStateChange}
            />
          </div>
        </aside>
      ) : null}
    </div>
  );
}
