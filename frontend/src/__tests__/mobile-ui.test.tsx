import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { NavigationShell } from "@/components/layout/NavigationShell";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatFolder, ChatMessage, ConversationSummary } from "@/types/gemuse";

const sampleConversations: ConversationSummary[] = [
  {
    id: "conv-1",
    title: "朝のスプリント計画まとめ",
    thinking_level: "auto",
    latest_job_status: "completed",
    latest_job_id: "job-1",
    updated_at: "2026-11-16T00:00:00Z",
    created_at: "2026-11-15T23:00:00Z",
  },
  {
    id: "conv-2",
    title: "採用面接の質問リスト",
    thinking_level: "high",
    latest_job_status: "completed",
    latest_job_id: "job-2",
    updated_at: "2026-11-15T18:00:00Z",
    created_at: "2026-11-15T17:00:00Z",
  },
];

const sampleFolders: ChatFolder[] = [
  { id: "folder-1", name: "就活", created_at: "2026-11-15T12:00:00Z" },
];

function renderShell(overrides: Partial<React.ComponentProps<typeof NavigationShell>> = {}) {
  const props: React.ComponentProps<typeof NavigationShell> = {
    activeTab: "chat",
    onSelectTab: vi.fn(),
    conversations: sampleConversations,
    activeConversationId: "conv-1",
    pinnedConversationIds: [],
    folders: sampleFolders,
    conversationFolders: {},
    loadingConversations: false,
    onSelectConversation: vi.fn(),
    onNewConversation: vi.fn(),
    onTogglePinConversation: vi.fn(),
    onRenameConversation: vi.fn(),
    onDeleteConversation: vi.fn(),
    onCreateFolder: vi.fn(),
    onRenameFolder: vi.fn(),
    onDeleteFolder: vi.fn(),
    onMoveConversationToFolder: vi.fn(),
    onRefreshConversations: vi.fn(),
    thinkingLevel: "auto",
    onChangeThinkingLevel: vi.fn(),
    backgroundMode: false,
    onToggleBackgroundMode: vi.fn(),
    showBrowserPane: false,
    onToggleBrowserPane: vi.fn(),
    pendingApprovalCount: 0,
    browserPaused: false,
    onLogout: vi.fn(),
    children: <div>Main Content</div>,
    ...overrides,
  };

  return { ...render(<NavigationShell {...props} />), props };
}

