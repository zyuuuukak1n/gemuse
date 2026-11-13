import React from "react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ApprovalCard } from "@/components/chat/ApprovalCard";
import type { ApprovalCardData } from "@/types/gemuse";

const BASE_APPROVAL: ApprovalCardData = {
  approval_id: "11111111-1111-1111-1111-111111111111",
  action_type: "discord_post_message",
  payload: { guild_id: "123", message: "テスト送信" },
  expires_at: "2026-11-14T12:00:00Z",
  one_time_token: "signed-token-abcdef012345",
  decision_status: "pending",
};

describe("ApprovalCard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls resolve and mcp execute when clicking approve", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "approved" }), { status: 200 })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ wrapped_output: "投稿成功" }), { status: 200 })
      );
    vi.stubGlobal("fetch", fetchMock);

    const onUpdated = vi.fn();
    render(<ApprovalCard approval={BASE_APPROVAL} onUpdated={onUpdated} />);

    fireEvent.click(screen.getByRole("button", { name: "承認して実行" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      `/api/proxy/approval/${BASE_APPROVAL.approval_id}/resolve`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          decision: "approved",
          token: BASE_APPROVAL.one_time_token,
        }),
      })
    );

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/proxy/mcp/execute",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          tool_name: "discord_post_message",
          arguments: { guild_id: "123", message: "テスト送信" },
          approval_id: BASE_APPROVAL.approval_id,
        }),
      })
    );

    expect(onUpdated).toHaveBeenCalledWith(
      expect.objectContaining({
        decision_status: "executed",
        execution_output: "投稿成功",
      })
    );
  });

  it("calls only resolve with rejected when clicking reject", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ status: "rejected" }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetchMock);

    const onUpdated = vi.fn();
    render(<ApprovalCard approval={BASE_APPROVAL} onUpdated={onUpdated} />);

    fireEvent.click(screen.getByRole("button", { name: "却下" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      `/api/proxy/approval/${BASE_APPROVAL.approval_id}/resolve`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          decision: "rejected",
          token: BASE_APPROVAL.one_time_token,
        }),
      })
    );
    expect(onUpdated).toHaveBeenCalledWith(
      expect.objectContaining({ decision_status: "rejected" })
    );
  });
});

