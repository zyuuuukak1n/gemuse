"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
} from "react";
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
import type { ChatAttachmentItem } from "@/types/gemuse";

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

function readFileAsBase64DataUrl(file: File): Promise<{ dataUrl: string; base64: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const commaIdx = result.indexOf(",");
      const base64 = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
      resolve({ dataUrl: result, base64 });
    };
    reader.onerror = () => reject(new Error(`ファイル読み込みに失敗 (${file.name})`));
    reader.readAsDataURL(file);
  });
}

function normalizeAttachmentFilename(file: File, index: number): string {
  const raw = (file.name || "").trim();
  if (raw && raw !== "image.png") return raw;
  const ext = file.type.split("/")[1]?.split(";")[0] || "png";
  return `pasted-image-${Date.now()}-${index}.${ext}`;
}

interface ChatComposerProps {
  sending: boolean;
  onSend: (message: string, attachments: ChatAttachmentItem[]) => Promise<void>;
  onStop?: () => void;
  droppedFiles?: File[];
  onConsumeDroppedFiles?: () => void;
}

export function ChatComposer({
  sending,
  onSend,
  onStop,
  droppedFiles,
  onConsumeDroppedFiles,
}: ChatComposerProps) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachmentItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [previewModalItem, setPreviewModalItem] = useState<ChatAttachmentItem | null>(null);
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

  useEffect(() => {
    if (!previewModalItem) return;
    function handleEsc(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setPreviewModalItem(null);
      }
    }
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [previewModalItem]);

  const processFiles = useCallback(async (files: FileList | File[]) => {
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;

    setUploading(true);
    setUploadError(null);
    try {
      const preparedItems: ChatAttachmentItem[] = [];
      for (let idx = 0; idx < fileArr.length; idx += 1) {
        const file = fileArr[idx];
        const filename = normalizeAttachmentFilename(file, idx);
        const mimeType = file.type || "application/octet-stream";
        const isMultimodal =
          mimeType.startsWith("image/") ||
          mimeType === "application/pdf" ||
          mimeType.startsWith("audio/");

        let base64 = "";
        let previewUrl = "";
        if (isMultimodal) {
          const readRes = await readFileAsBase64DataUrl(file);
          base64 = readRes.base64;
          previewUrl = mimeType.startsWith("image/") ? readRes.dataUrl : "";
        }

        let uploadedFilename: string | undefined;
        try {
          const form = new FormData();
          form.append("file", file, filename);
          const res = await fetch("/api/proxy/browser/upload", {
            method: "POST",
            body: form,
          });
          if (res.ok) {
            const data = (await res.json()) as { filename?: string };
            uploadedFilename = data.filename || filename;
          } else if (!base64) {
            throw new Error(`ファイルアップロードに失敗 (${filename})`);
          }
        } catch (err) {
          if (!base64) {
            throw err;
          }
        }

        preparedItems.push({
          id: `${filename}-${Date.now()}-${idx}-${Math.random().toString(36).slice(2, 6)}`,
          filename,
          mime_type: mimeType,
          data_base64: base64,
          preview_url: previewUrl,
          uploaded_filename: uploadedFilename,
        });
      }

      setAttachments((prev) => [...prev, ...preparedItems]);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "ファイル添付に失敗しました");
    } finally {
      setUploading(false);
    }
  }, []);

  useEffect(() => {
    if (droppedFiles && droppedFiles.length > 0) {
      onConsumeDroppedFiles?.();
      void processFiles(droppedFiles);
    }
  }, [droppedFiles, onConsumeDroppedFiles, processFiles]);

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files?.length) {
      void processFiles(e.dataTransfer.files);
    }
  }

  function handlePaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const items = e.clipboardData?.items;
    const filesFromClipboard: File[] = [];

    if (items) {
      for (let i = 0; i < items.length; i += 1) {
        const item = items[i];
        if (item && item.kind === "file" && item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) filesFromClipboard.push(file);
        }
      }
    }

    if (filesFromClipboard.length === 0 && e.clipboardData?.files?.length) {
      for (const file of Array.from(e.clipboardData.files)) {
        if (file.type.startsWith("image/")) {
          filesFromClipboard.push(file);
        }
      }
    }

    if (filesFromClipboard.length > 0) {
      e.preventDefault();
      triggerHaptic();
      void processFiles(filesFromClipboard);
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
    if ((!trimmed && attachments.length === 0) || sending) return;
    if (listening) {
      stopVoiceRecognition();
    }
    triggerHaptic();
    const payloadText = trimmed || "添付画像の内容を確認して対応してください。";
    const currentAttachments = [...attachments];
    setText("");
    setAttachments([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    await onSend(payloadText, currentAttachments);
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
        {/* Thumbnail & File Attachment Preview Strip */}
        {attachments.length > 0 ? (
          <div
            data-testid="attachment-preview-strip"
            className="mb-2 flex flex-wrap items-center gap-2"
          >
            {attachments.map((item) => {
              const isImage = item.mime_type.startsWith("image/") && Boolean(item.preview_url);
              if (isImage) {
                return (
                  <div
                    key={item.id}
                    className="group relative h-16 w-16 overflow-hidden rounded-xl border border-emerald-500/40 bg-slate-100 shadow-xs dark:bg-slate-900"
                  >
                    <button
                      type="button"
                      onClick={() => setPreviewModalItem(item)}
                      aria-label={`${item.filename}を拡大表示`}
                      className="h-full w-full focus:outline-none"
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={item.preview_url}
                        alt={item.filename}
                        className="h-full w-full object-cover transition group-hover:scale-105"
                      />
                    </button>
                    <button
                      type="button"
                      aria-label={`${item.filename}を削除`}
                      onClick={() =>
                        setAttachments((prev) => prev.filter((att) => att.id !== item.id))
                      }
                      className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-slate-950/80 text-white shadow transition hover:bg-rose-600"
                    >
                      <X className="h-3 w-3" strokeWidth={2.25} />
                    </button>
                  </div>
                );
              }
              return (
                <span
                  key={item.id}
                  className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-700 dark:text-emerald-300"
                >
                  <Paperclip className="h-3 w-3" strokeWidth={1.75} />
                  <span>{item.filename}</span>
                  <button
                    type="button"
                    aria-label={`${item.filename}を削除`}
                    onClick={() =>
                      setAttachments((prev) => prev.filter((att) => att.id !== item.id))
                    }
                    className="ml-0.5 hover:text-rose-500"
                  >
                    <X className="h-3 w-3" strokeWidth={1.75} />
                  </button>
                </span>
              );
            })}
          </div>
        ) : null}

        {uploadError ? (
          <p className="mb-2 text-xs text-rose-500">{uploadError}</p>
        ) : null}

        {dragging ? (
          <div className="mb-2 flex items-center justify-center gap-2 rounded-xl border border-dashed border-emerald-500 bg-emerald-500/10 py-2.5 text-xs text-emerald-600 dark:text-emerald-300">
            <UploadCloud className="h-4 w-4" strokeWidth={1.75} />
            <span>ドロップして画像・ファイルを添付</span>
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
              if (e.target.files) void processFiles(e.target.files);
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
            onPaste={handlePaste}
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
              disabled={sending || (!text.trim() && attachments.length === 0)}
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

      {/* Enlarged Image Preview Modal */}
      {previewModalItem ? (
        <div
          data-testid="image-preview-modal"
          role="dialog"
          aria-modal="true"
          aria-label={previewModalItem.filename}
          onClick={() => setPreviewModalItem(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="relative max-h-[85vh] max-w-3xl overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 shadow-2xl"
          >
            <button
              type="button"
              onClick={() => setPreviewModalItem(null)}
              aria-label="拡大プレビューを閉じる"
              className="absolute right-3 top-3 z-10 flex h-10 w-10 items-center justify-center rounded-full bg-slate-900/90 text-white hover:bg-slate-800"
            >
              <X className="h-5 w-5" />
            </button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={previewModalItem.preview_url}
              alt={previewModalItem.filename}
              className="max-h-[80vh] w-auto object-contain"
            />
            <div className="border-t border-slate-800 px-4 py-2 text-xs text-slate-300">
              {previewModalItem.filename}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
