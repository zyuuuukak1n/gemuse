"use client";

import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  ArrowUp,
  Loader2,
  Mic,
  MicOff,
  Paperclip,
  Plus,
  Square,
  UploadCloud,
  X,
} from "lucide-react";
import { triggerHaptic } from "@/lib/haptics";

interface SpeechRecognitionResultAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionResultAlternativeLike;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionInstanceLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort?: () => void;
}

type SpeechRecognitionConstructorLike = new () => SpeechRecognitionInstanceLike;

function getSpeechRecognitionConstructor(): SpeechRecognitionConstructorLike | null {
  if (typeof window === "undefined") return null;
  const win = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructorLike;
    webkitSpeechRecognition?: SpeechRecognitionConstructorLike;
  };
  return win.SpeechRecognition || win.webkitSpeechRecognition || null;
}

interface ChatComposerProps {
  sending: boolean;
  onSend: (message: string, attachments: string[]) => Promise<void>;
  onStop?: () => void;
}

export function ChatComposer({ sending, onSend, onStop }: ChatComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [speechSupported, setSpeechSupported] = useState(false);
  const [listening, setListening] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const recognitionRef = useRef<SpeechRecognitionInstanceLike | null>(null);
  const baseTextBeforeSpeechRef = useRef("");
  const textRef = useRef("");
  textRef.current = text;

  useEffect(() => {
    setSpeechSupported(Boolean(getSpeechRecognitionConstructor()));
    return () => {
      try {
        recognitionRef.current?.abort?.();
        recognitionRef.current?.stop();
      } catch {
        // Ignore cleanup errors
      }
      recognitionRef.current = null;
    };
  }, []);

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

  function stopVoiceRecognition() {
    try {
      recognitionRef.current?.stop();
    } catch {
      // Ignore stop errors
    }
    setListening(false);
  }

  function toggleVoiceRecognition() {
    triggerHaptic();
    if (listening) {
      stopVoiceRecognition();
      return;
    }

    const SpeechRec = getSpeechRecognitionConstructor();
    if (!SpeechRec) return;

    const recognition = new SpeechRec();
    recognition.lang = "ja-JP";
    recognition.interimResults = true;
    recognition.continuous = true;

    baseTextBeforeSpeechRef.current = textRef.current.trimEnd();

    recognition.onresult = (event) => {
      let transcript = "";
      for (let i = 0; i < event.results.length; i += 1) {
        const res = event.results[i];
        if (res?.[0]?.transcript) {
          transcript += res[0].transcript;
        }
      }
      const base = baseTextBeforeSpeechRef.current;
      const combined = base && transcript.trim() ? `${base} ${transcript}` : base || transcript;
      setText(combined);
      if (textareaRef.current) {
        requestAnimationFrame(() => {
          if (textareaRef.current) adjustTextareaHeight(textareaRef.current);
        });
      }
    };

    recognition.onerror = () => {
      setListening(false);
    };

    recognition.onend = () => {
      setListening(false);
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
      setListening(true);
    } catch {
      setListening(false);
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    if (listening) {
      stopVoiceRecognition();
    }
    triggerHaptic();
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
      className={`shrink-0 border-t border-slate-200 bg-white px-3 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] transition dark:border-slate-800 dark:bg-[#0f172a] sm:px-4 ${
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
          className="flex items-end gap-1.5 rounded-2xl border border-slate-200 bg-slate-50 px-2 py-1.5 transition focus-within:border-emerald-500/60 focus-within:ring-2 focus-within:ring-emerald-500/15 dark:border-slate-800 dark:bg-slate-900"
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
            onClick={() => {
              triggerHaptic();
              fileInputRef.current?.click();
            }}
            aria-label="ファイルを添付"
            title="画像 / PDF / 音声ファイルを添付"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-600 transition-transform duration-100 hover:bg-slate-200 hover:text-emerald-600 active:scale-95 disabled:opacity-50 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
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
            placeholder={listening ? "音声を認識中..." : "Gemuseにメッセージを入力..."}
            className="max-h-36 min-h-[44px] flex-1 resize-none bg-transparent py-2.5 text-sm leading-5 text-slate-900 outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
          />

          {speechSupported ? (
            <button
              type="button"
              onClick={toggleVoiceRecognition}
              aria-label={listening ? "音声入力を停止" : "音声入力を開始"}
              aria-pressed={listening}
              title={listening ? "音声入力を停止" : "音声入力（日本語）"}
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition-transform duration-100 active:scale-95 ${
                listening
                  ? "animate-pulse bg-rose-500/15 text-rose-600 ring-2 ring-rose-500/60 dark:text-rose-400"
                  : "text-slate-500 hover:bg-slate-200 hover:text-emerald-600 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-emerald-400"
              }`}
            >
              {listening ? (
                <MicOff className="h-4 w-4" strokeWidth={1.75} />
              ) : (
                <Mic className="h-4 w-4" strokeWidth={1.75} />
              )}
            </button>
          ) : null}

          {sending && onStop ? (
            <button
              type="button"
              onClick={() => {
                triggerHaptic();
                onStop();
              }}
              aria-label="停止"
              title="生成を停止"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-slate-900 text-white shadow-sm transition-transform duration-100 hover:bg-slate-700 active:scale-95 dark:bg-emerald-500 dark:text-slate-950 dark:hover:bg-emerald-400"
            >
              <Square className="h-4 w-4 fill-current" strokeWidth={2} />
            </button>
          ) : (
            <button
              type="submit"
              disabled={sending || !text.trim()}
              aria-label="送信"
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white shadow-sm transition-transform duration-100 hover:bg-emerald-500 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {sending ? (
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />
              ) : (
                <ArrowUp className="h-4 w-4" strokeWidth={2} />
              )}
            </button>
          )}
        </form>
      </div>
    </div>
  );
}
