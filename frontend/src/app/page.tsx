"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { registerGemuseServiceWorker } from "@/lib/push";
import type { WorkspaceTab } from "@/types/gemuse";
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

function parseTabParam(raw: string | null): WorkspaceTab {
  if (raw && (VALID_TABS as string[]).includes(raw)) {
    return raw as WorkspaceTab;
  }
  return "chat";
}

export default function HomePage() {
  const router = useRouter();
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("chat");
  const [pendingApprovalCount, setPendingApprovalCount] = useState(0);
  const [browserPaused, setBrowserPaused] = useState(false);
  const [browserSessionId, setBrowserSessionId] = useState("default");
  const [handoffPromptToChat, setHandoffPromptToChat] = useState<string | null>(
    null
  );

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setActiveTab(parseTabParam(params.get("tab")));
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
  }, [router]);

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
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-200">
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
      pendingApprovalCount={pendingApprovalCount}
      browserPaused={browserPaused}
      onLogout={() => void handleLogout()}
    >
      {activeTab === "chat" && (
        <ChatWorkspace
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

