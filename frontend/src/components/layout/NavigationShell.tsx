"use client";

import {
  BrainCircuit,
  Globe,
  LayoutDashboard,
  LogOut,
  MessageSquare,
  Settings,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import type { WorkspaceTab } from "@/types/gemuse";
import { ThemeToggle } from "./ThemeToggle";

interface NavigationShellProps {
  activeTab: WorkspaceTab;
  onSelectTab: (tab: WorkspaceTab) => void;
  pendingApprovalCount: number;
  browserPaused: boolean;
  onLogout: () => void;
  children: React.ReactNode;
}

const NAV_ITEMS: Array<{
  id: WorkspaceTab;
  label: string;
  mobileLabel: string;
  icon: typeof MessageSquare;
}> = [
  { id: "chat", label: "チャット", mobileLabel: "チャット", icon: MessageSquare },
  { id: "browser", label: "ブラウザ監視", mobileLabel: "ブラウザ", icon: Globe },
  { id: "dashboard", label: "ダッシュボード", mobileLabel: "統合", icon: LayoutDashboard },
  { id: "memory", label: "記憶・Gemini連携", mobileLabel: "記憶・連携", icon: BrainCircuit },
  { id: "settings", label: "連携設定", mobileLabel: "設定", icon: Settings },
];

export function NavigationShell({
  activeTab,
  onSelectTab,
  pendingApprovalCount,
  browserPaused,
  onLogout,
  children,
}: NavigationShellProps) {
  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-slate-50 dark:bg-[#090d0b] md:flex-row">
      {/* Desktop Rail */}
      <aside className="hidden w-56 shrink-0 flex-col justify-between border-r border-slate-200 bg-white/90 px-3 py-4 dark:border-emerald-950/60 dark:bg-[#0d1310] md:flex">
        <div>
          <div className="mb-6 flex items-center gap-2.5 px-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-500 ring-1 ring-emerald-500/30">
              <Sparkles className="h-4 w-4" strokeWidth={1.75} />
            </div>
            <div>
              <div className="text-base font-semibold tracking-tight text-slate-900 dark:text-white">
                gemuse
              </div>
              <div className="text-[11px] text-emerald-600 dark:text-emerald-400">
                Gemini × Muse
              </div>
            </div>
          </div>

          <nav className="space-y-1" aria-label="メインナビゲーション">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = activeTab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelectTab(item.id)}
                  className={`flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-sm font-medium transition ${
                    active
                      ? "bg-emerald-500/15 text-emerald-700 ring-1 ring-emerald-500/30 dark:text-emerald-300"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-800/60 dark:hover:text-slate-100"
                  }`}
                >
                  <span className="flex items-center gap-2.5">
                    <Icon className="h-4 w-4" strokeWidth={1.75} />
                    <span>{item.label}</span>
                  </span>
                  {item.id === "chat" && pendingApprovalCount > 0 ? (
                    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-amber-500 px-1.5 py-0.5 text-[11px] font-semibold text-slate-950">
                      {pendingApprovalCount}
                    </span>
                  ) : null}
                  {item.id === "browser" && browserPaused ? (
                    <span
                      title="Take Control 一時停止中"
                      className="h-2.5 w-2.5 rounded-full bg-amber-500 ring-2 ring-amber-500/30"
                    />
                  ) : null}
                </button>
              );
            })}
          </nav>
        </div>

        <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-800">
          {pendingApprovalCount > 0 ? (
            <div className="flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              <ShieldAlert className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              <span>承認待ち {pendingApprovalCount} 件</span>
            </div>
          ) : null}
          <div className="flex items-center justify-between gap-2 px-1">
            <ThemeToggle />
            <button
              type="button"
              onClick={onLogout}
              title="ログアウト"
              className="inline-flex items-center gap-1 rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 transition hover:border-rose-500/40 hover:text-rose-600 dark:border-slate-800 dark:text-slate-400 dark:hover:text-rose-400"
            >
              <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
              <span>終了</span>
            </button>
          </div>
        </div>
      </aside>

      {/* Mobile Top Bar */}
      <header className="flex items-center justify-between border-b border-slate-200 bg-white/90 px-4 py-2.5 dark:border-emerald-950/60 dark:bg-[#0d1310] md:hidden">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-500/15 text-emerald-500">
            <Sparkles className="h-4 w-4" strokeWidth={1.75} />
          </div>
          <span className="text-sm font-semibold text-slate-900 dark:text-white">gemuse</span>
          {pendingApprovalCount > 0 ? (
            <span className="rounded-full bg-amber-500 px-2 py-0.5 text-[10px] font-semibold text-slate-950">
              承認 {pendingApprovalCount}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <button
            type="button"
            onClick={onLogout}
            aria-label="ログアウト"
            className="rounded-xl border border-slate-200 p-1.5 text-slate-600 dark:border-slate-800 dark:text-slate-300"
          >
            <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="min-h-0 flex-1 overflow-hidden">{children}</main>

      {/* Mobile Bottom Navigation */}
      <nav
        aria-label="モバイルナビゲーション"
        className="grid grid-cols-5 border-t border-slate-200 bg-white/95 pb-safe dark:border-emerald-950/60 dark:bg-[#0d1310] md:hidden"
      >
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectTab(item.id)}
              className={`relative flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium ${
                active
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-slate-500 dark:text-slate-400"
              }`}
            >
              <Icon className="h-4 w-4" strokeWidth={1.75} />
              <span>{item.mobileLabel}</span>
              {item.id === "chat" && pendingApprovalCount > 0 ? (
                <span className="absolute right-3 top-1 h-2 w-2 rounded-full bg-amber-500" />
              ) : null}
            </button>
          );
        })}
      </nav>
    </div>
  );
}

