"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  BookOpen,
  Bookmark,
  Check,
  Copy,
  Database,
  FileUp,
  Layers,
  Loader2,
  Save,
  Search,
  Sparkles,
  Wrench,
} from "lucide-react";
import type {
  EpisodeMemoryItem,
  HandoffPackageData,
  SkillMemoryItem,
  TakeoutImportResultData,
} from "@/types/gemuse";

export function buildGeminiBookmarklet(
  domainInput: string,
  ingestTokenInput: string
): string {
  const cleanDomain = (domainInput || "https://gemuse.zyuuuukak1n.dev")
    .trim()
    .replace(/\/+$/, "");
  const cleanToken = (ingestTokenInput || "CHANGE_ME_INGEST_TOKEN").trim();

  const innerCode = `
(async function(){
  var domain=${JSON.stringify(cleanDomain)};
  var token=${JSON.stringify(cleanToken)};
  if(!token || token==="CHANGE_ME_INGEST_TOKEN"){
    token=window.prompt("Gemuse X-Gemuse-Ingest-Token を入力してください:")||"";
  }
  if(!token){return;}
  var turns=[];
  var nodes=document.querySelectorAll("user-query,model-response,[data-message-author-role]");
  nodes.forEach(function(n){
    var tag=(n.tagName||"").toLowerCase();
    var role=n.getAttribute("data-message-author-role")||(tag==="user-query"?"user":"assistant");
    var txt=(n.innerText||"").trim();
    if(txt){turns.push({role:role,content:txt.slice(0,12000)});}
  });
  var payload={
    title:(document.title||"Gemini Chat Import").slice(0,280),
    source_url:location.href,
    messages:turns.length>0?turns.slice(-40):null,
    raw_text:turns.length===0?(document.body.innerText||"").slice(0,50000):null
  };
  try{
    var r=await fetch(domain+"/api/sync/ingest-chat",{
      method:"POST",
      headers:{"Content-Type":"application/json","X-Gemuse-Ingest-Token":token},
      body:JSON.stringify(payload)
    });
    var d=await r.json();
    if(!r.ok){alert("Gemuse取込エラー: "+(d.detail||r.status));return;}
    alert("Gemuseへ取込完了! Episodes: "+d.episodes_saved+", Core更新: "+d.core_updates_applied);
  }catch(e){alert("Gemuse送信失敗: "+e);}
})();
`
    .replace(/\s+/g, " ")
    .trim();

  return `javascript:${innerCode}`;
}

type StudioSubTab = "layers" | "bridge";

