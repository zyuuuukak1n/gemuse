"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { triggerHaptic } from "@/lib/haptics";

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(true);

  useEffect(() => {
    const saved = window.localStorage.getItem("gemuse_theme");
    const nextDark = saved ? saved === "dark" : true;
    setIsDark(nextDark);
    document.documentElement.classList.toggle("dark", nextDark);
  }, []);

  function toggleTheme() {
    triggerHaptic();
    const nextDark = !isDark;
    setIsDark(nextDark);
    document.documentElement.classList.toggle("dark", nextDark);
    window.localStorage.setItem("gemuse_theme", nextDark ? "dark" : "light");
  }

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={isDark ? "ライトモードへ切替" : "ダークモードへ切替"}
      className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 rounded-xl border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 transition-transform duration-100 hover:border-emerald-500/40 hover:text-emerald-600 active:scale-95 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-emerald-500/40 dark:hover:text-emerald-400"
    >
      {isDark ? (
        <>
          <Sun className="h-4 w-4" strokeWidth={1.75} />
          <span className="hidden sm:inline">Light</span>
        </>
      ) : (
        <>
          <Moon className="h-4 w-4" strokeWidth={1.75} />
          <span className="hidden sm:inline">Dark</span>
        </>
      )}
    </button>
  );
}
