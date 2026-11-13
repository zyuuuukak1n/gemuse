"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Bell,
  Calendar,
  CheckCheck,
  Hash,
  Loader2,
  Mail,
  MessageSquarePlus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type { DashboardSummary } from "@/types/gemuse";

interface CommandDashboardProps {
  onStartChatWithPrompt: (prompt: string) => void;
  onNavigateTab?: (tab: "chat" | "browser" | "memory" | "settings") => void;
}

export function CommandDashboard({
  onStartChatWithPrompt,
  onNavigateTab,
}: CommandDashboardProps) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async (silent = false) => {
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError(null);
    try {
      const res = await fetch("/api/proxy/dashboard/summary", {
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as DashboardSummary;
      setSummary(data);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "ダッシュボード情報の取得に失敗しました。"
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary(false);
  }, [loadSummary]);

  async function handleMarkRead(notificationId: string) {
    try {
      const res = await fetch(
        `/api/proxy/proactive/notifications/${encodeURIComponent(
          notificationId
        )}/read`,
        { method: "POST" }
      );
      if (res.ok && summary) {
        setSummary({
          ...summary,
          notifications: summary.notifications.map((n) =>
            n.id === notificationId ? { ...n, is_read: true } : n
          ),
        });
      }
    } catch {
      // Ignore transient errors
    }
  }

  if (loading && !summary) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-slate-500 dark:text-slate-400">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        コマンドダッシュボードを構築中...
      </div>
    );
  }

  const unreadNotifications =
    summary?.notifications.filter((n) => !n.is_read).length ?? 0;
  const activeJobsCount =
    summary?.jobs.filter(
      (j) =>
        j.status === "queued" ||
        j.status === "running" ||
        j.status === "waiting_approval"
    ).length ?? 0;
  const configuredConnectors =
    summary?.connectors.filter((c) => c.configured).length ?? 0;

  return (
    <div className="h-full overflow-y-auto px-4 py-5 md:px-6">
      <div className="mx-auto max-w-6xl space-y-6">
        {/* Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              Command Dashboard
            </h1>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Discord・メール（Outlook / iCloud / Gmail）・カレンダー・バックグラウンドタスクの統合ビュー
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() =>
                onStartChatWithPrompt(
                  "現在の未読通知、メール、直近の予定、進行中タスクを踏まえた今すぐ対応すべきブリーフィングを作成して。"
                )
              }
              className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-emerald-500"
            >
              <Sparkles className="h-3.5 w-3.5" />
              AIブリーフィング生成
            </button>
            <button
              type="button"
              onClick={() => void loadSummary(true)}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
              />
              更新
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-700 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-200">
            サマリー取得エラー: {error}
          </div>
        )}

        {/* KPI Cards */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>承認待ち</span>
              <ShieldAlert className="h-4 w-4 text-amber-500" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">
              {summary?.pending_approvals.length ?? 0}
            </div>
            <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              安全ゲート保留アクション
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>未読通知</span>
              <Bell className="h-4 w-4 text-emerald-500" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">
              {unreadNotifications}
            </div>
            <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              Proactiveアラート
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>稼働中ジョブ</span>
              <Activity className="h-4 w-4 text-sky-500" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">
              {activeJobsCount}
            </div>
            <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
              直近全{summary?.jobs.length ?? 0}件中
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>有効コネクタ</span>
              <Hash className="h-4 w-4 text-emerald-500" />
            </div>
            <div className="mt-2 text-2xl font-bold text-slate-900 dark:text-slate-100">
              {configuredConnectors} / {summary?.connectors.length ?? 0}
            </div>
            <button
              type="button"
              onClick={() => onNavigateTab?.("settings")}
              className="mt-1 text-[11px] font-medium text-emerald-600 hover:underline dark:text-emerald-400"
            >
              コネクタ設定を確認 →
            </button>
          </div>
        </div>

        {/* Main 2-column grid */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          {/* Emails Card */}
          <section className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Mail className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  受信メール（Outlook / iCloud / Gmail）
                </h2>
              </div>
              <button
                type="button"
                onClick={() =>
                  onStartChatWithPrompt(
                    "接続済みメールボックス（Outlook / iCloud / Gmail）の最新重要メールを一覧化し、返信案を提案してください。"
                  )
                }
                className="text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400"
              >
                一括整理
              </button>
            </div>

            {!summary?.emails || summary.emails.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                取得されたメールサマリーはありません（設定タブでOAuth/IMAP接続状態を確認してください）。
              </div>
            ) : (
              <div className="space-y-2.5">
                {summary.emails.map((mail) => (
                  <div
                    key={`${mail.provider}-${mail.message_id}`}
                    className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-slate-950/60"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="rounded-md bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                        {mail.provider}
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          onStartChatWithPrompt(
                            `以下のメール（${mail.provider} / 送信元: ${mail.sender} / 件名: 「${mail.subject}」）の内容を確認し、対応方針と返信下書きを作成して。承認前は送信しないでください。`
                          )
                        }
                        className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-950/50"
                      >
                        <MessageSquarePlus className="h-3 w-3" />
                        チャットで対応
                      </button>
                    </div>
                    <div className="mt-1 truncate text-xs font-semibold text-slate-900 dark:text-slate-100">
                      {mail.subject || "(件名なし)"}
                    </div>
                    <div className="truncate text-[11px] text-slate-500 dark:text-slate-400">
                      {mail.sender}
                    </div>
                    {mail.snippet && (
                      <p className="mt-1 line-clamp-2 text-[11px] text-slate-600 dark:text-slate-300">
                        {mail.snippet}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Calendar & Deadlines Card */}
          <section className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Calendar className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Googleカレンダー・直近スケジュール
                </h2>
              </div>
              <button
                type="button"
                onClick={() =>
                  onStartChatWithPrompt(
                    "直近のカレンダー予定と空き時間を分析して、作業ブロックの提案をしてください。"
                  )
                }
                className="text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400"
              >
                空き時間分析
              </button>
            </div>

            {!summary?.calendar_events || summary.calendar_events.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                直近の予定データは取得されていません（Google Workspace連携を有効化すると表示されます）。
              </div>
            ) : (
              <div className="space-y-2.5">
                {summary.calendar_events.map((evt) => (
                  <div
                    key={evt.id}
                    className="flex items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-slate-950/60"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-xs font-semibold text-slate-900 dark:text-slate-100">
                        {evt.summary}
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        {evt.start} {evt.end ? `→ ${evt.end}` : ""}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() =>
                        onStartChatWithPrompt(
                          `予定「${evt.summary}」（${evt.start}）の事前準備チェックリストを作成してください。`
                        )
                      }
                      className="flex-shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-700 hover:border-emerald-400 hover:text-emerald-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-emerald-500"
                    >
                      準備する
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Discord Multi-Server Card */}
          <section className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Hash className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Discord 複数サーバー監視状況
                </h2>
              </div>
              <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                既定: {summary?.discord.default_policy || "disabled"}
              </span>
            </div>

            {!summary?.discord.servers || summary.discord.servers.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                `discord_permissions.yaml` に登録されたサーバーがありません。
              </div>
            ) : (
              <div className="space-y-2">
                {summary.discord.servers.map((srv) => (
                  <div
                    key={srv.guild_id}
                    className="flex items-center justify-between gap-2 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-slate-950/60"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="truncate text-xs font-semibold text-slate-900 dark:text-slate-100">
                          {srv.name || srv.guild_id}
                        </span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            srv.permission === "read_write"
                              ? "bg-amber-500/15 text-amber-600 dark:text-amber-300"
                              : srv.permission === "read_only"
                              ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300"
                              : "bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                          }`}
                        >
                          {srv.permission}
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        Guild ID: {srv.guild_id}
                      </div>
                    </div>
                    {srv.permission !== "disabled" && (
                      <button
                        type="button"
                        onClick={() =>
                          onStartChatWithPrompt(
                            `Discordサーバー「${srv.name || srv.guild_id}」(guild_id=${srv.guild_id})の直近メッセージを取得して要点をまとめてください。`
                          )
                        }
                        className="flex-shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-emerald-600 hover:bg-emerald-50 dark:border-slate-700 dark:bg-slate-900 dark:text-emerald-400 dark:hover:bg-emerald-950/40"
                      >
                        要約する
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Proactive Notifications & Recent Jobs Card */}
          <section className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Bell className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  先回りアラート & バックグラウンドジョブ
                </h2>
              </div>
            </div>

            <div className="space-y-3">
              {summary?.notifications && summary.notifications.length > 0 && (
                <div className="space-y-2">
                  {summary.notifications.slice(0, 4).map((note) => (
                    <div
                      key={note.id}
                      className={`rounded-xl border p-3 text-xs ${
                        note.is_read
                          ? "border-slate-200/80 bg-slate-50/50 text-slate-600 dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-400"
                          : "border-emerald-300 bg-emerald-50/50 text-slate-900 dark:border-emerald-500/40 dark:bg-emerald-950/20 dark:text-slate-100"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold">{note.title}</span>
                        <div className="flex items-center gap-1.5">
                          <span className="rounded bg-slate-200/80 px-1.5 py-0.5 text-[10px] uppercase dark:bg-slate-800">
                            {note.priority}
                          </span>
                          {!note.is_read && (
                            <button
                              type="button"
                              onClick={() => void handleMarkRead(note.id)}
                              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 hover:bg-emerald-100 dark:text-emerald-400 dark:hover:bg-emerald-900/40"
                            >
                              <CheckCheck className="h-3 w-3" />
                              既読
                            </button>
                          )}
                        </div>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[11px] opacity-90">
                        {note.body}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              <div className="space-y-1.5">
                <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                  直近ジョブ実行履歴
                </div>
                {!summary?.jobs || summary.jobs.length === 0 ? (
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    ジョブ実行履歴はまだありません。
                  </p>
                ) : (
                  summary.jobs.slice(0, 5).map((job) => (
                    <div
                      key={job.job_id}
                      className="flex items-center justify-between rounded-lg border border-slate-200/70 px-3 py-2 text-xs dark:border-slate-800"
                    >
                      <div className="min-w-0 pr-2">
                        <div className="truncate font-medium text-slate-800 dark:text-slate-200">
                          {job.prompt_preview || job.job_id}
                        </div>
                        <div className="text-[10px] text-slate-400">
                          Level: {job.thinking_level}
                        </div>
                      </div>
                      <span
                        className={`flex-shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold ${
                          job.status === "completed"
                            ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300"
                            : job.status === "failed"
                            ? "bg-rose-500/15 text-rose-600 dark:text-rose-300"
                            : "bg-amber-500/15 text-amber-600 dark:text-amber-300"
                        }`}
                      >
                        {job.status}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

