"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  BrainCircuit,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Globe,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  MoreHorizontal,
  Pencil,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Sparkles,
  Trash2,
  User,
  X,
} from "lucide-react";
import type { ChatFolder, ConversationSummary, WorkspaceTab } from "@/types/gemuse";
import { triggerHaptic } from "@/lib/haptics";

export const VIEW_NAV_ITEMS: Array<{
  id: WorkspaceTab;
  label: string;
  icon: typeof MessageSquare;
}> = [
  { id: "chat", label: "チャット", icon: MessageSquare },
  { id: "dashboard", label: "統合ダッシュボード", icon: LayoutDashboard },
  { id: "memory", label: "記憶・Gemini連携", icon: BrainCircuit },
  { id: "browser", label: "ブラウザ監視", icon: Globe },
  { id: "settings", label: "設定", icon: Settings },
];

interface ConversationSidebarProps {
  activeTab: WorkspaceTab;
  onSelectTab: (tab: WorkspaceTab) => void;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  pinnedConversationIds: string[];
  folders?: ChatFolder[];
  conversationFolders?: Record<string, string>;
  loading: boolean;
  pendingApprovalCount: number;
  browserPaused: boolean;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onTogglePin: (id: string) => void;
  onRenameConversation?: (id: string, newTitle: string) => Promise<void> | void;
  onDeleteConversation: (id: string) => void;
  onCreateFolder?: (name: string) => void;
  onRenameFolder?: (folderId: string, newName: string) => void;
  onDeleteFolder?: (folderId: string) => void;
  onMoveConversationToFolder?: (conversationId: string, folderId: string | null) => void;
  onRefresh: () => void;
  onCloseDrawer: () => void;
  onLogout: () => void;
}

const NOOP = () => undefined;

