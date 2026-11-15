"use client";

import { useMemo, useState } from "react";
import {
  BrainCircuit,
  Globe,
  LayoutDashboard,
  LogOut,
  MessageSquare,
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
import type { ConversationSummary, WorkspaceTab } from "@/types/gemuse";

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
  loading: boolean;
  pendingApprovalCount: number;
  browserPaused: boolean;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onTogglePin: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onRefresh: () => void;
  onCloseDrawer: () => void;
  onLogout: () => void;
}

export function ConversationSidebar({
  activeTab,
  onSelectTab,
  conversations,
  activeConversationId,
  pinnedConversationIds,
  loading,
  pendingApprovalCount,
  browserPaused,
  onSelectConversation,
  onNewConversation,
  onTogglePin,
  onDeleteConversation,
  onRefresh,
  onCloseDrawer,
  onLogout,
}: ConversationSidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearchInput, setShowSearchInput] = useState(false);

  const pinnedSet = useMemo(() => new Set(pinnedConversationIds), [pinnedConversationIds]);

  const filteredConversations = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const list = q
      ? conversations.filter((c) => c.title.toLowerCase().includes(q))
      : conversations;
    return [...list].sort((a, b) => {
      const aPinned = pinnedSet.has(a.id) ? 1 : 0;
      const bPinned = pinnedSet.has(b.id) ? 1 : 0;
      if (aPinned !== bPinned) return bPinned - aPinned;
      return 0;
    });
  }, [conversations, pinnedSet, searchQuery]);

  function handleLogoutClick() {
    if (typeof window !== "undefined" && !window.confirm("Gemuse からログアウトしますか？")) {
      return;
    }
    onLogout();
  }

  return (
    <div className="flex h-full w-full flex-col bg-white dark:bg-[#0d1310]">
      {/* Drawer Header */}
      <div className="shrink-0 border-b border-slate-200/80 px-3 pt-[max(0.75rem,env(safe-area-inset-top))] pb-3 dark:border-emerald-950/60">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30">
              <Sparkles className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-sm font-semibold tracking-tight text-slate-900 dark:text-white">
                gemuse
              </div>
              <div className="text-[10px] text-emerald-600 dark:text-emerald-400">
                Gemini × Muse
              </div>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setShowSearchInput((v) => !v)}
              aria-label="会話を検索"
              aria-expanded={showSearchInput}
              className={`rounded-lg p-1.5 transition ${
                showSearchInput
                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                  : "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
              }`}
            >
              <Search className="h-4 w-4" strokeWidth={1.75} />
            </button>
            <button
              type="button"
              onClick={onRefresh}
              aria-label="履歴を更新"
              className="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-100 hover:text-emerald-500 dark:text-slate-400 dark:hover:bg-slate-800"
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
              className="rounded-lg p-1.5 text-slate-500 transition hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
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
              className="w-full rounded-xl border border-slate-200 bg-slate-50 py-1.5 pl-8 pr-3 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
            />
          </div>
        ) : null}

        {/* Prominent New Chat CTA */}
        <button
          type="button"
          onClick={onNewConversation}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-3 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-500 active:scale-[0.99]"
        >
          <Plus className="h-4 w-4" strokeWidth={2} />
          <span>＋ 新しいチャット</span>
        </button>
      </div>

      {/* Scrollable Menu + History Body */}
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
                onClick={() => onSelectTab(item.id)}
                className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-xs font-medium transition ${
                  active
                    ? "bg-emerald-500/15 text-emerald-700 ring-1 ring-emerald-500/30 dark:text-emerald-300"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800/60"
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

        {/* Clean Gemini-style Conversation History */}
        <div className="mt-4 border-t border-slate-200/80 pt-3 dark:border-slate-800/80">
          <div className="mb-1.5 flex items-center justify-between px-2">
            <span className="text-[11px] font-semibold tracking-wide text-slate-400">
              最近のチャット
            </span>
            {searchQuery ? (
              <span className="text-[10px] text-emerald-600 dark:text-emerald-400">
                {filteredConversations.length}件
              </span>
            ) : null}
          </div>

          {filteredConversations.length === 0 ? (
            <p className="px-2 py-4 text-center text-xs text-slate-400">
              {searchQuery ? "一致するチャットはありません" : "チャット履歴はまだありません"}
            </p>
          ) : (
            <ul className="space-y-1" data-testid="conversation-history-list">
              {filteredConversations.map((conv) => {
                const active = conv.id === activeConversationId && activeTab === "chat";
                const isPinned = pinnedSet.has(conv.id);
                return (
                  <li
                    key={conv.id}
                    className={`group flex items-center gap-1 rounded-xl px-2 py-1.5 transition ${
                      active
                        ? "bg-emerald-500/15 ring-1 ring-emerald-500/30"
                        : "hover:bg-slate-100 dark:hover:bg-slate-800/60"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => onSelectConversation(conv.id)}
                      className="flex min-w-0 flex-1 items-start gap-2 py-1 text-left"
                    >
                      {isPinned ? (
                        <Pin
                          className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500"
                          strokeWidth={1.75}
                        />
                      ) : null}
                      <span className="line-clamp-2 text-xs leading-snug font-medium text-slate-800 dark:text-slate-200">
                        {conv.title}
                      </span>
                    </button>

                    <div className="flex shrink-0 items-center gap-0.5 opacity-80 transition group-hover:opacity-100 sm:opacity-60">
                      <button
                        type="button"
                        onClick={() => onTogglePin(conv.id)}
                        aria-label={isPinned ? `${conv.title}のピン留めを解除` : `${conv.title}をピン留め`}
                        title={isPinned ? "ピン留め解除" : "上部にピン留め"}
                        className={`rounded-lg p-1 transition hover:bg-slate-200 dark:hover:bg-slate-700 ${
                          isPinned
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-slate-400 hover:text-slate-700 dark:hover:text-slate-200"
                        }`}
                      >
                        {isPinned ? (
                          <PinOff className="h-3.5 w-3.5" strokeWidth={1.75} />
                        ) : (
                          <Pin className="h-3.5 w-3.5" strokeWidth={1.75} />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => onDeleteConversation(conv.id)}
                        aria-label={`${conv.title}を削除`}
                        title="履歴から削除"
                        className="rounded-lg p-1 text-slate-400 transition hover:bg-rose-500/15 hover:text-rose-500"
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      {/* Fixed Drawer Footer */}
      <div className="shrink-0 border-t border-slate-200/80 px-3 pt-2.5 pb-[max(0.75rem,env(safe-area-inset-bottom))] dark:border-slate-800">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300">
              <User className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-slate-800 dark:text-slate-100">
                gemuse Owner
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
            className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 transition hover:border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-600 dark:border-slate-800 dark:text-slate-300 dark:hover:text-rose-400"
          >
            <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
            <span>ログアウト</span>
          </button>
        </div>
      </div>
    </div>
  );
}