describe("Mobile PWA NavigationShell & Drawer", () => {
  it("renders opaque solid top header without blur haze and keeps header controls working", () => {
    const { props } = renderShell();

    expect(screen.queryByLabelText("モバイルナビゲーション")).toBeNull();
    const header = screen.getByTestId("top-header");
    expect(header.className).toContain("bg-white");
    expect(header.className).toContain("dark:bg-[#0f172a]");
    expect(header.className).not.toContain("backdrop-blur");
    expect(header.className).not.toContain("bg-white/");

    const drawer = screen.getByTestId("unified-drawer");
    expect(drawer).not.toHaveTextContent("＋ 新しいチャット");
    expect(drawer).toHaveTextContent("新しいチャット");

    const newChatBtn = within(header).getByRole("button", { name: "新しいチャット" });
    fireEvent.click(newChatBtn);
    expect(props.onNewConversation).toHaveBeenCalledTimes(1);

    const modelBtn = screen.getByRole("button", { name: /Gemini 4 Hydrogen/i });
    fireEvent.click(modelBtn);
    expect(screen.getByTestId("thinking-popover")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^High/i }));
    expect(props.onChangeThinkingLevel).toHaveBeenCalledWith("high");

    fireEvent.click(modelBtn);
    const bgSwitch = screen.getByRole("switch", { name: "バックグラウンド実行" });
    fireEvent.click(bgSwitch);
    expect(props.onToggleBackgroundMode).toHaveBeenCalledTimes(1);
  });

  it("opens and closes unified drawer via left-edge touch swipe gestures", () => {
    renderShell();
    const shell = screen.getByTestId("app-shell");
    const drawer = screen.getByTestId("unified-drawer");

    expect(drawer.className).toContain("-translate-x-full");

    fireEvent.touchStart(shell, { touches: [{ clientX: 10, clientY: 200 }] });
    fireEvent.touchEnd(shell, { changedTouches: [{ clientX: 90, clientY: 205 }] });
    expect(drawer.className).toContain("translate-x-0");

    fireEvent.touchStart(shell, { touches: [{ clientX: 180, clientY: 200 }] });
    fireEvent.touchEnd(shell, { changedTouches: [{ clientX: 60, clientY: 202 }] });
    expect(drawer.className).toContain("-translate-x-full");
  });

  it("supports inline chat rename, folder creation/move, pin/delete via menu, and logout confirmation", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { props } = renderShell();

    // Search filters conversation list cleanly
    const historyList = screen.getByTestId("conversation-history-list");
    expect(historyList).not.toHaveTextContent("completed");

    fireEvent.click(screen.getByRole("button", { name: "会話を検索" }));
    fireEvent.change(screen.getByLabelText("チャット履歴検索入力"), {
      target: { value: "面接" },
    });
    expect(historyList).toHaveTextContent("採用面接の質問リスト");
    expect(historyList).not.toHaveTextContent("朝のスプリント計画まとめ");

    // Open menu on conv-2 -> Rename (reject blank, accept trimmed)
    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストのメニュー" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "名前を変更" }));

    const renameInput = screen.getByLabelText("新しいチャット名");
    fireEvent.change(renameInput, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "チャット名を保存" })).toBeDisabled();

    fireEvent.change(renameInput, { target: { value: "  A社 面接対策  " } });
    fireEvent.click(screen.getByRole("button", { name: "チャット名を保存" }));
    expect(props.onRenameConversation).toHaveBeenCalledWith("conv-2", "A社 面接対策");

    // Open menu again -> Move to folder, Pin, Delete
    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストのメニュー" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "就活" }));
    expect(props.onMoveConversationToFolder).toHaveBeenCalledWith("conv-2", "folder-1");

    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストのメニュー" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "採用面接の質問リストをピン留め" }));
    expect(props.onTogglePinConversation).toHaveBeenCalledWith("conv-2");

    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストのメニュー" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "採用面接の質問リストを削除" }));
    expect(props.onDeleteConversation).toHaveBeenCalledWith("conv-2");

    // Create folder
    fireEvent.click(screen.getByRole("button", { name: "新規フォルダを作成" }));
    const folderInput = screen.getByLabelText("新規フォルダ名");
    fireEvent.change(folderInput, { target: { value: "開発" } });
    fireEvent.click(screen.getByRole("button", { name: "フォルダを保存" }));
    expect(props.onCreateFolder).toHaveBeenCalledWith("開発");

    // Collapse/expand folder
    const folderToggle = within(screen.getByTestId("folder-list")).getByRole("button", {
      name: /^就活\s*\d+$/,
    });
    expect(folderToggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(folderToggle);
    expect(folderToggle).toHaveAttribute("aria-expanded", "false");

    // Logout confirmation
    fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(props.onLogout).toHaveBeenCalledTimes(1);

    confirmSpy.mockRestore();
  });
});

