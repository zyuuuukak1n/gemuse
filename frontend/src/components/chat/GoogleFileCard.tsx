"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Eye,
  EyeOff,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  HardDrive,
  Presentation,
} from "lucide-react";

export type GoogleFileKind = "docs" | "sheets" | "slides" | "drive";

export interface ParsedGoogleWorkspaceUrl {
  kind: GoogleFileKind;
  fileId: string;
  openUrl: string;
  previewUrl: string | null;
  serviceLabel: string;
  isFolder: boolean;
}

export interface ExtractedGoogleFileLink {
  url: string;
  title: string;
  accountLabel?: string;
}

const DOCS_URL_RE =
  /^https:\/\/docs\.google\.com\/(document|spreadsheets|presentation)(?:\/u\/\d+)?\/d\/([a-zA-Z0-9_-]+)/;
const DRIVE_FILE_URL_RE =
  /^https:\/\/drive\.google\.com\/file(?:\/u\/\d+)?\/d\/([a-zA-Z0-9_-]+)/;
const DRIVE_FOLDER_URL_RE =
  /^https:\/\/drive\.google\.com\/drive(?:\/u\/\d+)?\/folders\/([a-zA-Z0-9_-]+)/;
const DRIVE_OPEN_URL_RE =
  /^https:\/\/drive\.google\.com\/open\?(?:.*&)?id=([a-zA-Z0-9_-]+)/;

export function parseGoogleWorkspaceUrl(rawUrl: string): ParsedGoogleWorkspaceUrl | null {
  const url = rawUrl.trim();
  const docsMatch = DOCS_URL_RE.exec(url);
  if (docsMatch) {
    const subType = docsMatch[1];
    const fileId = docsMatch[2];
    const kindMap: Record<string, { kind: GoogleFileKind; label: string }> = {
      document: { kind: "docs", label: "Google Docs" },
      spreadsheets: { kind: "sheets", label: "Google Sheets" },
      presentation: { kind: "slides", label: "Google Slides" },
    };
    const meta = kindMap[subType];
    return {
      kind: meta.kind,
      fileId,
      openUrl: url,
      previewUrl: `https://docs.google.com/${subType}/d/${fileId}/preview`,
      serviceLabel: meta.label,
      isFolder: false,
    };
  }

  const driveFileMatch = DRIVE_FILE_URL_RE.exec(url) || DRIVE_OPEN_URL_RE.exec(url);
  if (driveFileMatch) {
    const fileId = driveFileMatch[1];
    return {
      kind: "drive",
      fileId,
      openUrl: url,
      previewUrl: `https://drive.google.com/file/d/${fileId}/preview`,
      serviceLabel: "Google Drive",
      isFolder: false,
    };
  }

  const driveFolderMatch = DRIVE_FOLDER_URL_RE.exec(url);
  if (driveFolderMatch) {
    const fileId = driveFolderMatch[1];
    return {
      kind: "drive",
      fileId,
      openUrl: url,
      previewUrl: null,
      serviceLabel: "Google Drive フォルダ",
      isFolder: true,
    };
  }

  return null;
}

const MARKDOWN_LINK_RE = /\[([^\]]+)\]\((https:\/\/(?:docs|drive)\.google\.com\/[^\s)]+)\)/g;
const BARE_GOOGLE_URL_RE =
  /https:\/\/(?:docs\.google\.com\/(?:document|spreadsheets|presentation)|drive\.google\.com\/(?:file|drive|open))[^\s)>\]"'`]+/g;

export function extractGoogleFileLinks(markdown: string): ExtractedGoogleFileLink[] {
  const byFileKey = new Map<string, ExtractedGoogleFileLink>();

  const register = (url: string, title: string, matchIndex: number) => {
    const parsed = parseGoogleWorkspaceUrl(url);
    if (!parsed) return;
    const key = `${parsed.kind}:${parsed.fileId}`;
    const cleanTitle = title.trim() || `${parsed.serviceLabel} (${parsed.fileId})`;
    const contextSlice = markdown.slice(Math.max(0, matchIndex - 60), matchIndex + url.length + 60);
    let accountLabel: string | undefined;
    if (/\[school\]|学校|school/i.test(contextSlice)) {
      accountLabel = "school";
    } else if (/\[private\]|プライベート|private/i.test(contextSlice)) {
      accountLabel = "private";
    }
    const existing = byFileKey.get(key);
    if (!existing) {
      byFileKey.set(key, { url, title: cleanTitle, accountLabel });
    } else if (existing.title.startsWith(parsed.serviceLabel) && !cleanTitle.startsWith(parsed.serviceLabel)) {
      byFileKey.set(key, { url, title: cleanTitle, accountLabel: existing.accountLabel || accountLabel });
    }
  };

  for (const m of markdown.matchAll(MARKDOWN_LINK_RE)) {
    register(m[2], m[1], m.index ?? 0);
  }
  for (const m of markdown.matchAll(BARE_GOOGLE_URL_RE)) {
    const raw = m[0].replace(/[.,;!?]+$/, "");
    register(raw, "", m.index ?? 0);
  }

  return Array.from(byFileKey.values());
}

