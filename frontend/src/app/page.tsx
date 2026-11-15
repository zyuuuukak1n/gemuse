"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { registerGemuseServiceWorker } from "@/lib/push";
import type {
  ChatFolder,
  ConversationSummary,
  ThinkingLevel,
  WorkspaceTab,
} from "@/types/gemuse";
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
const CUSTOM_TITLES_STORAGE_KEY = "gemuse_custom_conversation_titles";
const FOLDERS_STORAGE_KEY = "gemuse_chat_folders";
const CONVERSATION_FOLDERS_STORAGE_KEY = "gemuse_conversation_folders";

function parseTabParam(raw: string | null): WorkspaceTab {
  if (raw && (VALID_TABS as string[]).includes(raw)) {
    return raw as WorkspaceTab;
  }
  return "chat";
}

function readStoredJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeStoredJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage quota/access errors
  }
}

function createFolderId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `folder-${crypto.randomUUID()}`;
  }
  return `folder-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
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

  // Unified Chat, Model & Organization State
  const [thinkingLevel, setThinkingLevel] = useState<ThinkingLevel>("auto");
  const [backgroundMode, setBackgroundMode] = useState(false);
  const [activeBackgroundJobStatus, setActiveBackgroundJobStatus] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [newChatTrigger, setNewChatTrigger] = useState(0);
  const [pinnedConversationIds, setPinnedConversationIds] = useState<string[]>([]);
  const [deletedConversationIds, setDeletedConversationIds] = useState<string[]>([]);
  const [customTitles, setCustomTitles] = useState<Record<string, string>>({});
  const [folders, setFolders] = useState<ChatFolder[]>([]);
  const [conversationFolders, setConversationFolders] = useState<Record<string, string>>({});

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setActiveTab(parseTabParam(params.get("tab")));
    const storedPinned = readStoredJson<unknown>(PINNED_STORAGE_KEY, []);
    setPinnedConversationIds(
      Array.isArray(storedPinned)
        ? storedPinned.filter((v): v is string => typeof v === "string")
        : []
    );
    const storedDeleted = readStoredJson<unknown>(DELETED_STORAGE_KEY, []);
    setDeletedConversationIds(
      Array.isArray(storedDeleted)
        ? storedDeleted.filter((v): v is string => typeof v === "string")
        : []
    );
    const storedTitles = readStoredJson<unknown>(CUSTOM_TITLES_STORAGE_KEY, {});
    if (storedTitles && typeof storedTitles === "object" && !Array.isArray(storedTitles)) {
      setCustomTitles(storedTitles as Record<string, string>);
    }
    const storedFolders = readStoredJson<unknown>(FOLDERS_STORAGE_KEY, []);
    if (Array.isArray(storedFolders)) {
      setFolders(
        storedFolders.filter(
          (f): f is ChatFolder =>
            Boolean(f && typeof f === "object" && typeof f.id === "string" && typeof f.name === "string")
        )
      );
    }
    const storedConvFolders = readStoredJson<unknown>(CONVERSATION_FOLDERS_STORAGE_KEY, {});
    if (storedConvFolders && typeof storedConvFolders === "object" && !Array.isArray(storedConvFolders)) {
      setConversationFolders(storedConvFolders as Record<string, string>);
    }
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
      writeStoredJson(PINNED_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const handleRenameConversation = useCallback(
    async (conversationId: string, rawTitle: string) => {
      const trimmed = rawTitle.trim();
      if (!trimmed) return;

      setConversations((prev) =>
        prev.map((conv) => (conv.id === conversationId ? { ...conv, title: trimmed } : conv))
      );
      setCustomTitles((prev) => {
        const next = { ...prev, [conversationId]: trimmed };
        writeStoredJson(CUSTOM_TITLES_STORAGE_KEY, next);
        return next;
      });

      try {
        await fetch(`/api/proxy/chat/conversations/${encodeURIComponent(conversationId)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: trimmed }),
        });
      } catch {
        // Keep local custom title persistence even if network update fails
      }
    },
    []
  );

  const handleDeleteConversation = useCallback(
    (conversationId: string) => {
      setDeletedConversationIds((prev) => {
        if (prev.includes(conversationId)) return prev;
        const next = [...prev, conversationId];
        writeStoredJson(DELETED_STORAGE_KEY, next);
        return next;
      });
      setPinnedConversationIds((prev) => {
        if (!prev.includes(conversationId)) return prev;
        const next = prev.filter((id) => id !== conversationId);
        writeStoredJson(PINNED_STORAGE_KEY, next);
        return next;
      });
      setConversationFolders((prev) => {
        if (!(conversationId in prev)) return prev;
        const next = { ...prev };
        delete next[conversationId];
        writeStoredJson(CONVERSATION_FOLDERS_STORAGE_KEY, next);
        return next;
      });
      if (activeConversationId === conversationId) {
        setActiveConversationId(null);
        setNewChatTrigger((c) => c + 1);
      }
      void fetch(`/api/proxy/chat/conversations/${encodeURIComponent(conversationId)}`, {
        method: "DELETE",
      }).catch(() => undefined);
    },
    [activeConversationId]
  );

  const handleCreateFolder = useCallback((rawName: string) => {
    const trimmed = rawName.trim();
    if (!trimmed) return;
    setFolders((prev) => {
      const next: ChatFolder[] = [
        ...prev,
        { id: createFolderId(), name: trimmed, created_at: new Date().toISOString() },
      ];
      writeStoredJson(FOLDERS_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const handleRenameFolder = useCallback((folderId: string, rawName: string) => {
    const trimmed = rawName.trim();
    if (!trimmed) return;
    setFolders((prev) => {
      const next = prev.map((f) => (f.id === folderId ? { ...f, name: trimmed } : f));
      writeStoredJson(FOLDERS_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const handleDeleteFolder = useCallback((folderId: string) => {
    setFolders((prev) => {
      const next = prev.filter((f) => f.id !== folderId);
      writeStoredJson(FOLDERS_STORAGE_KEY, next);
      return next;
    });
    // Safely return any conversations inside the deleted folder to uncategorized
    setConversationFolders((prev) => {
      const next: Record<string, string> = {};
      for (const [convId, fId] of Object.entries(prev)) {
        if (fId !== folderId) {
          next[convId] = fId;
        }
      }
      writeStoredJson(CONVERSATION_FOLDERS_STORAGE_KEY, next);
      return next;
    });
  }, []);

  const handleMoveConversationToFolder = useCallback(
    (conversationId: string, folderId: string | null) => {
      setConversationFolders((prev) => {
        const next = { ...prev };
        if (!folderId) {
          delete next[conversationId];
        } else {
          next[conversationId] = folderId;
        }
        writeStoredJson(CONVERSATION_FOLDERS_STORAGE_KEY, next);
        return next;
      });
    },
    []
  );

  const visibleConversations = useMemo(() => {
    const deletedSet = new Set(deletedConversationIds);
    return conversations
      .filter((c) => !deletedSet.has(c.id))
      .map((c) => (customTitles[c.id] ? { ...c, title: customTitles[c.id] } : c));
  }, [conversations, customTitles, deletedConversationIds]);

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
      <div className="flex h-[100dvh] items-center justify-center bg-[#0f172a] text-slate-200">
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
      folders={folders}
      conversationFolders={conversationFolders}
      loadingConversations={loadingConversations}
      onSelectConversation={handleSelectConversation}
      onNewConversation={handleNewConversation}
      onTogglePinConversation={handleTogglePinConversation}
      onRenameConversation={handleRenameConversation}
      onDeleteConversation={handleDeleteConversation}
      onCreateFolder={handleCreateFolder}
      onRenameFolder={handleRenameFolder}
      onDeleteFolder={handleDeleteFolder}
      onMoveConversationToFolder={handleMoveConversationToFolder}
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
        <div className="h-full overflow-y-auto bg-slate-50 p-3 sm:p-4 dark:bg-slate-950">
          <div className="mx-auto h-full max-w-6xl">
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
          onNavigateTab={(t) => handleSelectTab(t)}
        />
      )}

      {activeTab === "memory" && <MemorySyncStudio />}

      {activeTab === "settings" && <ConnectorsSettings />}
    </NavigationShell>
  );
}
