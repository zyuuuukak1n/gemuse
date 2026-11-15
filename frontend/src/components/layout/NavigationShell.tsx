"use client";

import React, { useEffect, useRef, useState, type TouchEvent } from "react";
import {
  Check,
  ChevronDown,
  Menu,
  PanelRightClose,
  PanelRightOpen,
  Sparkles,
  SquarePen,
} from "lucide-react";
import type {
  ChatFolder,
  ConversationSummary,
  ThinkingLevel,
  WorkspaceTab,
} from "@/types/gemuse";
import { ConversationSidebar } from "@/components/chat/ConversationSidebar";
import { triggerHaptic } from "@/lib/haptics";
import { ThemeToggle } from "./ThemeToggle";

const THINKING_OPTIONS: Array<{
  id: ThinkingLevel;
  label: string;
  hint: string;
}> = [
  { id: "auto", label: "Auto", hint: "質問の複雑度に応じて自動切替" },
  { id: "low", label: "Low", hint: "軽量・高速レスポンス" },
  { id: "medium", label: "Medium", hint: "日常タスク向けバランス" },
  { id: "high", label: "High", hint: "深い推論・ツール連携" },
  { id: "xhigh", label: "X-High", hint: "徹底熟考・複雑調査向け" },
];

const TAB_TITLE_MAP: Record<WorkspaceTab, string> = {
  chat: "チャット",
  dashboard: "統合ダッシュボード",
  memory: "記憶・Gemini連携",
  browser: "ブラウザ監視",
  settings: "設定",
};

interface NavigationShellProps {
  activeTab: WorkspaceTab;
  onSelectTab: (tab: WorkspaceTab) => void;
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  pinnedConversationIds: string[];
  folders?: ChatFolder[];
  conversationFolders?: Record<string, string>;
  loadingConversations: boolean;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onTogglePinConversation: (id: string) => void;
  onRenameConversation?: (id: string, newTitle: string) => Promise<void> | void;
  onDeleteConversation: (id: string) => void;
  onCreateFolder?: (name: string) => void;
  onRenameFolder?: (folderId: string, newName: string) => void;
  onDeleteFolder?: (folderId: string) => void;
  onMoveConversationToFolder?: (conversationId: string, folderId: string | null) => void;
  onRefreshConversations: () => void;
  thinkingLevel: ThinkingLevel;
  onChangeThinkingLevel: (level: ThinkingLevel) => void;
  backgroundMode: boolean;
  onToggleBackgroundMode: (value: boolean) => void;
  activeBackgroundJobStatus?: string | null;
  showBrowserPane: boolean;
  onToggleBrowserPane: () => void;
  pendingApprovalCount: number;
  browserPaused: boolean;
  onLogout: () => void;
  children: React.ReactNode;
}

const NOOP = () => undefined;