export function ConversationSidebar({
  activeTab,
  onSelectTab,
  conversations,
  activeConversationId,
  pinnedConversationIds,
  folders = [],
  conversationFolders = {},
  loading,
  pendingApprovalCount,
  browserPaused,
  onSelectConversation,
  onNewConversation,
  onTogglePin,
  onRenameConversation = NOOP,
  onDeleteConversation,
  onCreateFolder = NOOP,
  onRenameFolder = NOOP,
  onDeleteFolder = NOOP,
  onMoveConversationToFolder = NOOP,
  onRefresh,
  onCloseDrawer,
  onLogout,
}: ConversationSidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearchInput, setShowSearchInput] = useState(false);

  // Conversation menu & inline rename state
  const [openMenuConvId, setOpenMenuConvId] = useState<string | null>(null);
  const [editingConvId, setEditingConvId] = useState<string | null>(null);
  const [editingConvTitle, setEditingConvTitle] = useState("");

  // Folder UI states
  const [collapsedFolderIds, setCollapsedFolderIds] = useState<Record<string, boolean>>({});
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  const [editingFolderName, setEditingFolderName] = useState("");
  const [openMenuFolderId, setOpenMenuFolderId] = useState<string | null>(null);

  const sidebarRootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!openMenuConvId && !openMenuFolderId) return;

    function handleOutside(e: MouseEvent) {
      const target = e.target as HTMLElement | null;
      if (target?.closest("[data-sidebar-menu]")) return;
      setOpenMenuConvId(null);
      setOpenMenuFolderId(null);
    }

    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpenMenuConvId(null);
        setOpenMenuFolderId(null);
      }
    }

    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleKey);
    };
  }, [openMenuConvId, openMenuFolderId]);

  const pinnedSet = useMemo(() => new Set(pinnedConversationIds), [pinnedConversationIds]);
  const folderIdSet = useMemo(() => new Set(folders.map((f) => f.id)), [folders]);

  const filteredConversations = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const list = q
      ? conversations.filter((c) => c.title.toLowerCase().includes(q))
      : conversations;
    return [...list].sort((a, b) => {
      const aPinned = pinnedSet.has(a.id) ? 1 : 0;
      const bPinned = pinnedSet.has(b.id) ? 1 : 0;
      return bPinned - aPinned;
    });
  }, [conversations, pinnedSet, searchQuery]);

  const { folderGroups, uncategorizedConversations } = useMemo(() => {
    const groups: Record<string, ConversationSummary[]> = {};
    for (const folder of folders) {
      groups[folder.id] = [];
    }
    const uncategorized: ConversationSummary[] = [];

    for (const conv of filteredConversations) {
      const assignedFolderId = conversationFolders[conv.id];
      if (assignedFolderId && folderIdSet.has(assignedFolderId)) {
        groups[assignedFolderId].push(conv);
      } else {
        uncategorized.push(conv);
      }
    }

    return { folderGroups: groups, uncategorizedConversations: uncategorized };
  }, [conversationFolders, filteredConversations, folderIdSet, folders]);

  function handleLogoutClick() {
    triggerHaptic();
    if (typeof window !== "undefined" && !window.confirm("Gemuse からログアウトしますか？")) {
      return;
    }
    onLogout();
  }

  function startEditingConversation(conv: ConversationSummary) {
    setOpenMenuConvId(null);
    setEditingConvId(conv.id);
    setEditingConvTitle(conv.title);
  }

  function submitConversationRename(convId: string) {
    const trimmed = editingConvTitle.trim();
    if (!trimmed) return;
    triggerHaptic();
    void onRenameConversation(convId, trimmed);
    setEditingConvId(null);
    setEditingConvTitle("");
  }

  function handleCreateFolderSubmit() {
    const trimmed = newFolderName.trim();
    if (!trimmed) return;
    triggerHaptic();
    onCreateFolder(trimmed);
    setNewFolderName("");
    setCreatingFolder(false);
  }

  function handleRenameFolderSubmit(folderId: string) {
    const trimmed = editingFolderName.trim();
    if (!trimmed) return;
    triggerHaptic();
    onRenameFolder(folderId, trimmed);
    setEditingFolderId(null);
    setEditingFolderName("");
  }

  function renderConversationRow(conv: ConversationSummary) {
    const active = conv.id === activeConversationId && activeTab === "chat";
    const isPinned = pinnedSet.has(conv.id);
    const isEditing = editingConvId === conv.id;
    const menuOpen = openMenuConvId === conv.id;
    const assignedFolderId = conversationFolders[conv.id];
    const inFolder = Boolean(assignedFolderId && folderIdSet.has(assignedFolderId));

    if (isEditing) {
      return (
        <li
          key={conv.id}
          className="flex items-center gap-1 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-2 py-1.5"
        >
          <input
            type="text"
            value={editingConvTitle}
            onChange={(e) => setEditingConvTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitConversationRename(conv.id);
              } else if (e.key === "Escape") {
                setEditingConvId(null);
              }
            }}
            aria-label="新しいチャット名"
            autoFocus
            className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          />
          <button
            type="button"
            onClick={() => submitConversationRename(conv.id)}
            disabled={!editingConvTitle.trim()}
            aria-label="チャット名を保存"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white transition hover:bg-emerald-500 disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
          <button
            type="button"
            onClick={() => setEditingConvId(null)}
            aria-label="名前変更をキャンセル"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <X className="h-3.5 w-3.5" strokeWidth={2} />
          </button>
        </li>
      );
    }

    return (
      <li
        key={conv.id}
        className={`group relative flex items-center gap-1 rounded-xl px-2 py-1 transition ${
          active
            ? "bg-emerald-500/15 ring-1 ring-emerald-500/30"
            : "hover:bg-slate-100 dark:hover:bg-slate-800"
        }`}
      >
        <button
          type="button"
          onClick={() => {
            triggerHaptic();
            onSelectConversation(conv.id);
          }}
          className="flex min-h-[44px] min-w-0 flex-1 items-center gap-2 py-1 text-left transition-transform duration-100 active:scale-[0.98]"
        >
          {isPinned ? (
            <Pin
              className="h-3.5 w-3.5 shrink-0 text-emerald-500"
              strokeWidth={1.75}
            />
          ) : null}
          <span className="line-clamp-2 text-xs leading-snug font-medium text-slate-800 dark:text-slate-200">
            {conv.title}
          </span>
        </button>

        <div className="relative shrink-0" data-sidebar-menu>
          <button
            type="button"
            onClick={() => {
              triggerHaptic();
              setOpenMenuFolderId(null);
              setOpenMenuConvId((prev) => (prev === conv.id ? null : conv.id));
            }}
            aria-label={`${conv.title}のメニュー`}
            aria-expanded={menuOpen}
            className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-400 transition-transform duration-100 hover:bg-slate-200 hover:text-slate-700 active:scale-95 dark:hover:bg-slate-700 dark:hover:text-slate-200"
          >
            <MoreHorizontal className="h-4 w-4" strokeWidth={1.75} />
          </button>

          {menuOpen ? (
            <div
              role="menu"
              aria-label={`${conv.title}の操作`}
              className="absolute right-0 top-full z-50 mt-1 w-52 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl dark:border-slate-800 dark:bg-slate-900"
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => startEditingConversation(conv)}
                className="flex min-h-[44px] w-full items-center gap-2.5 rounded-xl px-2.5 text-left text-xs font-medium text-slate-700 transition hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                <Pencil className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.75} />
                <span>名前を変更</span>
              </button>

              <button
                type="button"
                role="menuitem"
                aria-label={isPinned ? `${conv.title}のピン留めを解除` : `${conv.title}をピン留め`}
                onClick={() => {
                  triggerHaptic();
                  onTogglePin(conv.id);
                  setOpenMenuConvId(null);
                }}
                className="flex min-h-[44px] w-full items-center gap-2.5 rounded-xl px-2.5 text-left text-xs font-medium text-slate-700 transition hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                {isPinned ? (
                  <>
                    <PinOff className="h-3.5 w-3.5 text-emerald-500" strokeWidth={1.75} />
                    <span>ピン留めを解除</span>
                  </>
                ) : (
                  <>
                    <Pin className="h-3.5 w-3.5 text-slate-400" strokeWidth={1.75} />
                    <span>上部にピン留め</span>
                  </>
                )}
              </button>

              {(folders.length > 0 || inFolder) ? (
                <div className="my-1 border-t border-slate-200 pt-1 dark:border-slate-800">
                  <div className="flex items-center gap-1.5 px-2.5 py-1 text-[10px] font-semibold text-slate-400">
                    <FolderInput className="h-3 w-3" strokeWidth={1.75} />
                    <span>フォルダへ移動</span>
                  </div>
                  {folders.map((folder) => {
                    const isCurrentFolder = assignedFolderId === folder.id;
                    return (
                      <button
                        key={folder.id}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          triggerHaptic();
                          onMoveConversationToFolder(conv.id, folder.id);
                          setOpenMenuConvId(null);
                        }}
                        className="flex min-h-[40px] w-full items-center justify-between rounded-xl px-2.5 text-left text-xs text-slate-700 transition hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                      >
                        <span className="truncate">{folder.name}</span>
                        {isCurrentFolder ? (
                          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" strokeWidth={2} />
                        ) : null}
                      </button>
                    );
                  })}
                  {inFolder ? (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        triggerHaptic();
                        onMoveConversationToFolder(conv.id, null);
                        setOpenMenuConvId(null);
                      }}
                      className="flex min-h-[40px] w-full items-center gap-2 rounded-xl px-2.5 text-left text-xs text-slate-500 transition hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
                    >
                      <span>未分類に戻す</span>
                    </button>
                  ) : null}
                </div>
              ) : null}

              <div className="mt-1 border-t border-slate-200 pt-1 dark:border-slate-800">
                <button
                  type="button"
                  role="menuitem"
                  aria-label={`${conv.title}を削除`}
                  onClick={() => {
                    triggerHaptic();
                    onDeleteConversation(conv.id);
                    setOpenMenuConvId(null);
                  }}
                  className="flex min-h-[44px] w-full items-center gap-2.5 rounded-xl px-2.5 text-left text-xs font-medium text-rose-600 transition hover:bg-rose-500/10 dark:text-rose-400"
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                  <span>削除</span>
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </li>
    );
  }

  return (
    <div
      ref={sidebarRootRef}
      className="flex h-full w-full flex-col bg-white dark:bg-[#0f172a]"
    >
      {/* Solid Drawer Header */}
      <div className="shrink-0 border-b border-slate-200 bg-white px-3 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3 dark:border-slate-800 dark:bg-[#0f172a]">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30">
              <Sparkles className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-tight text-slate-900 dark:text-white">
                Gemuse
              </div>
              <div className="text-[10px] text-emerald-600 dark:text-emerald-400">
                Gemini × Muse
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => {
                triggerHaptic();
                setShowSearchInput((v) => !v);
              }}
              aria-label="会話を検索"
              aria-expanded={showSearchInput}
              className={`flex h-11 w-11 items-center justify-center rounded-xl transition-transform duration-100 active:scale-95 ${
                showSearchInput
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              }`}
            >
              <Search className="h-4 w-4" strokeWidth={1.75} />
            </button>
            <button
              type="button"
              onClick={() => {
                triggerHaptic();
                onRefresh();
              }}
              aria-label="履歴を更新"
              className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 transition-transform duration-100 hover:bg-slate-100 hover:text-emerald-500 active:scale-95 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              <RefreshCw
                className={`h-4 w-4 ${loading ? "animate-spin" : ""}`}
                strokeWidth={1.75}
              />
            </button>
            <button
              type="button"
              onClick={onCloseDrawer}
              aria-label="ドロワーを閉じる"
              className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-500 transition-transform duration-100 hover:bg-slate-100 active:scale-95 dark:text-slate-400 dark:hover:bg-slate-800"
            >
              <X className="h-4 w-4" strokeWidth={1.75} />
            </button>
          </div>
        </div>

        {showSearchInput ? (
          <div className="relative mt-2.5">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
              strokeWidth={1.75}
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="チャット履歴をフィルタ..."
              aria-label="チャット履歴検索入力"
              className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2 pl-8 pr-3 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
            />
          </div>
        ) : null}

        {/* Prominent New Chat CTA (single plus icon, no duplicate '+') */}
        <button
          type="button"
          onClick={() => {
            triggerHaptic();
            onNewConversation();
          }}
          className="mt-3 flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-3 py-2.5 text-xs font-semibold text-white shadow-sm transition-transform duration-100 hover:bg-emerald-500 active:scale-95"
        >
          <Plus className="h-4 w-4 shrink-0" strokeWidth={2} />
          <span>新しいチャット</span>
        </button>
      </div>

      {/* Scrollable Menu + Folders + History Body */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
        {/* View Switcher Menu */}
        <nav aria-label="ビュー切替メニュー" className="space-y-0.5">
          {VIEW_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = activeTab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  triggerHaptic();
                  onSelectTab(item.id);
                }}
                className={`flex min-h-[44px] w-full items-center justify-between rounded-xl px-3 py-2 text-xs font-medium transition-transform duration-100 active:scale-95 ${
                  active
                    ? "bg-emerald-500/15 text-emerald-700 ring-1 ring-emerald-500/30 dark:text-emerald-300"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                <span className="flex items-center gap-2.5">
                  <Icon className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  <span>{item.label}</span>
                </span>
                {item.id === "chat" && pendingApprovalCount > 0 ? (
                  <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-slate-950">
                    {pendingApprovalCount}
                  </span>
                ) : null}
                {item.id === "browser" && browserPaused ? (
                  <span
                    title="Take Control 一時停止中"
                    className="h-2 w-2 rounded-full bg-amber-500 ring-2 ring-amber-500/30"
                  />
                ) : null}
              </button>
            );
          })}
        </nav>

        {/* Folders Section */}
        <div
          data-testid="folder-section"
          className="mt-4 border-t border-slate-200 pt-3 dark:border-slate-800"
        >
          <div className="mb-1.5 flex items-center justify-between px-2">
            <span className="text-[11px] font-semibold tracking-wide text-slate-400">
              フォルダ
            </span>
            <button
              type="button"
              onClick={() => {
                triggerHaptic();
                setCreatingFolder((v) => !v);
                setNewFolderName("");
              }}
              aria-label="新規フォルダを作成"
              className="inline-flex min-h-[36px] items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-emerald-600 transition-transform duration-100 hover:bg-emerald-500/10 active:scale-95 dark:text-emerald-400"
            >
              <FolderPlus className="h-3.5 w-3.5" strokeWidth={1.75} />
              <span>＋ 新規フォルダ</span>
            </button>
          </div>

          {creatingFolder ? (
            <div className="mb-2 flex items-center gap-1 rounded-xl border border-emerald-500/40 bg-emerald-500/10 px-2 py-1.5">
              <input
                type="text"
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    handleCreateFolderSubmit();
                  } else if (e.key === "Escape") {
                    setCreatingFolder(false);
                  }
                }}
                placeholder="フォルダ名（例: 就活）"
                aria-label="新規フォルダ名"
                autoFocus
                className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              <button
                type="button"
                onClick={handleCreateFolderSubmit}
                disabled={!newFolderName.trim()}
                aria-label="フォルダを保存"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white transition hover:bg-emerald-500 disabled:opacity-40"
              >
                <Check className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
              <button
                type="button"
                onClick={() => setCreatingFolder(false)}
                aria-label="フォルダ作成をキャンセル"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-slate-500 transition hover:bg-slate-200 dark:text-slate-400 dark:hover:bg-slate-800"
              >
                <X className="h-3.5 w-3.5" strokeWidth={2} />
              </button>
            </div>
          ) : null}

          {folders.length === 0 ? (
            <p className="px-2 py-1.5 text-[11px] text-slate-400">
              フォルダでチャットを整理できます
            </p>
          ) : (
            <div className="space-y-1.5" data-testid="folder-list">
              {folders.map((folder) => {
                const items = folderGroups[folder.id] || [];
                const isCollapsed = Boolean(collapsedFolderIds[folder.id]);
                const isEditingThisFolder = editingFolderId === folder.id;
                const folderMenuOpen = openMenuFolderId === folder.id;

                return (
                  <div
                    key={folder.id}
                    className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-1 dark:border-slate-800 dark:bg-slate-900/60"
                  >
                    {isEditingThisFolder ? (
                      <div className="flex items-center gap-1 px-1 py-1">
                        <input
                          type="text"
                          value={editingFolderName}
                          onChange={(e) => setEditingFolderName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              handleRenameFolderSubmit(folder.id);
                            } else if (e.key === "Escape") {
                              setEditingFolderId(null);
                            }
                          }}
                          aria-label="新しいフォルダ名"
                          autoFocus
                          className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                        />
                        <button
                          type="button"
                          onClick={() => handleRenameFolderSubmit(folder.id)}
                          disabled={!editingFolderName.trim()}
                          aria-label="フォルダ名を保存"
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white"
                        >
                          <Check className="h-3.5 w-3.5" strokeWidth={2} />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingFolderId(null)}
                          aria-label="フォルダ名変更をキャンセル"
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-500"
                        >
                          <X className="h-3.5 w-3.5" strokeWidth={2} />
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between gap-1">
                        <button
                          type="button"
                          onClick={() => {
                            triggerHaptic();
                            setCollapsedFolderIds((prev) => ({
                              ...prev,
                              [folder.id]: !prev[folder.id],
                            }));
                          }}
                          aria-expanded={!isCollapsed}
                          className="flex min-h-[44px] min-w-0 flex-1 items-center gap-1.5 rounded-lg px-2 text-left text-xs font-semibold text-slate-700 transition hover:bg-slate-200/60 dark:text-slate-200 dark:hover:bg-slate-800"
                        >
                          {isCollapsed ? (
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                          )}
                          {isCollapsed ? (
                            <Folder className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                          ) : (
                            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                          )}
                          <span className="truncate">{folder.name}</span>
                          <span className="ml-auto rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-400">
                            {items.length}
                          </span>
                        </button>

                        <div className="relative shrink-0" data-sidebar-menu>
                          <button
                            type="button"
                            onClick={() => {
                              triggerHaptic();
                              setOpenMenuConvId(null);
                              setOpenMenuFolderId((prev) =>
                                prev === folder.id ? null : folder.id
                              );
                            }}
                            aria-label={`${folder.name}フォルダのメニュー`}
                            className="flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-200 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
                          >
                            <MoreHorizontal className="h-4 w-4" strokeWidth={1.75} />
                          </button>

                          {folderMenuOpen ? (
                            <div
                              role="menu"
                              className="absolute right-0 top-full z-50 mt-1 w-44 rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl dark:border-slate-800 dark:bg-slate-900"
                            >
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  setOpenMenuFolderId(null);
                                  setEditingFolderId(folder.id);
                                  setEditingFolderName(folder.name);
                                }}
                                className="flex min-h-[40px] w-full items-center gap-2 rounded-xl px-2.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                              >
                                <Pencil className="h-3.5 w-3.5 text-slate-400" />
                                <span>名前を変更</span>
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  triggerHaptic();
                                  setOpenMenuFolderId(null);
                                  onDeleteFolder(folder.id);
                                }}
                                className="flex min-h-[40px] w-full items-center gap-2 rounded-xl px-2.5 text-left text-xs font-medium text-rose-600 hover:bg-rose-500/10 dark:text-rose-400"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                                <span>フォルダを削除</span>
                              </button>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    )}

                    {!isCollapsed ? (
                      items.length === 0 ? (
                        <p className="px-3 py-1.5 text-[11px] text-slate-400">
                          チャットはありません
                        </p>
                      ) : (
                        <ul className="mt-1 space-y-0.5 pl-2">
                          {items.map((conv) => renderConversationRow(conv))}
                        </ul>
                      )
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Uncategorized Gemini-style Conversation History */}
        <div className="mt-4 border-t border-slate-200 pt-3 dark:border-slate-800">
          <div className="mb-1.5 flex items-center justify-between px-2">
            <span className="text-[11px] font-semibold tracking-wide text-slate-400">
              {folders.length > 0 ? "未分類（最近のチャット）" : "最近のチャット"}
            </span>
            {searchQuery ? (
              <span className="text-[10px] text-emerald-600 dark:text-emerald-400">
                {filteredConversations.length}件
              </span>
            ) : null}
          </div>

          {uncategorizedConversations.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-slate-400">
              {searchQuery
                ? "一致するチャットはありません"
                : folders.length > 0
                ? "未分類のチャットはありません"
                : "チャット履歴はまだありません"}
            </p>
          ) : (
            <ul className="space-y-1" data-testid="conversation-history-list">
              {uncategorizedConversations.map((conv) => renderConversationRow(conv))}
            </ul>
          )}
        </div>
      </div>

      {/* Fixed Solid Drawer Footer */}
      <div className="shrink-0 border-t border-slate-200 bg-white px-3 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] dark:border-slate-800 dark:bg-[#0f172a]">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <User className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-slate-800 dark:text-slate-100">
                Gemuse Owner
              </div>
              <div className="flex items-center gap-1 text-[10px] text-emerald-600 dark:text-emerald-400">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span>接続中</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={handleLogoutClick}
            className="inline-flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 px-3 text-xs font-medium text-slate-600 transition-transform duration-100 hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-600 active:scale-95 dark:border-slate-800 dark:text-slate-300 dark:hover:text-rose-400"
          >
            <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
            <span>ログアウト</span>
          </button>
        </div>
      </div>
    </div>
  );
}
