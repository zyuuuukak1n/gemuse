"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  KeyRound,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import {
  GEMINI_OFFICIAL_ORIGIN,
  type GeminiImportPayload,
  type GeminiImportResult,
  GEMUSE_INGEST_TOKEN_STORAGE_KEY,
  validateGeminiImportMessage,
} from "@/lib/gemini-import";

type ImportStatus =
  | "waiting"
  | "needs_token"
  | "submitting"
  | "success"
  | "error";

export default function ImportPage() {
  const [status, setStatus] = useState<ImportStatus>("waiting");
  const [payload, setPayload] = useState<GeminiImportPayload | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [result, setResult] = useState<GeminiImportResult | null>(null);

  const hasAcceptedPayloadRef = useRef(false);
  const tokenRef = useRef("");

  const submitIngest = useCallback(
    async (targetPayload: GeminiImportPayload, rawToken: string) => {
      const cleanToken = rawToken.trim();
      if (!cleanToken) {
        setStatus("needs_token");
        setErrorMessage(
          "X-Gemuse-Ingest-Token が未設定です。トークンを入力して保存・送信してください。"
        );
        return;
      }

      setStatus("submitting");
      setErrorMessage(null);

      try {
        const res = await fetch("/api/sync/ingest-chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Gemuse-Ingest-Token": cleanToken,
          },
          body: JSON.stringify(targetPayload),
        });

        if (res.status === 401 || res.status === 403) {
          setStatus("needs_token");
          setErrorMessage(
            `認証に失敗しました (HTTP ${res.status})。正しい X-Gemuse-Ingest-Token を入力して再送してください。`
          );
          return;
        }

        const body = (await res.json().catch(() => ({}))) as Partial<GeminiImportResult> & {
          detail?: string;
        };

        if (!res.ok) {
          setStatus("error");
          setErrorMessage(
            body.detail || `取り込みエラーが発生しました (HTTP ${res.status})。`
          );
          return;
        }

        try {
          window.localStorage.setItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY, cleanToken);
        } catch {
          // Ignore storage quota / private-mode restrictions
        }
        tokenRef.current = cleanToken;

        setResult({
          episodes_saved: Number(body.episodes_saved ?? 0),
          core_updates_applied: Number(body.core_updates_applied ?? 0),
          consolidation_summary: body.consolidation_summary,
        });
        setStatus("success");
      } catch (err) {
        setStatus("error");
        setErrorMessage(
          err instanceof Error
            ? `通信に失敗しました: ${err.message}`
            : "通信に失敗しました。"
        );
      }
    },
    []
  );

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY) || "";
      if (stored) {
        tokenRef.current = stored;
        setTokenInput(stored);
      }
    } catch {
      // Ignore storage access errors
    }

    function handleMessage(event: MessageEvent) {
      const validated = validateGeminiImportMessage(event.origin, event.data);
      if (!validated) {
        return;
      }
      if (hasAcceptedPayloadRef.current) {
        return;
      }
      hasAcceptedPayloadRef.current = true;
      setPayload(validated);
      void submitIngest(validated, tokenRef.current);
    }

    window.addEventListener("message", handleMessage);

    try {
      window.opener?.postMessage(
        { type: "GEMUSE_IMPORT_READY" },
        GEMINI_OFFICIAL_ORIGIN
      );
    } catch {
      // Ignore opener postMessage error if opener is unavailable
    }

    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [submitIngest]);

  function handleTokenSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const cleanToken = tokenInput.trim();
    if (!cleanToken) {
      setErrorMessage("X-Gemuse-Ingest-Token を入力してください。");
      return;
    }
    try {
      window.localStorage.setItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY, cleanToken);
    } catch {
      // Ignore storage errors
    }
    tokenRef.current = cleanToken;
    if (payload) {
      void submitIngest(payload, cleanToken);
    } else {
      setErrorMessage(null);
      setStatus("waiting");
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10 text-slate-900 dark:bg-[#090d0b] dark:text-slate-100">
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-[#101815]">
        <div className="flex items-center gap-3 border-b border-slate-100 pb-4 dark:border-slate-800">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30">
            <Sparkles className="h-5 w-5" strokeWidth={1.75} />
          </div>
          <div>
            <h1 className="text-base font-semibold">
              公式 Gemini 会話インポート
            </h1>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              gemini.google.com からの会話データを Gemuse 記憶基盤へ統合します
            </p>
          </div>
        </div>

        <div className="mt-5 space-y-4">
          {status === "waiting" ? (
            <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/60">
              <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-emerald-500" />
              <div className="space-y-1 text-xs">
                <p className="font-semibold text-slate-800 dark:text-slate-100">
                  Gemini からのデータ送信を待機しています...
                </p>
                <p className="text-slate-500 dark:text-slate-400">
                  数秒経っても進まない場合は、gemini.google.com のタブでブックマークレットを再実行してください。
                </p>
              </div>
            </div>
          ) : null}

          {status === "submitting" ? (
            <div className="flex items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4 text-xs text-emerald-800 dark:text-emerald-200">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              <div>
                <p className="font-semibold">
                  「{payload?.title || "Gemini Chat Import"}」を取り込んでいます...
                </p>
                <p className="mt-0.5 opacity-80">
                  エピソード記憶および Core Memory の統合処理を実行中です。
                </p>
              </div>
            </div>
          ) : null}

          {status === "success" && result ? (
            <div
              data-testid="import-success"
              className="space-y-3 rounded-xl border border-emerald-500/30 bg-emerald-50/70 p-4 dark:border-emerald-500/30 dark:bg-emerald-950/25"
            >
              <div className="flex items-center gap-2 text-sm font-semibold text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                <span>Gemuse への取り込みが完了しました</span>
              </div>
              {payload?.title ? (
                <p className="text-xs font-medium text-slate-700 dark:text-slate-200">
                  タイトル: {payload.title}
                </p>
              ) : null}
              <div className="grid grid-cols-2 gap-2.5 text-center">
                <div className="rounded-lg border border-emerald-500/20 bg-white/80 p-2.5 dark:bg-slate-900/70">
                  <div className="text-lg font-bold text-emerald-600 dark:text-emerald-400">
                    {result.episodes_saved}
                  </div>
                  <div className="text-[11px] text-slate-500 dark:text-slate-400">
                    episodes_saved
                  </div>
                </div>
                <div className="rounded-lg border border-emerald-500/20 bg-white/80 p-2.5 dark:bg-slate-900/70">
                  <div className="text-lg font-bold text-emerald-600 dark:text-emerald-400">
                    {result.core_updates_applied}
                  </div>
                  <div className="text-[11px] text-slate-500 dark:text-slate-400">
                    core_updates_applied
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {errorMessage ? (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-xl border border-rose-500/30 bg-rose-50/70 p-3.5 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-950/25 dark:text-rose-300"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.75} />
              <div className="flex-1 space-y-2">
                <p>{errorMessage}</p>
                {status === "error" && payload ? (
                  <button
                    type="button"
                    onClick={() => void submitIngest(payload, tokenRef.current)}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 font-medium text-white hover:bg-rose-500"
                  >
                    <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
                    <span>再試行</span>
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}

          {status === "needs_token" ? (
            <form onSubmit={handleTokenSubmit} className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-900/50">
              <label
                htmlFor="gemuse-ingest-token"
                className="flex items-center gap-1.5 text-xs font-semibold text-slate-700 dark:text-slate-200"
              >
                <KeyRound className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                <span>X-Gemuse-Ingest-Token を入力</span>
              </label>
              <input
                id="gemuse-ingest-token"
                type="password"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="Ingest Token を入力..."
                className="w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
              />
              <button
                type="submit"
                className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-emerald-500"
              >
                <span>保存してインポートを実行</span>
              </button>
            </form>
          ) : null}

          <div className="flex items-center justify-between border-t border-slate-100 pt-4 dark:border-slate-800">
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400"
            >
              <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.75} />
              <span>Gemuse トップ画面へ戻る</span>
            </Link>
            {payload ? (
              <span className="text-[11px] text-slate-400">
                {payload.messages
                  ? `会話ターン ${payload.messages.length} 件受信済み`
                  : "本文テキスト受信済み"}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </main>
  );
}