export function MemorySyncStudio() {
  const [subTab, setSubTab] = useState<StudioSubTab>("layers");

  // Layer 1 Core Memory
  const [coreContent, setCoreContent] = useState("");
  const [coreSavedContent, setCoreSavedContent] = useState("");
  const [loadingCore, setLoadingCore] = useState(true);
  const [savingCore, setSavingCore] = useState(false);
  const [coreNotice, setCoreNotice] = useState<string | null>(null);

  // Layer 2 Episode Search
  const [episodeQuery, setEpisodeQuery] = useState("");
  const [episodes, setEpisodes] = useState<EpisodeMemoryItem[]>([]);
  const [searchingEpisodes, setSearchingEpisodes] = useState(false);

  // Layer 3 Skills
  const [skills, setSkills] = useState<SkillMemoryItem[]>([]);
  const [loadingSkills, setLoadingSkills] = useState(false);

  // Gemini Bridge Handoff
  const [handoffGoal, setHandoffGoal] = useState("");
  const [handoffResult, setHandoffResult] = useState<HandoffPackageData | null>(null);
  const [generatingHandoff, setGeneratingHandoff] = useState(false);
  const [copiedHandoff, setCopiedHandoff] = useState(false);

  // Bookmarklet Generator
  const [bookmarkletDomain, setBookmarkletDomain] = useState(
    process.env.NEXT_PUBLIC_APP_DOMAIN || "https://gemuse.zyuuuukak1n.dev"
  );
  const [bookmarkletToken, setBookmarkletToken] = useState("");
  const [copiedBookmarklet, setCopiedBookmarklet] = useState(false);

  // Takeout Upload
  const [takeoutFile, setTakeoutFile] = useState<File | null>(null);
  const [takeoutMaxItems, setTakeoutMaxItems] = useState(100);
  const [importingTakeout, setImportingTakeout] = useState(false);
  const [takeoutResult, setTakeoutResult] = useState<TakeoutImportResultData | null>(null);
  const [takeoutError, setTakeoutError] = useState<string | null>(null);

  const loadCoreAndSkills = useCallback(async () => {
    setLoadingCore(true);
    setLoadingSkills(true);
    try {
      const [coreRes, epRes, skillRes] = await Promise.all([
        fetch("/api/proxy/memory/core", { cache: "no-store" }),
        fetch("/api/proxy/memory/episodes?top_k=10", { cache: "no-store" }),
        fetch("/api/proxy/memory/skills", { cache: "no-store" }),
      ]);
      if (coreRes.ok) {
        const coreJson = (await coreRes.json()) as { content: string };
        setCoreContent(coreJson.content || "");
        setCoreSavedContent(coreJson.content || "");
      }
      if (epRes.ok) {
        setEpisodes((await epRes.json()) as EpisodeMemoryItem[]);
      }
      if (skillRes.ok) {
        setSkills((await skillRes.json()) as SkillMemoryItem[]);
      }
    } finally {
      setLoadingCore(false);
      setLoadingSkills(false);
    }
  }, []);

  useEffect(() => {
    void loadCoreAndSkills();
  }, [loadCoreAndSkills]);

  async function handleSaveCore() {
    setSavingCore(true);
    setCoreNotice(null);
    try {
      const res = await fetch("/api/proxy/memory/core", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: coreContent }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as { content: string };
      setCoreContent(data.content);
      setCoreSavedContent(data.content);
      setCoreNotice("Layer 1 コアメモリを保存しました。");
    } catch (err) {
      setCoreNotice(
        `保存エラー: ${err instanceof Error ? err.message : "unknown"}`
      );
    } finally {
      setSavingCore(false);
    }
  }

  async function handleSearchEpisodes(e: React.FormEvent) {
    e.preventDefault();
    setSearchingEpisodes(true);
    try {
      const qs = new URLSearchParams({ top_k: "12" });
      if (episodeQuery.trim()) {
        qs.set("q", episodeQuery.trim());
      }
      const res = await fetch(`/api/proxy/memory/episodes?${qs.toString()}`, {
        cache: "no-store",
      });
      if (res.ok) {
        setEpisodes((await res.json()) as EpisodeMemoryItem[]);
      }
    } finally {
      setSearchingEpisodes(false);
    }
  }

  async function handleGenerateHandoff() {
    setGeneratingHandoff(true);
    try {
      const qs = new URLSearchParams();
      if (handoffGoal.trim()) {
        qs.set("goal", handoffGoal.trim());
      }
      const res = await fetch(`/api/proxy/sync/handoff?${qs.toString()}`, {
        cache: "no-store",
      });
      if (res.ok) {
        setHandoffResult((await res.json()) as HandoffPackageData);
      }
    } finally {
      setGeneratingHandoff(false);
    }
  }

  async function handleTakeoutUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!takeoutFile) return;
    setImportingTakeout(true);
    setTakeoutError(null);
    setTakeoutResult(null);
    try {
      const form = new FormData();
      form.append("file", takeoutFile);
      const res = await fetch(
        `/api/proxy/sync/import-takeout?max_items=${encodeURIComponent(
          String(takeoutMaxItems)
        )}`,
        {
          method: "POST",
          body: form,
        }
      );
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(txt || `HTTP ${res.status}`);
      }
      const data = (await res.json()) as TakeoutImportResultData;
      setTakeoutResult(data);
    } catch (err) {
      setTakeoutError(
        err instanceof Error ? err.message : "Takeoutインポートに失敗しました。"
      );
    } finally {
      setImportingTakeout(false);
    }
  }

  const bookmarkletHref = useMemo(
    () => buildGeminiBookmarklet(bookmarkletDomain, bookmarkletToken),
    [bookmarkletDomain, bookmarkletToken]
  );

  async function copyText(text: string, which: "handoff" | "bookmarklet") {
    try {
      await navigator.clipboard.writeText(text);
      if (which === "handoff") {
        setCopiedHandoff(true);
        setTimeout(() => setCopiedHandoff(false), 1800);
      } else {
        setCopiedBookmarklet(true);
        setTimeout(() => setCopiedBookmarklet(false), 1800);
      }
    } catch {
      // Ignore clipboard failure in headless test environments
    }
  }

  const coreDirty = coreContent !== coreSavedContent;

  return (
    <div className="h-full overflow-y-auto px-4 py-5 md:px-6">
      <div className="mx-auto max-w-6xl space-y-5">
        {/* Top Header + Subtab Switch */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              Memory & Sync Studio
            </h1>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              3層メモリ（Core Markdown / pgvector Episode RAG / Procedural Skills）と公式Geminiワンクリック取込ブリッジ
            </p>
          </div>
          <div className="flex items-center rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900">
            <button
              type="button"
              onClick={() => setSubTab("layers")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                subTab === "layers"
                  ? "bg-emerald-600 text-white"
                  : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              3層メモリ管理
            </button>
            <button
              type="button"
              onClick={() => setSubTab("bridge")}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                subTab === "bridge"
                  ? "bg-emerald-600 text-white"
                  : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
              }`}
            >
              <Sparkles className="h-3.5 w-3.5" />
              公式Geminiブリッジ
            </button>
          </div>
        </div>

        {subTab === "layers" ? (
          <div className="space-y-6">
            {/* Layer 1 Split Markdown Editor */}
            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <BookOpen className="h-4 w-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Layer 1: Core Memory (`core_memory.md`)
                  </h2>
                  {coreDirty && (
                    <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-300">
                      未保存の変更あり
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => void handleSaveCore()}
                  disabled={savingCore || !coreDirty}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {savingCore ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="h-3.5 w-3.5" />
                  )}
                  保存する
                </button>
              </div>

              {coreNotice && (
                <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-300">
                  {coreNotice}
                </div>
              )}

              {loadingCore ? (
                <div className="flex items-center gap-2 py-10 text-xs text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
                  読み込み中...
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <div>
                    <div className="mb-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">
                      Markdown エディタ
                    </div>
                    <textarea
                      value={coreContent}
                      onChange={(e) => setCoreContent(e.target.value)}
                      rows={12}
                      className="w-full rounded-xl border border-slate-200 bg-slate-50 p-3 font-mono text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                    />
                  </div>
                  <div>
                    <div className="mb-1 text-[11px] font-medium text-slate-500 dark:text-slate-400">
                      ライブプレビュー
                    </div>
                    <div className="h-[268px] overflow-y-auto rounded-xl border border-slate-200 bg-slate-50/40 p-3 text-xs leading-relaxed text-slate-800 dark:border-slate-700 dark:bg-slate-950/50 dark:text-slate-200">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {coreContent}
                      </ReactMarkdown>
                    </div>
                  </div>
                </div>
              )}
            </section>

            {/* Layer 2 & Layer 3 Grid */}
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {/* Layer 2 Episodes */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
                <div className="mb-3 flex items-center gap-2">
                  <Database className="h-4 w-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Layer 2: Episode Memory (pgvector RAG)
                  </h2>
                </div>

                <form onSubmit={handleSearchEpisodes} className="mb-3 flex gap-2">
                  <input
                    type="text"
                    value={episodeQuery}
                    onChange={(e) => setEpisodeQuery(e.target.value)}
                    placeholder="キーワードや意味でエピソード検索..."
                    className="flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                  />
                  <button
                    type="submit"
                    disabled={searchingEpisodes}
                    className="inline-flex items-center gap-1 rounded-xl bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500"
                  >
                    <Search className="h-3.5 w-3.5" />
                    検索
                  </button>
                </form>

                <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                  {episodes.length === 0 ? (
                    <div className="rounded-xl border border-dashed border-slate-200 p-5 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                      一致するエピソード記憶はありません。
                    </div>
                  ) : (
                    episodes.map((ep) => (
                      <div
                        key={ep.id}
                        className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 text-xs dark:border-slate-800 dark:bg-slate-950/60"
                      >
                        <div className="flex items-center justify-between gap-2 text-[10px] text-slate-500 dark:text-slate-400">
                          <span className="rounded bg-slate-200 px-1.5 py-0.5 font-medium dark:bg-slate-800">
                            {ep.source_type}
                          </span>
                          <span>
                            重要度 {ep.importance_score.toFixed(2)}
                            {ep.similarity !== null
                              ? ` • 類似度 ${ep.similarity.toFixed(2)}`
                              : ""}
                          </span>
                        </div>
                        <p className="mt-1.5 text-slate-800 dark:text-slate-200">
                          {ep.content}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </section>

              {/* Layer 3 Skills */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
                <div className="mb-3 flex items-center gap-2">
                  <Wrench className="h-4 w-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Layer 3: Procedural Skills ({skills.length})
                  </h2>
                </div>

                {loadingSkills ? (
                  <div className="flex items-center gap-2 py-8 text-xs text-slate-500">
                    <Loader2 className="h-4 w-4 animate-spin text-emerald-500" />
                    スキル読込中...
                  </div>
                ) : skills.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-200 p-5 text-center text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
                    自動抽出された手順スキルはまだありません。
                  </div>
                ) : (
                  <div className="max-h-80 space-y-2.5 overflow-y-auto pr-1">
                    {skills.map((skill) => (
                      <div
                        key={skill.id}
                        className="rounded-xl border border-slate-200/80 bg-slate-50/70 p-3 text-xs dark:border-slate-800 dark:bg-slate-950/60"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold text-slate-900 dark:text-slate-100">
                            {skill.name}
                          </span>
                          <span className="text-[10px] text-slate-500">
                            使用回数: {skill.usage_count}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-slate-600 dark:text-slate-300">
                          {skill.description}
                        </p>
                        {skill.trigger_keywords.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {skill.trigger_keywords.map((kw) => (
                              <span
                                key={kw}
                                className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-700 dark:text-emerald-300"
                              >
                                {kw}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </div>
        ) : (
          /* Official Gemini Bridge Workspace */
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {/* Bookmarklet Generator */}
            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
              <div className="mb-3 flex items-center gap-2">
                <Bookmark className="h-4 w-4 text-emerald-500" />
                <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                  Chrome ワンクリック取込ブックマークレット生成器
                </h2>
              </div>
              <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                公式 Gemini Web UI (`gemini.google.com`) で開いている会話をワンクリックで Gemuse の3層メモリへ自動統合します。
              </p>

              <div className="space-y-3">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-slate-600 dark:text-slate-300">
                    Gemuse バックエンド公開ドメイン
                  </label>
                  <input
                    type="url"
                    value={bookmarkletDomain}
                    onChange={(e) => setBookmarkletDomain(e.target.value)}
                    placeholder="https://gemuse.zyuuuukak1n.dev"
                    aria-label="Gemuse公開ドメイン"
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-slate-600 dark:text-slate-300">
                    Ingest Token (`X-Gemuse-Ingest-Token`)
                  </label>
                  <input
                    type="password"
                    value={bookmarkletToken}
                    onChange={(e) => setBookmarkletToken(e.target.value)}
                    placeholder="未入力時は実行時にプロンプト入力"
                    aria-label="Ingest Token"
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                  />
                </div>

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <a
                    href={bookmarkletHref}
                    onClick={(e) => e.preventDefault()}
                    draggable
                    className="inline-flex cursor-grab items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm hover:bg-emerald-500"
                  >
                    <Bookmark className="h-3.5 w-3.5" />
                    Gemuseへ記憶を取込 (ドラッグ)
                  </a>
                  <button
                    type="button"
                    onClick={() => void copyText(bookmarkletHref, "bookmarklet")}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
                  >
                    {copiedBookmarklet ? (
                      <>
                        <Check className="h-3.5 w-3.5 text-emerald-500" />
                        コピー完了
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5" />
                        コードをコピー
                      </>
                    )}
                  </button>
                </div>

                <textarea
                  readOnly
                  aria-label="生成されたブックマークレット"
                  value={bookmarkletHref}
                  rows={4}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 font-mono text-[11px] text-slate-600 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300"
                />
              </div>
            </section>

            {/* Handoff Prompt + Takeout Importer */}
            <div className="space-y-5">
              {/* Handoff Generator */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
                <div className="mb-2 flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Deep Research 文脈引き継ぎプロンプト生成
                  </h2>
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={handoffGoal}
                    onChange={(e) => setHandoffGoal(e.target.value)}
                    placeholder="公式Geminiに調査・依頼したい目的（任意）"
                    className="flex-1 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-800 outline-none focus:border-emerald-500 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
                  />
                  <button
                    type="button"
                    onClick={() => void handleGenerateHandoff()}
                    disabled={generatingHandoff}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    {generatingHandoff ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      "生成"
                    )}
                  </button>
                </div>

                {handoffResult && (
                  <div className="mt-3 space-y-2">
                    <div className="flex items-center justify-between text-[11px] text-slate-500">
                      <span>
                        参照会話: {handoffResult.conversation_title} (
                        {handoffResult.message_count}件)
                      </span>
                      <button
                        type="button"
                        onClick={() =>
                          void copyText(handoffResult.handoff_prompt, "handoff")
                        }
                        className="inline-flex items-center gap-1 text-emerald-600 hover:underline dark:text-emerald-400"
                      >
                        {copiedHandoff ? (
                          <Check className="h-3 w-3" />
                        ) : (
                          <Copy className="h-3 w-3" />
                        )}
                        コピー
                      </button>
                    </div>
                    <textarea
                      readOnly
                      value={handoffResult.handoff_prompt}
                      rows={6}
                      className="w-full rounded-xl border border-slate-200 bg-slate-50 p-2.5 font-mono text-[11px] text-slate-700 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-200"
                    />
                  </div>
                )}
              </section>

              {/* Google Takeout Importer */}
              <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900/70">
                <div className="mb-2 flex items-center gap-2">
                  <FileUp className="h-4 w-4 text-emerald-500" />
                  <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                    Google Takeout 過去履歴一括インポート
                  </h2>
                </div>
                <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
                  Google Takeout の `マイアクティビティ.json` または `.html` をアップロードし、重複排除して Layer 2 に格納します。
                </p>

                <form onSubmit={handleTakeoutUpload} className="space-y-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <input
                      type="file"
                      accept=".json,.html,.htm"
                      onChange={(e) =>
                        setTakeoutFile(e.target.files?.[0] || null)
                      }
                      className="block flex-1 text-xs text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-emerald-50 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-emerald-700 hover:file:bg-emerald-100 dark:text-slate-300 dark:file:bg-emerald-950/60 dark:file:text-emerald-300"
                    />
                    <label className="flex items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
                      上限:
                      <input
                        type="number"
                        min={1}
                        max={1000}
                        value={takeoutMaxItems}
                        onChange={(e) =>
                          setTakeoutMaxItems(Number(e.target.value) || 100)
                        }
                        className="w-20 rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-xs dark:border-slate-700 dark:bg-slate-950"
                      />
                    </label>
                  </div>

                  <button
                    type="submit"
                    disabled={!takeoutFile || importingTakeout}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
                  >
                    {importingTakeout ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <FileUp className="h-3.5 w-3.5" />
                    )}
                    インポート実行
                  </button>
                </form>

                {takeoutError && (
                  <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">
                    {takeoutError}
                  </p>
                )}
                {takeoutResult && (
                  <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800 dark:border-emerald-500/30 dark:bg-emerald-950/30 dark:text-emerald-200">
                    形式: <strong>{takeoutResult.format_detected}</strong> / 解析:{" "}
                    {takeoutResult.parsed_count}件 / 新規保存:{" "}
                    <strong>{takeoutResult.imported_count}件</strong> / 重複スキップ:{" "}
                    {takeoutResult.skipped_duplicates}件
                  </div>
                )}
              </section>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

