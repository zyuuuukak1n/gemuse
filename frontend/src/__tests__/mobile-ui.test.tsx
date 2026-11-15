import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { NavigationShell } from "@/components/layout/NavigationShell";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage, ConversationSummary } from "@/types/gemuse";

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

function renderShell(overrides: Partial<React.ComponentProps<typeof NavigationShell>> = {}) {
  const props: React.ComponentProps<typeof NavigationShell> = {
    activeTab: "chat",
    onSelectTab: vi.fn(),
    conversations: sampleConversations,
    activeConversationId: "conv-1",
    pinnedConversationIds: [],
    loadingConversations: false,
    onSelectConversation: vi.fn(),
    onNewConversation: vi.fn(),
    onTogglePinConversation: vi.fn(),
    onDeleteConversation: vi.fn(),
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
  it("omits bottom 5-tab navigation bar and keeps header controls working", () => {
    const { props } = renderShell();

    expect(screen.queryByLabelText("モバイルナビゲーション")).toBeNull();
    expect(screen.getByTestId("top-header")).toBeInTheDocument();

    const newChatBtn = screen.getByRole("button", { name: "新しいチャット" });
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

  it("filters conversation history, hides status/thinking badges, handles pin/delete, and confirms logout", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { props } = renderShell();

    const historyList = screen.getByTestId("conversation-history-list");
    expect(historyList).not.toHaveTextContent("completed");

    fireEvent.click(screen.getByRole("button", { name: "会話を検索" }));
    const searchInput = screen.getByLabelText("チャット履歴検索入力");
    fireEvent.change(searchInput, { target: { value: "面接" } });

    expect(historyList).toHaveTextContent("採用面接の質問リスト");
    expect(historyList).not.toHaveTextContent("朝のスプリント計画まとめ");

    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストをピン留め" }));
    expect(props.onTogglePinConversation).toHaveBeenCalledWith("conv-2");

    fireEvent.click(screen.getByRole("button", { name: "採用面接の質問リストを削除" }));
    expect(props.onDeleteConversation).toHaveBeenCalledWith("conv-2");

    fireEvent.click(screen.getByRole("button", { name: "ログアウト" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(props.onLogout).toHaveBeenCalledTimes(1);

    confirmSpy.mockRestore();
  });
});

describe("Slim ChatComposer", () => {
  it("renders 1-row composer without thinking pills or background checkbox and submits trimmed message", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined);
    render(<ChatComposer sending={false} onSend={onSend} />);

    const composer = screen.getByTestId("chat-composer");
    expect(composer).toBeInTheDocument();
    expect(screen.queryByText("X-High")).toBeNull();
    expect(screen.queryByLabelText("バックグラウンド実行")).toBeNull();
    expect(screen.getByRole("button", { name: "ファイルを添付" })).toBeInTheDocument();

    const textarea = screen.getByPlaceholderText("Gemuseにメッセージを入力...");
    fireEvent.change(textarea, { target: { value: "  テスト送信  " } });
    fireEvent.click(screen.getByRole("button", { name: "送信" }));

    expect(onSend).toHaveBeenCalledWith("テスト送信", []);
  });
});

describe("MessageBubble Markdown table optimization", () => {
  it("wraps Markdown tables in horizontal scroll container with nowrap headers and wide long cells", () => {
    const msg: ChatMessage = {
      id: "msg-1",
      role: "assistant",
      content: [
        "| 項目 | 短い値 | 詳細説明 |",
        "| --- | --- | --- |",
        "| A | OK | これは24文字を超えるとても長い説明テキストのセルです |",
      ].join("\n"),
      created_at: "2026-11-16T00:00:00Z",
    };

    render(<MessageBubble message={msg} />);

    const scrollWrap = screen.getByTestId("markdown-table-scroll");
    expect(scrollWrap.className).toContain("overflow-x-auto");

    const headers = screen.getAllByRole("columnheader");
    expect(headers[0].className).toContain("whitespace-nowrap");

    const shortCell = screen.getByText("OK").closest("td");
    expect(shortCell?.className).toContain("whitespace-nowrap");

    const longCell = screen
      .getByText("これは24文字を超えるとても長い説明テキストのセルです")
      .closest("td");
    expect(longCell?.className).toContain("min-w-[160px]");
  });
});

