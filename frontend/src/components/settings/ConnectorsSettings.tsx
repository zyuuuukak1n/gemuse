"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Bell,
  BellOff,
  CheckCircle2,
  Hash,
  Loader2,
  PhoneCall,
  Play,
  Plug,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import {
  getCurrentPushSubscriptionStatus,
  isWebPushSupported,
  subscribeWebPush,
  unsubscribeWebPush,
} from "@/lib/push";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import type {
  ApprovalCardData,
  DiscordPermissionServer,
  MCPConnectorStatus,
} from "@/types/gemuse";

export function ConnectorsSettings() {
  const [connectors, setConnectors] = useState<MCPConnectorStatus[]>([]);
  const [totalTools, setTotalTools] = useState(0);
  const [discordDefaultPolicy, setDiscordDefaultPolicy] = useState("disabled");
  const [discordServers, setDiscordServers] = useState<DiscordPermissionServer[]>([]);
  const [loading, setLoading] = useState(true);

  // Web Push
  const [pushSupported, setPushSupported] = useState(false);
  const [vapidConfigured, setVapidConfigured] = useState(false);
  const [vapidPublicKey, setVapidPublicKey] = useState("");
  const [pushSubscribed, setPushSubscribed] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushFeedback, setPushFeedback] = useState<string | null>(null);

  // Manual Proactive triggers
  const [runningBriefing, setRunningBriefing] = useState(false);
  const [runningAlerts, setRunningAlerts] = useState(false);
  const [proactiveOutput, setProactiveOutput] = useState<string | null>(null);

  // Voice Call Test (Approval-gated)
  const [phoneNumber, setPhoneNumber] = useState("");
  const [callPurpose, setCallPurpose] = useState(
    "レストラン予約の確認テスト通話です。"
  );
  const [creatingCallApproval, setCreatingCallApproval] = useState(false);
  const [callApproval, setCallApproval] = useState<ApprovalCardData | null>(null);
  const [callError, setCallError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [mcpRes, discordRes, vapidRes] = await Promise.all([
        fetch("/api/proxy/mcp/status", { cache: "no-store" }),
        fetch("/api/proxy/mcp/discord/permissions", { cache: "no-store" }),
        fetch("/api/proxy/proactive/push/vapid-public-key", { cache: "no-store" }),
      ]);

      if (mcpRes.ok) {
        const data = (await mcpRes.json()) as {
          total_tools?: number;
          connectors?: MCPConnectorStatus[];
        };
        setConnectors(data.connectors || []);
        setTotalTools(data.total_tools ?? 0);
      }

      if (discordRes.ok) {
        const data = (await discordRes.json()) as {
          default_policy?: string;
          servers?: DiscordPermissionServer[];
        };
        setDiscordDefaultPolicy(data.default_policy || "disabled");
        setDiscordServers(data.servers || []);
      }

      if (vapidRes.ok) {
        const data = (await vapidRes.json()) as {
          configured?: boolean;
          vapid_public_key?: string;
        };
        const key =
          data.vapid_public_key || process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY || "";
        setVapidConfigured(Boolean(data.configured || key));
        setVapidPublicKey(key);
      }

      setPushSupported(isWebPushSupported());
      const subscribed = await getCurrentPushSubscriptionStatus();
      setPushSubscribed(subscribed);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  async function handleToggleWebPush() {
    setPushBusy(true);
    setPushFeedback(null);
    try {
      if (pushSubscribed) {
        const ok = await unsubscribeWebPush();
        setPushSubscribed(!ok ? true : false);
        setPushFeedback(ok ? "Web Push購読を解除しました。" : "解除に失敗しました。");
      } else {
        if (!vapidPublicKey) {
          setPushFeedback("サーバー側に VAPID_PUBLIC_KEY が設定されていません。");
          return;
        }
        const ok = await subscribeWebPush(vapidPublicKey);
        setPushSubscribed(ok);
        setPushFeedback(
          ok
            ? "Web Push通知を有効化しました（バックエンドへ登録済み）。"
            : "ブラウザの通知許可が得られないか登録に失敗しました。"
        );
      }
    } finally {
      setPushBusy(false);
    }
  }

  async function handleRunBriefing() {
    setRunningBriefing(true);
    setProactiveOutput(null);
    try {
      const res = await fetch("/api/proxy/proactive/run-briefing", {
        method: "POST",
      });
      const data = (await res.json()) as Record<string, unknown>;
      setProactiveOutput(
        `モーニングブリーフィング結果: ${JSON.stringify(data, null, 2)}`
      );
    } finally {
      setRunningBriefing(false);
    }
  }

  async function handleRunAlerts() {
    setRunningAlerts(true);
    setProactiveOutput(null);
    try {
      const res = await fetch("/api/proxy/proactive/run-alerts", {
        method: "POST",
      });
      const data = (await res.json()) as Record<string, unknown>;
      setProactiveOutput(
        `イベントアラート評価結果: ${JSON.stringify(data, null, 2)}`
      );
    } finally {
      setRunningAlerts(false);
    }
  }

  async function handlePrepareVoiceCall(e: React.FormEvent) {
    e.preventDefault();
    setCallError(null);
    if (!phoneNumber.trim()) {
      setCallError("発信先電話番号（E.164形式推奨: +81...）を入力してください。");
      return;
    }
    setCreatingCallApproval(true);
    try {
      const res = await fetch("/api/proxy/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_type: "voice_make_call",
          payload: {
            to_number: phoneNumber.trim(),
            purpose: callPurpose.trim(),
            script: callPurpose.trim(),
          },
          ttl_seconds: 300,
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `HTTP ${res.status}`);
      }
      const created = (await res.json()) as {
        approval_id: string;
        action_type: string;
        expires_at: string;
        one_time_token: string;
      };
      setCallApproval({
        approval_id: created.approval_id,
        action_type: created.action_type,
        payload: {
          to_number: phoneNumber.trim(),
          purpose: callPurpose.trim(),
          script: callPurpose.trim(),
        },
        expires_at: created.expires_at,
        one_time_token: created.one_time_token,
        decision_status: "pending",
      });
    } catch (err) {
      setCallError(
        err instanceof Error ? err.message : "通話承認カードの発行に失敗しました。"
      );
    } finally {
      setCreatingCallApproval(false);
    }
  }

  return (
    <div className="h-full overflow-y-auto px-4 py-5 md:px-6">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              Connectors, Notifications & Voice Settings
            </h1>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              MCPハブ接続状態・Discord権限ポリシー・Web Push先回り通知・Twilio音声ブリッジ管理（総ツール数: {totalTools}）
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadAll()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            再読込
          </button>
        </div>

        {/* MCP Connectors Status */}
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
          <div className="mb-3 flex items-center gap-2">
            <Plug className="h-4 w-4 text-emerald-500" />
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              MCP コネクタステータス
            </h2>
          </div>

          {connectors.length === 0 ? (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              登録されたコネクタ情報がありません。
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {connectors.map((c) => (
                <div
                  key={c.connector}
                  className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 text-xs dark:border-slate-800 dark:bg-slate-950/60"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-slate-900 dark:text-slate-100">
                      {c.connector}
                    </span>
                    {c.configured ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-300">
                        <CheckCircle2 className="h-3 w-3" />
                        設定済
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                        <XCircle className="h-3 w-3" />
                        未設定
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                    Status: {c.status}
                  </div>
                  {c.tools && c.tools.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {c.tools.map((t) => (
                        <span
                          key={t}
                          className="rounded bg-slate-200/70 px-1.5 py-0.5 font-mono text-[10px] text-slate-700 dark:bg-slate-800 dark:text-slate-300"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        {/* Discord Permissions + Proactive/Web Push */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Hash className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Discord サーバー別権限ホワイトリスト
                </h2>
              </div>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                default: {discordDefaultPolicy}
              </span>
            </div>

            {discordServers.length === 0 ? (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                ホワイトリストに個別登録されたDiscordサーバーはありません。
              </p>
            ) : (
              <div className="space-y-2">
                {discordServers.map((srv) => (
                  <div
                    key={srv.guild_id}
                    className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 text-xs dark:border-slate-800 dark:bg-slate-950/60"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-900 dark:text-slate-100">
                        {srv.name || srv.guild_id}
                      </span>
                      <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-300">
                        {srv.permission}
                      </span>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">
                      Guild: {srv.guild_id}
                      {srv.allowed_channels && srv.allowed_channels.length > 0
                        ? ` • Channels: ${srv.allowed_channels.join(", ")}`
                        : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Web Push & Proactive Triggers */}
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
            <div className="mb-3 flex items-center gap-2">
              <Bell className="h-4 w-4 text-emerald-500" />
              <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                Web Push 通知 & 先回りルール手動トリガー
              </h2>
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-slate-950/60">
                <div>
                  <div className="text-xs font-semibold text-slate-900 dark:text-slate-100">
                    PWA Web Push 通知
                  </div>
                  <div className="text-[11px] text-slate-500 dark:text-slate-400">
                    VAPID設定: {vapidConfigured ? "有効" : "未設定"} • ブラウザ対応:{" "}
                    {pushSupported ? "対応" : "非対応/HTTPS必須"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void handleToggleWebPush()}
                  disabled={pushBusy || !pushSupported}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50 ${
                    pushSubscribed
                      ? "bg-slate-200 text-slate-800 hover:bg-slate-300 dark:bg-slate-800 dark:text-slate-200"
                      : "bg-emerald-600 text-white hover:bg-emerald-500"
                  }`}
                >
                  {pushSubscribed ? (
                    <>
                      <BellOff className="h-3.5 w-3.5" />
                      通知を解除
                    </>
                  ) : (
                    <>
                      <Bell className="h-3.5 w-3.5" />
                      通知を有効化
                    </>
                  )}
                </button>
              </div>

              {pushFeedback && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-300">
                  {pushFeedback}
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void handleRunBriefing()}
                  disabled={runningBriefing}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  {runningBriefing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                  モーニングブリーフィング即時実行
                </button>
                <button
                  type="button"
                  onClick={() => void handleRunAlerts()}
                  disabled={runningAlerts}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                >
                  {runningAlerts ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Play className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                  イベント監視ルール評価
                </button>
              </div>

              {proactiveOutput && (
                <pre className="max-h-40 overflow-auto rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-[11px] text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
                  {proactiveOutput}
                </pre>
              )}
            </div>
          </section>
        </div>

        {/* Outbound Voice Call Panel (Approval-Gated) */}
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
          <div className="mb-3 flex items-center gap-2">
            <PhoneCall className="h-4 w-4 text-emerald-500" />
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Twilio 音声電話アウトバウンド・テスト発信（承認ゲート必須）
            </h2>
          </div>
          <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
            Twilio資格情報が設定されている場合に架電できます。誤発信を防ぐため、即時実行ではなく先にワンタイムHMAC承認カードを発行します。
          </p>

          <form
            onSubmit={handlePrepareVoiceCall}
            className="grid grid-cols-1 gap-3 md:grid-cols-3"
          >
            <input
              type="tel"
              value={phoneNumber}
              onChange={(e) => setPhoneNumber(e.target.value)}
              placeholder="+819012345678"
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
            />
            <input
              type="text"
              value={callPurpose}
              onChange={(e) => setCallPurpose(e.target.value)}
              placeholder="発話内容・通話目的"
              className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 md:col-span-2"
            />
            <div className="md:col-span-3">
              <button
                type="submit"
                disabled={creatingCallApproval}
                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {creatingCallApproval ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ShieldCheck className="h-3.5 w-3.5" />
                )}
                通話承認カードを発行
              </button>
            </div>
          </form>

          {callError && (
            <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
              {callError}
            </p>
          )}

          {callApproval && (
            <div className="mt-4">
              <ApprovalCard
                approval={callApproval}
                onUpdated={(updated) => setCallApproval(updated)}
              />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