describe("Slim ChatComposer (Speech Input & Stop Control)", () => {
  it("submits trimmed text, toggles stop button while sending, and supports SpeechRecognition", () => {
    const startMock = vi.fn();
    const stopMock = vi.fn();
    class FakeSpeechRecognition {
      lang = "ja-JP";
      interimResults = true;
      continuous = true;
      onresult: ((e: unknown) => void) | null = null;
      onerror: ((e: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start = startMock;
      stop = stopMock;
    }

    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeSpeechRecognition;

    const onSend = vi.fn().mockResolvedValue(undefined);
    const onStop = vi.fn();
    const { rerender } = render(
      <ChatComposer sending={false} onSend={onSend} onStop={onStop} />
    );

    const micBtn = screen.getByRole("button", { name: "音声入力を開始" });
    fireEvent.click(micBtn);
    expect(startMock).toHaveBeenCalledTimes(1);

    const textarea = screen.getByPlaceholderText("音声を認識中...");
    fireEvent.change(textarea, { target: { value: "  テスト送信  " } });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));
    expect(onSend).toHaveBeenCalledWith("テスト送信", []);

    rerender(<ChatComposer sending={true} onSend={onSend} onStop={onStop} />);
    const stopBtn = screen.getByRole("button", { name: "停止" });
    fireEvent.click(stopBtn);
    expect(onStop).toHaveBeenCalledTimes(1);

    delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
  });
});

describe("Welcome Screen & MessageBubble Interactions", () => {
  it("renders Gemini-style welcome screen on empty session", () => {
    render(
      <ChatWorkspace
        activeConversationId={null}
        thinkingLevel="auto"
        backgroundMode={false}
        showBrowserPane={false}
        onChangeShowBrowserPane={vi.fn()}
        onConversationChange={vi.fn()}
        onConversationsUpdated={vi.fn()}
      />
    );

    expect(screen.getByText("何かお手伝いできますか？")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "今日の予定と未読メールを確認" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "就活の連絡・締切をまとめて" })).toBeInTheDocument();
  });

  it("renders AI action bar, streaming status, user edit-resend, and scrollable Markdown table", async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: writeTextMock },
      configurable: true,
    });

    const onRegenerate = vi.fn();
    const onEditResend = vi.fn();

    const streamingMsg: ChatMessage = {
      id: "ai-streaming",
      role: "assistant",
      content: "",
      tool_calls: [{ tool_name: "google_calendar_list", status: "started" }],
      is_streaming: true,
      created_at: "2026-11-16T00:00:00Z",
    };

    const { rerender } = render(<MessageBubble message={streamingMsg} />);
    expect(screen.getByTestId("ai-streaming-status")).toHaveTextContent("ツールを実行中...");
    expect(screen.queryByTestId("assistant-action-bar")).toBeNull();

    const completedMsg: ChatMessage = {
      id: "msg-1",
      role: "assistant",
      content: [
        "| 項目 | 短い値 | 詳細説明 |",
        "| --- | --- | --- |",
        "| A | OK | これは24文字を超えるとても長い説明テキストのセルです |",
      ].join("\n"),
      is_streaming: false,
      created_at: "2026-11-16T00:00:00Z",
    };

    rerender(<MessageBubble message={completedMsg} onRegenerate={onRegenerate} />);

    const scrollWrap = screen.getByTestId("markdown-table-scroll");
    expect(scrollWrap.className).toContain("overflow-x-auto");
    expect(screen.getAllByRole("columnheader")[0].className).toContain("whitespace-nowrap");
    expect(screen.getByText("OK").closest("td")?.className).toContain("whitespace-nowrap");
    expect(
      screen.getByText("これは24文字を超えるとても長い説明テキストのセルです").closest("td")?.className
    ).toContain("min-w-[160px]");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "回答をコピー" }));
    });
    expect(writeTextMock).toHaveBeenCalledWith(completedMsg.content);

    fireEvent.click(screen.getByRole("button", { name: "回答を再生成" }));
    expect(onRegenerate).toHaveBeenCalledWith("msg-1");

    const userMsg: ChatMessage = {
      id: "u-1",
      role: "user",
      content: "元の質問です",
      created_at: "2026-11-16T00:00:00Z",
    };
    rerender(
      <MessageBubble
        message={userMsg}
        isLastUserMessage={true}
        onEditResend={onEditResend}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "メッセージを編集して再送信" }));
    fireEvent.change(screen.getByLabelText("メッセージを編集"), {
      target: { value: "  編集した質問です  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存して再送信" }));
    expect(onEditResend).toHaveBeenCalledWith("u-1", "編集した質問です");
  });
});