const STYLE_MAP: Record<
  GoogleFileKind,
  {
    border: string;
    iconBg: string;
    iconText: string;
    badge: string;
  }
> = {
  docs: {
    border: "border-blue-500/25 bg-blue-50/40 dark:border-blue-500/30 dark:bg-blue-950/20",
    iconBg: "bg-blue-500/15",
    iconText: "text-blue-600 dark:text-blue-400",
    badge: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  },
  sheets: {
    border: "border-emerald-500/25 bg-emerald-50/40 dark:border-emerald-500/30 dark:bg-emerald-950/20",
    iconBg: "bg-emerald-500/15",
    iconText: "text-emerald-600 dark:text-emerald-400",
    badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
  slides: {
    border: "border-amber-500/25 bg-amber-50/40 dark:border-amber-500/30 dark:bg-amber-950/20",
    iconBg: "bg-amber-500/15",
    iconText: "text-amber-600 dark:text-amber-400",
    badge: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  },
  drive: {
    border: "border-teal-500/25 bg-teal-50/40 dark:border-teal-500/30 dark:bg-teal-950/20",
    iconBg: "bg-teal-500/15",
    iconText: "text-teal-600 dark:text-teal-400",
    badge: "bg-teal-500/15 text-teal-700 dark:text-teal-300",
  },
};

export interface GoogleFileCardProps {
  url: string;
  title?: string;
  accountLabel?: string;
  defaultExpanded?: boolean;
}

export function GoogleFileCard({
  url,
  title,
  accountLabel,
  defaultExpanded = false,
}: GoogleFileCardProps) {
  const parsed = parseGoogleWorkspaceUrl(url);
  const [expanded, setExpanded] = useState(defaultExpanded);

  if (!parsed) {
    return null;
  }

  const theme = STYLE_MAP[parsed.kind];
  const displayTitle = title?.trim() || `${parsed.serviceLabel} (${parsed.fileId})`;

  const renderIcon = () => {
    if (parsed.kind === "docs") {
      return <FileText className="h-4 w-4" strokeWidth={1.75} />;
    }
    if (parsed.kind === "sheets") {
      return <FileSpreadsheet className="h-4 w-4" strokeWidth={1.75} />;
    }
    if (parsed.kind === "slides") {
      return <Presentation className="h-4 w-4" strokeWidth={1.75} />;
    }
    if (parsed.isFolder) {
      return <FolderOpen className="h-4 w-4" strokeWidth={1.75} />;
    }
    return <HardDrive className="h-4 w-4" strokeWidth={1.75} />;
  };

  return (
    <div
      data-testid="google-file-card"
      data-google-kind={parsed.kind}
      className={`rounded-xl border p-3 transition ${theme.border}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <div
            className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${theme.iconBg} ${theme.iconText}`}
          >
            {renderIcon()}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {displayTitle}
            </p>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${theme.badge}`}>
                {parsed.serviceLabel}
              </span>
              {accountLabel ? (
                <span className="rounded-md bg-slate-200/70 px-1.5 py-0.5 text-[10px] font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                  {accountLabel}
                </span>
              ) : null}
            </div>
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {parsed.previewUrl ? (
            <button
              type="button"
              onClick={() => setExpanded((prev) => !prev)}
              aria-expanded={expanded}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300/80 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-xs transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
            >
              {expanded ? (
                <>
                  <EyeOff className="h-3.5 w-3.5" strokeWidth={1.75} />
                  <span>プレビューを閉じる</span>
                  <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.75} />
                </>
              ) : (
                <>
                  <Eye className="h-3.5 w-3.5" strokeWidth={1.75} />
                  <span>プレビューを表示</span>
                  <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
                </>
              )}
            </button>
          ) : null}

          <a
            href={parsed.openUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-2.5 py-1.5 text-xs font-medium text-white transition hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
          >
            <span>Google で開く</span>
            <ExternalLink className="h-3.5 w-3.5" strokeWidth={1.75} />
          </a>
        </div>
      </div>

      {expanded && parsed.previewUrl ? (
        <div className="mt-3 space-y-1.5">
          <iframe
            src={parsed.previewUrl}
            title={`${displayTitle} プレビュー`}
            sandbox="allow-scripts allow-same-origin allow-popups"
            loading="lazy"
            className="h-[420px] w-full rounded-lg border border-slate-200 bg-white dark:border-slate-700"
          />
          <p className="text-[11px] text-slate-500 dark:text-slate-400">
            表示されない場合は、Google アカウントにログイン中のブラウザで「Google で開く」をご利用ください。
          </p>
        </div>
      ) : null}
    </div>
  );
}

