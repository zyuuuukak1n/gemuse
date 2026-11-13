"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Download,
  Globe,
  Hand,
  History,
  Loader2,
  OctagonX,
  Play,
  RefreshCw,
} from "lucide-react";
import type { BrowserAuditEntry, BrowserSessionStatus } from "@/types/gemuse";

interface LiveBrowserPanelProps {
  sessionId: string;
  initialStatus?: BrowserSessionStatus | null;
  onSessionIdChange?: (sessionId: string) => void;
  onPauseStateChange?: (paused: boolean) => void;
}

export function LiveBrowserPanel({
  sessionId,
  initialStatus = null,
  onSessionIdChange,
  onPauseStateChange,
}: LiveBrowserPanelProps) {
  const [status, setStatus] = useState<BrowserSessionStatus | null>(initialStatus);
  const [auditLogs, setAuditLogs] = useState<BrowserAuditEntry[]>([]);
  const [vncTicket, setVncTicket] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshState = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const [statusRes, auditRes] = await Promise.all([
        fetch(`/api/proxy/browser/sessions/${encodeURIComponent(sessionId)}/status`),
        fetch(`/api/proxy/browser/audit-logs?session_id=${encodeURIComponent(sessionId)}&limit=20`),
      ]);
      if (statusRes.ok) {
        const nextStatus = (await statusRes.json()) as BrowserSessionStatus;
        setStatus(nextStatus);
        onPauseStateChange?.(nextStatus.paused);
      }
      if (auditRes.ok) {
        const auditJson = (await auditRes.json()) as { items?: BrowserAuditEntry[] };
        setAuditLogs(auditJson.items || []);
      }
    } catch {
      // keep previous status cleanly
    } finally {
      setLoading(false);
    }
  }, [sessionId, onPauseStateChange]);

  const refreshTicket = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await fetch(
        `/api/proxy/browser/sessions/${encodeURIComponent(sessionId)}/vnc-ticket`,
        { method: "POST" }
      );
      if (res.ok) {
        const data = (await res.json()) as { ticket: string };
        setVncTicket(data.ticket);
      }
    } catch {
      // ticket optional when sandbox offline
    }
  }, [sessionId]);

  useEffect(() => {
    if (initialStatus) {
      setStatus(initialStatus);
      onPauseStateChange?.(initialStatus.paused);
      return;
    }
    void refreshState();
    void refreshTicket();
  }, [initialStatus, refreshState, refreshTicket, onPauseStateChange]);

  async function handleControlAction(action: "pause" | "resume" | "emergency-stop") {
    setActionBusy(action);
    setError(null);
    try {
      const res = await fetch(
        `/api/proxy/browser/sessions/${encodeURIComponent(sessionId)}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            action === "pause" ? { reason: "手動操作 (Take Control) へ切替" } : {}
          ),
        }
      );
      if (!res.ok) {
        throw new Error(`操作失敗 (${res.status})`);
      }
      if (action === "pause") {
        setStatus((prev) => ({
          session_id: sessionId,
          paused: true,
          pause_info: { reason: "手動操作 (Take Control) へ切替" },
          stopped: false,
          novnc_url: prev?.novnc_url || `/api/browser/vnc/${sessionId}/vnc.html`,
          downloads: prev?.downloads || [],
          url: prev?.url || "",
          title: prev?.title || "",
        }));
        onPauseStateChange?.(true);
      } else if (action === "resume") {
        setStatus((prev) => ({
          session_id: sessionId,
          paused: false,
          pause_info: null,
          stopped: false,
          novnc_url: prev?.novnc_url || `/api/browser/vnc/${sessionId}/vnc.html`,
          downloads: prev?.downloads || [],
          url: prev?.url || "",
          title: prev?.title || "",
        }));
        onPauseStateChange?.(false);
      } else {
        setStatus((prev) => ({
          session_id: sessionId,
          paused: false,
          pause_info: null,
          stopped: true,
          novnc_url: prev?.novnc_url || `/api/browser/vnc/${sessionId}/vnc.html`,
          downloads: prev?.downloads || [],
          url: prev?.url || "",
          title: prev?.title || "",
        }));
        onPauseStateChange?.(false);
      }
      await refreshState();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作に失敗しました");
    } finally {
      setActionBusy(null);
    }
  }

  const wsPath = vncTicket
    ? `api/browser/vnc/${encodeURIComponent(sessionId)}/websockify?ticket=${encodeURIComponent(vncTicket)}`
    : `api/browser/vnc/${encodeURIComponent(sessionId)}/websockify`;
  const iframeSrc = `/api/proxy/browser/vnc/${encodeURIComponent(sessionId)}/vnc.html?autoconnect=1&resize=scale&path=${encodeURIComponent(wsPath)}`;

  return (
    <section
      aria-label="ライブブラウザ監視パネル"
      className="flex h-full flex-col bg-slate-50 dark:bg-[#0b110e]"
    >
      {/* Top Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-3 py-2.5 dark:border-emerald-950/60 dark:bg-[#0f1613]">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Globe className="h-4 w-4 shrink-0 text-emerald-500" strokeWidth={1.75} />
          <input
            type="text"
            aria-label="ブラウザセッションID"
            value={sessionId}
            onChange={(e) => onSessionIdChange?.(e.target.value)}
            className="w-32 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          />
          <div className="min-w-0 flex-1 truncate rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300">
            {status?.url || "about:blank (待機中)"}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {!status?.paused ? (
            <button
              type="button"
              disabled={actionBusy !== null}
              onClick={() => handleControlAction("pause")}
              className="inline-flex items-center gap-1 rounded-lg bg-amber-500 px-2.5 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-amber-400 disabled:opacity-50"
            >
              {actionBusy === "pause" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} />
              ) : (
                <Hand className="h-3.5 w-3.5" strokeWidth={1.75} />
              )}
              手動操作に切替(Take Control)
            </button>
          ) : (
            <button
              type="button"
              disabled={actionBusy !== null}
              onClick={() => handleControlAction("resume")}
              className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-50"
            >
              {actionBusy === "resume" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} />
              ) : (
                <Play className="h-3.5 w-3.5" strokeWidth={1.75} />
              )}
              AI再開
            </button>
          )}

          <button
            type="button"
            disabled={actionBusy !== null}
            onClick={() => handleControlAction("emergency-stop")}
            className="inline-flex items-center gap-1 rounded-lg border border-rose-500/40 bg-rose-500/10 px-2.5 py-1.5 text-xs font-medium text-rose-600 transition hover:bg-rose-500/20 disabled:opacity-50 dark:text-rose-400"
          >
            <OctagonX className="h-3.5 w-3.5" strokeWidth={1.75} />
            緊急停止
          </button>

          <button
            type="button"
            onClick={() => {
              void refreshState();
              void refreshTicket();
            }}
            aria-label="ブラウザ状態更新"
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:text-emerald-500 dark:border-slate-700 dark:text-slate-400"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} strokeWidth={1.75} />
          </button>
        </div>
      </div>

      {/* Take Control Pause Banner */}
      {status?.paused ? (
        <div
          role="status"
          className="flex items-center justify-between gap-2 border-b border-amber-500/30 bg-amber-500/15 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" strokeWidth={1.75} />
            <span>
              <strong>Take Control 一時停止中:</strong>{" "}
              {status.pause_info?.reason || "ユーザー操作を待機しています"}
              {status.pause_info?.detected_price
                ? `（検出金額: ${status.pause_info.detected_price}）`
                : ""}
            </span>
          </div>
          <span className="shrink-0 text-[11px] opacity-80">操作完了後「AI再開」を押してください</span>
        </div>
      ) : null}

      {error ? (
        <div className="bg-rose-500/10 px-3 py-1.5 text-xs text-rose-500">{error}</div>
      ) : null}

      {/* noVNC Live Canvas */}
      <div className="relative min-h-[260px] flex-1 bg-slate-950">
        <iframe
          title="gemuse noVNC live browser"
          src={iframeSrc}
          className="h-full w-full border-0"
          allow="clipboard-read; clipboard-write"
        />
      </div>

      {/* Downloads & Audit Timeline Footer */}
      <div className="grid max-h-48 grid-cols-1 gap-3 overflow-y-auto border-t border-slate-200 bg-white p-3 dark:border-emerald-950/60 dark:bg-[#0f1613] lg:grid-cols-2">
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
            <Download className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
            <span>ダウンロード成果物 (/shared_files/downloads)</span>
          </div>
          {status?.downloads && status.downloads.length > 0 ? (
            <ul className="space-y-1">
              {status.downloads.map((file) => (
                <li key={file.filename}>
                  <a
                    href={`/api/proxy/browser/downloads/${encodeURIComponent(sessionId)}/${encodeURIComponent(file.filename)}`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between rounded-lg border border-slate-200 px-2.5 py-1 text-xs text-emerald-600 hover:bg-emerald-500/10 dark:border-slate-800 dark:text-emerald-400"
                  >
                    <span className="truncate">{file.filename}</span>
                    <span className="text-[10px] text-slate-400">
                      {Math.max(1, Math.round(file.size_bytes / 1024))} KB
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400">ダウンロード済みファイルはありません</p>
          )}
        </div>

        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-300">
            <History className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
            <span>閲覧・操作監査ログ</span>
          </div>
          {auditLogs.length > 0 ? (
            <ul className="space-y-1">
              {auditLogs.slice(0, 6).map((log) => (
                <li
                  key={log.id}
                  className="flex items-center justify-between gap-2 rounded-lg bg-slate-50 px-2 py-1 text-[11px] dark:bg-slate-900"
                >
                  <span className="font-medium text-emerald-600 dark:text-emerald-400">
                    {log.action}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-slate-500 dark:text-slate-400">
                    {log.target_url || "-"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400">監査履歴はまだありません</p>
          )}
        </div>
      </div>
    </section>
  );
}