export function NavigationShell({
  activeTab,
  onSelectTab,
  conversations,
  activeConversationId,
  pinnedConversationIds,
  folders = [],
  conversationFolders = {},
  loadingConversations,
  onSelectConversation,
  onNewConversation,
  onTogglePinConversation,
  onRenameConversation = NOOP,
  onDeleteConversation,
  onCreateFolder = NOOP,
  onRenameFolder = NOOP,
  onDeleteFolder = NOOP,
  onMoveConversationToFolder = NOOP,
  onRefreshConversations,
  thinkingLevel,
  onChangeThinkingLevel,
  backgroundMode,
  onToggleBackgroundMode,
  activeBackgroundJobStatus = null,
  showBrowserPane,
  onToggleBrowserPane,
  pendingApprovalCount,
  browserPaused,
  onLogout,
  children,
}: NavigationShellProps) {
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [desktopDrawerOpen, setDesktopDrawerOpen] = useState(true);
  const [modelPopoverOpen, setModelPopoverOpen] = useState(false);

  const popoverContainerRef = useRef<HTMLDivElement>(null);
  const touchStartRef = useRef<{
    x: number;
    y: number;
    startedFromEdge: boolean;
    wasOpen: boolean;
  } | null>(null);

  useEffect(() => {
    if (!modelPopoverOpen) return;

    function handleOutside(e: MouseEvent) {
      const target = e.target as Node | null;
      if (
        popoverContainerRef.current &&
        target &&
        !popoverContainerRef.current.contains(target)
      ) {
        setModelPopoverOpen(false);
      }
    }

    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setModelPopoverOpen(false);
      }
    }

    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleKey);
    };
  }, [modelPopoverOpen]);

  const currentThinkingLabel =
    THINKING_OPTIONS.find((opt) => opt.id === thinkingLevel)?.label || "Auto";

  function handleHamburgerClick() {
    triggerHaptic();
    if (typeof window !== "undefined" && window.innerWidth >= 1024) {
      setDesktopDrawerOpen((prev) => !prev);
    } else {
      setMobileDrawerOpen((prev) => !prev);
    }
  }

  function handleCloseDrawer() {
    triggerHaptic();
    if (typeof window !== "undefined" && window.innerWidth >= 1024) {
      setDesktopDrawerOpen(false);
    } else {
      setMobileDrawerOpen(false);
    }
  }

  function handleTouchStart(e: TouchEvent<HTMLDivElement>) {
    const touch = e.touches[0];
    if (!touch) return;
    touchStartRef.current = {
      x: touch.clientX,
      y: touch.clientY,
      startedFromEdge: touch.clientX <= 40,
      wasOpen: mobileDrawerOpen,
    };
  }

  function handleTouchEnd(e: TouchEvent<HTMLDivElement>) {
    const start = touchStartRef.current;
    touchStartRef.current = null;
    const touch = e.changedTouches[0];
    if (!start || !touch) return;

    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (Math.abs(dx) <= Math.abs(dy) * 1.2) {
      return;
    }

    if (!start.wasOpen && start.startedFromEdge && dx > 50) {
      setMobileDrawerOpen(true);
    } else if (start.wasOpen && dx < -50) {
      setMobileDrawerOpen(false);
    }
  }

  return (
    <div
      data-testid="app-shell"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
      className="flex h-[100dvh] w-full overflow-hidden bg-white dark:bg-[#0f172a]"
    >
      {/* Mobile Backdrop (solid dim without blur haze) */}
      {mobileDrawerOpen ? (
        <div
          data-testid="drawer-backdrop"
          onClick={() => setMobileDrawerOpen(false)}
          className="fixed inset-0 z-40 bg-black/50 transition-opacity lg:hidden"
        />
      ) : null}

      {/* Unified Left Drawer (Mobile Overlay + Collapsible Desktop Sidebar) */}
      <aside
        data-testid="unified-drawer"
        aria-label="サイドナビゲーション"
        className={`fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col border-r border-slate-200 bg-white transition-all duration-200 ease-out dark:border-slate-800 dark:bg-[#0f172a] lg:static lg:z-auto lg:max-w-none lg:shrink-0 ${
          mobileDrawerOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full"
        } ${
          desktopDrawerOpen
            ? "lg:w-72 lg:translate-x-0"
            : "lg:w-0 lg:-translate-x-full lg:overflow-hidden lg:border-r-0"
        }`}
      >
        <ConversationSidebar
          activeTab={activeTab}
          onSelectTab={(tab) => {
            onSelectTab(tab);
            setMobileDrawerOpen(false);
          }}
          conversations={conversations}
          activeConversationId={activeConversationId}
          pinnedConversationIds={pinnedConversationIds}
          folders={folders}
          conversationFolders={conversationFolders}
          loading={loadingConversations}
          pendingApprovalCount={pendingApprovalCount}
          browserPaused={browserPaused}
          onSelectConversation={(id) => {
            onSelectConversation(id);
            setMobileDrawerOpen(false);
          }}
          onNewConversation={() => {
            onNewConversation();
            setMobileDrawerOpen(false);
          }}
          onTogglePin={onTogglePinConversation}
          onRenameConversation={onRenameConversation}
          onDeleteConversation={onDeleteConversation}
          onCreateFolder={onCreateFolder}
          onRenameFolder={onRenameFolder}
          onDeleteFolder={onDeleteFolder}
          onMoveConversationToFolder={onMoveConversationToFolder}
          onRefresh={onRefreshConversations}
          onCloseDrawer={handleCloseDrawer}
          onLogout={onLogout}
        />
      </aside>

      {/* Main Content Column */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-[#0f172a]">
        {/* Fixed Solid Safe-Area Top Header (Zero transparency/blur haze) */}
        <header
          data-testid="top-header"
          className="z-30 shrink-0 border-b border-slate-200 bg-white px-3 pt-[max(0.5rem,env(safe-area-inset-top))] pb-2 dark:border-slate-800 dark:bg-[#0f172a] sm:px-4"
        >
          <div className="flex items-center justify-between gap-2">
            {/* Left + Model/Thinking Dropdown */}
            <div className="flex min-w-0 items-center gap-1.5">
              <button
                type="button"
                onClick={handleHamburgerClick}
                aria-label="メニューを開く"
                aria-expanded={mobileDrawerOpen}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition-transform duration-100 hover:bg-slate-100 active:scale-95 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
              >
                <Menu className="h-5 w-5" strokeWidth={1.75} />
              </button>

              <div ref={popoverContainerRef} className="relative min-w-0">
                <button
                  type="button"
                  onClick={() => {
                    triggerHaptic();
                    setModelPopoverOpen((v) => !v);
                  }}
                  aria-expanded={modelPopoverOpen}
                  aria-haspopup="dialog"
                  className="inline-flex min-h-[44px] max-w-full items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-left transition-transform duration-100 hover:bg-slate-100 active:scale-95 dark:hover:bg-slate-800"
                >
                  <Sparkles
                    className="h-4 w-4 shrink-0 text-emerald-500"
                    strokeWidth={1.75}
                  />
                  <span className="truncate text-xs font-semibold text-slate-900 dark:text-slate-100 sm:text-sm">
                    Gemini 4 Hydrogen · {currentThinkingLabel}
                  </span>
                  {backgroundMode ? (
                    <span className="hidden rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 sm:inline">
                      BG
                    </span>
                  ) : null}
                  {activeTab !== "chat" ? (
                    <span className="hidden rounded-md bg-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300 md:inline">
                      {TAB_TITLE_MAP[activeTab]}
                    </span>
                  ) : null}
                  <ChevronDown
                    className={`h-3.5 w-3.5 shrink-0 text-slate-400 transition-transform ${
                      modelPopoverOpen ? "rotate-180" : ""
                    }`}
                    strokeWidth={1.75}
                  />
                </button>

                {modelPopoverOpen ? (
                  <div
                    role="dialog"
                    aria-label="モデルとThinking設定"
                    data-testid="thinking-popover"
                    className="absolute left-0 top-full z-50 mt-2 w-72 rounded-2xl border border-slate-200 bg-white p-3 shadow-xl dark:border-slate-800 dark:bg-slate-900"
                  >
                    <div className="mb-2 px-1 text-[11px] font-semibold tracking-wide text-slate-400">
                      Thinking レベル
                    </div>
                    <div className="space-y-1">
                      {THINKING_OPTIONS.map((opt) => {
                        const selected = thinkingLevel === opt.id;
                        return (
                          <button
                            key={opt.id}
                            type="button"
                            onClick={() => {
                              triggerHaptic();
                              onChangeThinkingLevel(opt.id);
                              setModelPopoverOpen(false);
                            }}
                            className={`flex min-h-[44px] w-full items-center justify-between rounded-xl px-2.5 py-2 text-left transition-transform duration-100 active:scale-95 ${
                              selected
                                ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                                : "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                            }`}
                          >
                            <div>
                              <div className="text-xs font-semibold">{opt.label}</div>
                              <div className="text-[11px] text-slate-500 dark:text-slate-400">
                                {opt.hint}
                              </div>
                            </div>
                            {selected ? (
                              <Check className="h-4 w-4 shrink-0 text-emerald-500" strokeWidth={2} />
                            ) : null}
                          </button>
                        );
                      })}
                    </div>

                    <div className="mt-2.5 border-t border-slate-200 pt-2.5 dark:border-slate-800">
                      <div className="flex items-center justify-between gap-3 px-1">
                        <div>
                          <div className="text-xs font-semibold text-slate-800 dark:text-slate-200">
                            バックグラウンド実行
                          </div>
                          <div className="text-[11px] text-slate-500 dark:text-slate-400">
                            ARQ非同期キューで長時間処理
                          </div>
                        </div>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={backgroundMode}
                          aria-label="バックグラウンド実行"
                          onClick={() => {
                            triggerHaptic();
                            onToggleBackgroundMode(!backgroundMode);
                          }}
                          className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full transition-colors active:scale-95 ${
                            backgroundMode
                              ? "bg-emerald-600"
                              : "bg-slate-300 dark:bg-slate-700"
                          }`}
                        >
                          <span
                            className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                              backgroundMode ? "translate-x-6" : "translate-x-1"
                            }`}
                          />
                        </button>
                      </div>
                      {activeBackgroundJobStatus ? (
                        <p className="mt-1.5 px-1 text-[11px] text-emerald-600 dark:text-emerald-400">
                          現在のBGジョブ: {activeBackgroundJobStatus}
                        </p>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            {/* Right Fixed Action Buttons */}
            <div className="flex shrink-0 items-center gap-1.5">
              {activeTab === "chat" ? (
                <button
                  type="button"
                  onClick={() => {
                    triggerHaptic();
                    onToggleBrowserPane();
                  }}
                  aria-label={showBrowserPane ? "Live Browserを隠す" : "Live Browserを表示"}
                  title="Live Browser スプリット切替"
                  className={`inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-xl border px-2.5 text-xs font-medium transition-transform duration-100 active:scale-95 ${
                    showBrowserPane
                      ? "border-emerald-500 bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-100 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  {showBrowserPane ? (
                    <PanelRightClose className="h-4 w-4" strokeWidth={1.75} />
                  ) : (
                    <PanelRightOpen className="h-4 w-4" strokeWidth={1.75} />
                  )}
                  <span className="hidden md:inline">Browser</span>
                </button>
              ) : null}

              <button
                type="button"
                onClick={() => {
                  triggerHaptic();
                  onNewConversation();
                }}
                aria-label="新しいチャット"
                title="新しいチャットを開始"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition-transform duration-100 hover:border-emerald-500/50 hover:text-emerald-600 active:scale-95 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200 dark:hover:text-emerald-400"
              >
                <SquarePen className="h-4 w-4" strokeWidth={1.75} />
              </button>

              <ThemeToggle />
            </div>
          </div>
        </header>

        {/* Scrollable Workspace Content */}
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
