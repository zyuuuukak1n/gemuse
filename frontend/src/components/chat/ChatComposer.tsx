"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { ArrowUp, Loader2, Paperclip, Plus, UploadCloud, X } from "lucide-react";

interface ChatComposerProps {
  sending: boolean;
  onSend: (message: string, attachments: string[]) => Promise<void>;
}

export function ChatComposer({ sending, onSend }: ChatComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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

  function adjustTextareaHeight(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    const currentAttachments = [...attachments];
    setText("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    await onSend(trimmed, currentAttachments);
  }

  return (
    <div
      data-testid="chat-composer"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`shrink-0 border-t border-slate-200/80 bg-white/95 px-3 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur-sm transition dark:border-emerald-950/60 dark:bg-[#0d1310]/95 sm:px-4 ${
        dragging ? "ring-2 ring-inset ring-emerald-500" : ""
      }`}
    >
      <div className="mx-auto max-w-3xl">
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
          <div className="mb-2 flex items-center justify-center gap-2 rounded-xl border border-dashed border-emerald-500 bg-emerald-500/10 py-2.5 text-xs text-emerald-600 dark:text-emerald-300">
            <UploadCloud className="h-4 w-4" strokeWidth={1.75} />
            <span>ドロップして /shared_files/uploads へ添付</span>
          </div>
        ) : null}

        <form
          onSubmit={handleSubmit}
          className="flex items-end gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-2 py-1.5 transition focus-within:border-emerald-500/60 focus-within:ring-2 focus-within:ring-emerald-500/15 dark:border-slate-800 dark:bg-slate-900/90"
        >
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
            aria-label="ファイルを添付"
            title="画像 / PDF / 音声ファイルを添付"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-slate-600 transition hover:bg-slate-200/70 hover:text-emerald-600 disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
          >
            {uploading ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
            ) : (
              <Plus className="h-5 w-5" strokeWidth={1.75} />
            )}
          </button>

          <textarea
            ref={textareaRef}
            rows={1}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              adjustTextareaHeight(e.target);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSubmit(e);
              }
            }}
            placeholder="Gemuseにメッセージを入力..."
            className="max-h-36 min-h-9 flex-1 resize-none bg-transparent py-1.5 text-sm leading-5 text-slate-900 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
          />

          <button
            type="submit"
            disabled={sending || !text.trim()}
            aria-label="送信"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending ? (
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
            ) : (
              <ArrowUp className="h-4 w-4" strokeWidth={2} />
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
