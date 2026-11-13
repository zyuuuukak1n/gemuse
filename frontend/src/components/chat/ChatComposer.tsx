"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Loader2, Paperclip, Send, Sparkles, UploadCloud, X } from "lucide-react";
import type { ThinkingLevel } from "@/types/gemuse";

interface ChatComposerProps {
  thinkingLevel: ThinkingLevel;
  onChangeThinkingLevel: (level: ThinkingLevel) => void;
  backgroundMode: boolean;
  onToggleBackgroundMode: (val: boolean) => void;
  sending: boolean;
  onSend: (message: string, attachments: string[]) => Promise<void>;
}

const THINKING_PILLS: Array<{ id: ThinkingLevel; label: string }> = [
  { id: "auto", label: "Auto" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
  { id: "xhigh", label: "XHigh" },
];

export function ChatComposer({
  thinkingLevel,
  onChangeThinkingLevel,
  backgroundMode,
  onToggleBackgroundMode,
  sending,
  onSend,
}: ChatComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function uploadFiles(files: FileList | File[]) {
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;

    setUploading(true);
    setUploadError(null);
    try {
      const uploadedNames: string[] = [];
      for (const file of fileArr) {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch("/api/proxy/browser/upload", {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          throw new Error(`ファイルアップロードに失敗 (${file.name})`);
        }
        const data = (await res.json()) as { filename: string };
        uploadedNames.push(data.filename);
      }
      setAttachments((prev) => Array.from(new Set([...prev, ...uploadedNames])));
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "アップロードに失敗しました");
    } finally {
      setUploading(false);
    }
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files?.length) {
      void uploadFiles(e.dataTransfer.files);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    const currentAttachments = [...attachments];
    setText("");
    setAttachments([]);
    await onSend(trimmed, currentAttachments);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`border-t border-slate-200 bg-white/95 px-4 py-3 transition dark:border-emerald-950/60 dark:bg-[#0d1310] ${
        dragging ? "ring-2 ring-inset ring-emerald-500" : ""
      }`}
    >
      {/* Controls Row */}
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="思考レベル切替">
          <Sparkles className="mr-1 h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
          {THINKING_PILLS.map((pill) => (
            <button
              key={pill.id}
              type="button"
              onClick={() => onChangeThinkingLevel(pill.id)}
              className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition ${
                thinkingLevel === pill.id
                  ? "bg-emerald-600 text-white"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
              }`}
            >
              {pill.label}
            </button>
          ))}
        </div>

        <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-slate-600 dark:text-slate-400">
          <input
            type="checkbox"
            checked={backgroundMode}
            onChange={(e) => onToggleBackgroundMode(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-slate-400 accent-emerald-600"
          />
          <span>バックグラウンド実行</span>
        </label>
      </div>

      {/* Attachment chips */}
      {attachments.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {attachments.map((name) => (
            <span
              key={name}
              className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300"
            >
              <Paperclip className="h-3 w-3" strokeWidth={1.75} />
              <span>{name}</span>
              <button
                type="button"
                aria-label={`${name}を削除`}
                onClick={() => setAttachments((prev) => prev.filter((item) => item !== name))}
                className="ml-0.5 hover:text-rose-500"
              >
                <X className="h-3 w-3" strokeWidth={1.75} />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      {uploadError ? (
        <p className="mb-2 text-xs text-rose-500">{uploadError}</p>
      ) : null}

      {dragging ? (
        <div className="mb-2 flex items-center justify-center gap-2 rounded-xl border border-dashed border-emerald-500 bg-emerald-500/10 py-3 text-xs text-emerald-600 dark:text-emerald-300">
          <UploadCloud className="h-4 w-4" strokeWidth={1.75} />
          <span>ドロップして /shared_files/uploads へ添付</span>
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="flex items-end gap-2">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,application/pdf,audio/*,.txt,.md,.json,.csv"
          onChange={(e) => {
            if (e.target.files) void uploadFiles(e.target.files);
            e.target.value = "";
          }}
          className="hidden"
        />
        <button
          type="button"
          disabled={uploading}
          onClick={() => fileInputRef.current?.click()}
          title="画像 / PDF / 音声ファイルを添付"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-600 transition hover:border-emerald-500/50 hover:text-emerald-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300"
        >
          {uploading ? (
            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
          ) : (
            <Paperclip className="h-4 w-4" strokeWidth={1.75} />
          )}
        </button>

        <textarea
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSubmit(e);
            }
          }}
          placeholder="Gemuseに指示・質問を入力（画像/PDFのD&D添付に対応）..."
          className="max-h-36 min-h-10 flex-1 resize-y rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-900 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
        />

        <button
          type="submit"
          disabled={sending || !text.trim()}
          aria-label="送信"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-600 text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {sending ? (
            <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
          ) : (
            <Send className="h-4 w-4" strokeWidth={1.75} />
          )}
        </button>
      </form>
    </div>
  );
}

