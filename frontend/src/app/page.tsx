"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { registerGemuseServiceWorker } from "@/lib/push";
import type { ConversationSummary, ThinkingLevel, WorkspaceTab } from "@/types/gemuse";
import { NavigationShell } from "@/components/layout/NavigationShell";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { LiveBrowserPanel } from "@/components/browser/LiveBrowserPanel";
import { CommandDashboard } from "@/components/dashboard/CommandDashboard";
import { MemorySyncStudio } from "@/components/memory/MemorySyncStudio";
import { ConnectorsSettings } from "@/components/settings/ConnectorsSettings";

const VALID_TABS: WorkspaceTab[] = [
  "chat",
  "browser",
  "dashboard",
  "memory",
  "settings",
];

const PINNED_STORAGE_KEY = "gemuse_pinned_conversations";
const DELETED_STORAGE_KEY = "gemuse_deleted_conversations";

function parseTabParam(raw: string | null): WorkspaceTab {
  if (raw && (VALID_TABS as string[]).includes(raw)) {
    return raw as WorkspaceTab;
  }
  return "chat";
}

function readStoredIds(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export default function HomePage() {
  const router = useRouter();
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("chat");
  const [pendingApprovalCount, setPendingApprovalCount] = useState(0);
  const [browserPaused, setBrowserPaused] = useState(false);
  const [browserSessionId, setBrowserSessionId] = useState("default");
  const [showBrowserPane, setShowBrowserPane] = useState(false);
  const [handoffPromptToChat, setHandoffPromptToChat] = useState<string | null>(null);

  // Unified Chat & Model State
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>("auto");
  const [backgroundMode, setBackgroundMode] = useState(false);
  const [activeBackgroundJobStatus, setActiveBackgroundJobStatus] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [newChatTrigger, setNewChatTrigger] = useState(0);
  const [pinnedConversationIds, setPinnedConversationIds] = useState<string[]>([]);
  const [deletedConversationIds, setDeletedConversationIds] = useState<string[]>([]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setActiveTab(parseTabParam(params.get("tab")));
    setPinnedConversationIds(readStoredIds(PINNED_STORAGE_KEY));
    setDeletedConversationIds(readStoredIds(DELETED_STORAGE_KEY));
  }, []);

  const fetchConversations = useCallback(async () => {
    setLoadingConversations(true);
    try {
      const res = await fetch("/api/proxy/chat/conversations?limit=30", {
        cache: "no-store",
      });
      if (res.ok) {
        const data = (await res.json()) as {
          items?: ConversationSummary[];
          conversations?: ConversationSummary[];
        };
        setConversations(data.items || data.conversations || []);
      }
    } catch {
      // Ignore transient refresh failures
    } finally {
      setLoadingConversations(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function verifySession() {
      try {
        const res = await fetch("/api/auth/login", { cache: "no-store" });
        if (!res.ok) {
          throw new Error("Auth check failed");
        }
        const data = (await res.json()) as { authenticated?: boolean };
        if (cancelled) return;
        if (!data.authenticated) {
          router.replace("/login");
          return;
        }
        setAuthenticated(true);
        setAuthChecked(true);
        void fetchConversations();
        void registerGemuseServiceWorker().catch(() => undefined);
      } catch {
        if (!cancelled) {
          router.replace("/login");
        }
      }
    }
    void verifySession();
    return () => {
      cancelled = true;
    };
  }, [fetchConversations, router]);

  const handleSelectTab = useCallback((tab: WorkspaceTab) => {
    setActiveTab(tab);
    const url = new URL(window.location.href);
    if (tab === "chat") {
      url.searchParams.delete("tab");
    } else {
      url.searchParams.set("tab", tab);
    }
    window.history.replaceState({}, "", url.toString());
  }, []);

  const handleSelectConversation = useCallback(
    (conversationId: string) => {
      handleSelectTab("chat");
      setActiveConversationId(conversationId);
    },
    [handleSelectTab]
  );

  const handleNewConversation = useCallback(() => {
    handleSelectTab("chat");
    setActiveConversationId(null);
    setNewChatTrigger((c) => c + 1);
  }, [handleSelectTab]);

  const handleTogglePinConversation = useCallback((conversationId: string) => {
    setPinnedConversationIds((prev) => {
      const next = prev.includes(conversationId)
        ? prev.filter((id) => id !== conversationId)
        : [...prev, conversationId];
      try {
        window.localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // Ignore storage errors
      }
      return next;
    });
  }, []);

  const handleDeleteConversation = useCallback(
    (conversationId: string) => {
      setDeletedConversationIds((prev) => {
        if (prev.includes(conversationId)) return prev;
        const next = [...prev, conversationId];
        try {
          window.localStorage.setItem(DELETED_STORAGE_KEY, JSON.stringify(next));
        } catch {
          // Ignore storage errors
        }
        return next;
      });
      setPinnedConversationIds((prev) => {
        if (!prev.includes(conversationId)) return prev;
        const next = prev.filter((id) => id !== conversationId);
        try {
          window.localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(next));
        } catch {
          // Ignore storage errors
        }
        return next;
      });
      if (activeConversationId === conversationId) {
        setActiveConversationId(null);
        setNewChatTrigger((c) => c + 1);
      }
    },
    [activeConversationId]
  );

  const visibleConversations = useMemo(() => {
    if (deletedConversationIds.length === 0) return conversations;
    const deletedSet = new Set(deletedConversationIds);
    return conversations.filter((c) => !deletedSet.has(c.id));
  }, [conversations, deletedConversationIds]);

  const handleLogout = useCallback(async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
    }
  }, [router]);

  const handleStartChatWithPrompt = useCallback(
    (prompt: string) => {
      setHandoffPromptToChat(prompt);
      handleSelectTab("chat");
    },
    [handleSelectTab]
  );

  if (!authChecked || !authenticated) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-slate-950 text-slate-200">
        <div className="flex items-center gap-2.5 text-sm">
          <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
          <span>Gemuse セッションを確認中...</span>
        </div>
      </div>
    );
  }

  return (
    <NavigationShell
      activeTab={activeTab}
      onSelectTab={handleSelectTab}
      conversations={visibleConversations}
      activeConversationId={activeConversationId}
      pinnedConversationIds={pinnedConversationIds}
      loadingConversations={loadingConversations}
      onSelectConversation={handleSelectConversation}
      onNewConversation={handleNewConversation}
      onTogglePinConversation={handleTogglePinConversation}
      onDeleteConversation={handleDeleteConversation}
      onRefreshConversations={() => void fetchConversations()}
      thinkingLevel={thinkingLevel}
      onChangeThinkingLevel={setThinkingLevel}
      backgroundMode={backgroundMode}
      onToggleBackgroundMode={setBackgroundMode}
      activeBackgroundJobStatus={activeBackgroundJobStatus}
      showBrowserPane={showBrowserPane}
      onToggleBrowserPane={() => setShowBrowserPane((v) => !v)}
      pendingApprovalCount={pendingApprovalCount}
      browserPaused={browserPaused}
      onLogout={() => void handleLogout()}
    >
      {activeTab === "chat" && (
        <ChatWorkspace
          activeConversationId={activeConversationId}
          newChatTrigger={newChatTrigger}
          thinkingLevel={thinkingLevel}
          backgroundMode={backgroundMode}
          showBrowserPane={showBrowserPane}
          onChangeShowBrowserPane={setShowBrowserPane}
          onConversationChange={setActiveConversationId}
          onConversationsUpdated={() => void fetchConversations()}
          onBackgroundJobStatusChange={setActiveBackgroundJobStatus}
          initialPrompt={handoffPromptToChat}
          onConsumeInitialPrompt={() => setHandoffPromptToChat(null)}
          onPendingApprovalCountChange={setPendingApprovalCount}
          onBrowserPauseStateChange={setBrowserPaused}
        />
      )}

      {activeTab === "browser" && (
        <div className="h-full overflow-y-auto px-4 py-5 md:px-6">
          <div className="mx-auto max-w-5xl">
            <LiveBrowserPanel
              sessionId={browserSessionId}
              onSessionIdChange={setBrowserSessionId}
              onPauseStateChange={setBrowserPaused}
            />
          </div>
        </div>
      )}

      {activeTab === "dashboard" && (
        <CommandDashboard
          onStartChatWithPrompt={handleStartChatWithPrompt}
          onNavigateTab={handleSelectTab}
        />
      )}

      {activeTab === "memory" && <MemorySyncStudio />}

      {activeTab === "settings" && <ConnectorsSettings />}
    </NavigationShell>
  );
}
